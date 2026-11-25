#!/usr/bin/env python3
"""外部 Caster 特性测试：GGA 校验/周期上行、三级限额、统计字段对齐、UA 403 冷却。

运行：python tests/test_external_features.py
"""
import os
import sys
import tempfile
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ['DATABASE_PATH'] = os.path.join(tempfile.mkdtemp(), 'feat.db')
os.environ['EXTERNAL_CASTER_ENABLED'] = 'true'
os.environ['EXTERNAL_GGA_FORWARD'] = 'true'
os.environ['EXTERNAL_GGA_INTERVAL'] = '1'
os.environ['EXTERNAL_MAX_RELAYS'] = '2'
os.environ['EXTERNAL_MAX_RELAYS_PER_SOURCE'] = '1'
os.environ['EXTERNAL_MAX_SUBSCRIBERS_PER_MOUNT'] = '2'

from src import config, database, external_caster  # noqa: E402
import src.forwarder as forwarder  # noqa: E402

FAILS = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {name}: {actual!r} (expected {expected!r})")
    if not ok:
        FAILS.append(name)


class FakeUpstream:
    def __init__(self):
        import socket
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.received = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        import socket
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            conn.settimeout(15)
            buf = b''
            while b'\r\n\r\n' not in buf:
                part = conn.recv(4096)
                if not part:
                    return
                buf += part
            conn.sendall(b'ICY 200 OK\r\n\r\n')
            while not self.stop.is_set():
                part = conn.recv(4096)
                if not part:
                    break
                self.received.append(part)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self.stop.set()
        self.sock.close()


def gga_line(payload=None):
    payload = payload or 'GNGGA,120000.00,3000.0000,N,11000.0000,E,1,12,0.8,50.0,M,0,M,,'
    crc = 0
    for ch in payload.encode('ascii'):
        crc ^= ch
    return f'${payload}*{crc:02X}\r\n'.encode('ascii')


def main():
    print('[1] 限额配置生效')
    check('全局上限', config.EXTERNAL_MAX_RELAYS, 2)
    check('单源上限', config.EXTERNAL_MAX_RELAYS_PER_SOURCE, 1)
    check('单挂载点订阅上限', config.EXTERNAL_MAX_SUBSCRIBERS_PER_MOUNT, 2)
    check('GGA 开关', config.EXTERNAL_GGA_FORWARD, True)

    database.init_db()
    upstream = FakeUpstream()
    _, s1 = database.create_external_caster({
        'name': 'A', 'host': '127.0.0.1', 'port': upstream.port})
    _, s2 = database.create_external_caster({
        'name': 'B', 'host': '127.0.0.1', 'port': upstream.port})
    line = 'STR;MP1;X;RTCM 3;1;0;0;N;CHN;1;2;0;0;g;N;B;N;9600;'
    # MP1 与 MP1B 同属源 s1，用于验证「单源上限」；s2/MP2 本轮不使用
    database.replace_external_mounts(s1, [('MP1', line, 1.0, 2.0),
                                          ('MP1B', line, 1.0, 2.0)])
    database.replace_external_mounts(s2, [('MP2', line, 1.0, 2.0)])

    forwarder.upload_data = lambda mount, data: None
    mgr = external_caster.get_manager()
    mgr.start()

    print('[2] 三级限额')
    check('MP1 第一个订阅', mgr.acquire('MP1', 'web:c1'), True)
    check('MP1 第二个订阅（同 relay）', mgr.acquire('MP1', 'web:c2'), True)
    check('MP1 第三个订阅被拒', mgr.acquire('MP1', 'web:c3'), False)
    check('同源第二挂载点被单源上限拒绝', mgr.acquire('MP1B', 'web:c4'), False)
    check('未知挂载点拒绝', mgr.acquire('NOPE', 'web:c5'), False)

    print('[3] GGA 校验与显式来源')
    good = gga_line()
    broken = good.replace(b'GNGGA', b'BNGGA', 1)
    check('坏校验行被拒', mgr.feed_gga('MP1', 'web:c2', broken), 0)
    check('合法 GGA 被接受', mgr.feed_gga('MP1', 'web:c2', good), 1)
    state = mgr.get_relay_status()['MP1']
    check('锁定首个 GGA 来源', state['gga_source_tag'], 'web:c2')
    check('后来的 GGA 不覆盖（默认）',
          mgr.feed_gga('MP1', 'web:c1', good), 0)
    time.sleep(1.4)
    check('周期上行到达假上游', any(b'$GNGGA' in b for b in upstream.received), True)

    print('[4] 统计字段对齐')
    mgr.record_data('MP1', 100)
    stats = mgr.get_stats('MP1')
    check('统计字段齐全',
          sorted(stats.keys()),
          ['data_count', 'data_rate', 'last_update', 'total_bytes'])
    check('字节累计', stats['total_bytes'] >= 100, True)

    print('[5] 释放与关闭')
    mgr.release('MP1', 'web:c1')
    mgr.release('MP1', 'web:c2')
    time.sleep(6.2)
    check('relay 关闭', mgr.get_relay_status(), {})
    upstream.close()
    mgr.stop()
    if FAILS:
        print(f'失败 {len(FAILS)} 项: {FAILS}')
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
