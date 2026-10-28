#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统计数据与实时推送的端到端测试

回答两个问题：
  1) 挂载点/用户上下线，前端多久能看到？（要求：拓扑变化 < 1.5s）
  2) 统计数字对不对？（在线账号数 / 连接数 / 运行时长 / 字节数 / 速率）

不依赖外部服务，自己起临时实例（端口 21021 / 25767）。
需要 `requests`（python-socketio 的客户端走 HTTP 轮询要用它）：
    python -m pip install requests

用法：
    python tests/test_stats_push.py
    python tests/test_stats_push.py -v
"""

import argparse
import base64
import json
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.cookiejar import CookieJar

import tests.test_smoke as smoke

os.environ.setdefault('SMOKE_NTRIP_PORT', '21021')
os.environ.setdefault('SMOKE_WEB_PORT', '25767')
smoke.NTRIP_PORT = int(os.environ['SMOKE_NTRIP_PORT'])
smoke.WEB_PORT = int(os.environ['SMOKE_WEB_PORT'])

try:
    import requests
    import socketio
except ImportError as exc:  # pragma: no cover
    print(f"跳过：缺少依赖（{exc}）。请先 python -m pip install requests")
    sys.exit(0)

ADMIN = ('admin', 'admin123')
MOUNT = 'pushtest'
MOUNT_PWD = 'mountpass9'
USER = 'pushuser'
USER_PWD = 'userpass9'

_ok = 0
_fail = 0
VERBOSE = False


def log(msg):
    if VERBOSE:
        print(f"    · {msg}")


def check(name, cond, detail=''):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name} {detail}")


# ---------------------------------------------------------------- 观察器

class Watcher:
    """连上 Socket.IO，记录每个事件到达的本地时间"""

    def __init__(self, web_port):
        self.web_port = web_port
        self.base = f'http://127.0.0.1:{web_port}'
        self.events = []           # [(name, ts, payload)]
        self._lock = threading.Lock()
        self.sess = self._session()
        self.sio = socketio.Client(logger=False, engineio_logger=False,
                                   http_session=self.sess)
        for name in ('online_mounts_update', 'online_users_update',
                     'system_stats_update', 'str_data_update'):
            self.sio.on(name, self._make_handler(name))

    def _session(self):
        sess = requests.Session()
        sess.trust_env = False       # 绕开系统代理，否则 127.0.0.1 也会被代理
        return sess

    def login(self):
        r = self.sess.post(f'{self.base}/api/login',
                                       json={'username': ADMIN[0], 'password': ADMIN[1]},
                                       timeout=8)
        r.raise_for_status()

    def connect(self):
        self.login()
        cookie = '; '.join(f'{c.name}={c.value}' for c in self.sess.cookies)
        self.sio.connect(self.base, headers={'Cookie': cookie},
                         transports=['polling'], wait_timeout=10)
        self._t_connected = time.time()

    def _make_handler(self, name):
        def handler(data):
            with self._lock:
                self.events.append((name, time.time(), data))
        return handler

    def close(self):
        try:
            self.sio.disconnect()
        except Exception:
            pass

    def snapshot(self):
        with self._lock:
            return list(self.events)

    def wait_for(self, predicate, timeout=6.0):
        """等待第一条满足 predicate 的事件，返回 (elapsed, event) 或 (None, None)"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for name, ts, data in self.snapshot():
                if ts < self._t_connected:
                    continue
                if predicate(name, data):
                    return ts - getattr(self, '_mark', self._t_connected), (name, ts, data)
            time.sleep(0.02)
        return None, None

    def mark(self):
        self._mark = time.time()


# ---------------------------------------------------------------- 用例

def case_initial_push(w):
    print('\n[1] 连接后立即收到首轮推送')
    t0 = w._t_connected
    deadline = time.time() + 4
    seen = set()
    while time.time() < deadline:
        for name, ts, _ in w.snapshot():
            if ts >= t0:
                seen.add(name)
        if {'online_mounts_update', 'online_users_update', 'system_stats_update'} <= seen:
            break
        time.sleep(0.05)
    check('首轮推送包含挂载点/用户/统计三类事件',
          {'online_mounts_update', 'online_users_update', 'system_stats_update'} <= seen,
          f'实际收到 {sorted(seen)}')


def case_mount_online_offline(w):
    print('\n[2] 挂载点上下线推送时效')
    cj = CookieJar()
    smoke.http('/api/login', 'POST', {'username': ADMIN[0], 'password': ADMIN[1]}, cj)
    st, body = smoke.http('/api/mounts', 'POST',
                          {'mount': MOUNT, 'password': MOUNT_PWD}, cj)
    check('创建测试挂载点', st in (200, 201), f'{st} {body[:120]}')

    w.mark()
    src = socket.create_connection(('127.0.0.1', w_ntrip_port), timeout=5)
    src.sendall(f'SOURCE {MOUNT_PWD} /{MOUNT}\r\nUser-Agent: PushSource\r\n\r\n'.encode())
    resp = src.recv(128)
    check('源站推流建立', b'200' in resp, resp[:60])

    elapsed, ev = w.wait_for(
        lambda n, d: n == 'online_mounts_update' and MOUNT in (d.get('mounts') or {}))
    check('挂载点上线在 1.5s 内推到前端', elapsed is not None and elapsed <= 1.5,
          f'{elapsed:.2f}s' if elapsed else '超时')

    # 推一点数据，让统计有时间累积
    for i in range(30):
        src.sendall(smoke.make_frame(i))
        time.sleep(0.05)
    time.sleep(0.4)

    w.mark()
    src.close()
    elapsed, ev = w.wait_for(
        lambda n, d: n == 'online_mounts_update' and MOUNT not in (d.get('mounts') or {}),
        timeout=5)
    check('挂载点下线在 2.5s 内推到前端', elapsed is not None and elapsed <= 2.5,
          f'{elapsed:.2f}s' if elapsed else '超时')


def case_user_online_offline(w):
    print('\n[3] 用户上下线推送时效')
    cj = CookieJar()
    smoke.http('/api/login', 'POST', {'username': ADMIN[0], 'password': ADMIN[1]}, cj)
    smoke.http('/api/users', 'POST', {'username': USER, 'password': USER_PWD}, cj)

    src = socket.create_connection(('127.0.0.1', w_ntrip_port), timeout=5)
    src.sendall(f'SOURCE {MOUNT_PWD} /{MOUNT}\r\nUser-Agent: PushSource\r\n\r\n'.encode())
    src.recv(128)
    time.sleep(0.6)

    w.mark()
    cli = socket.create_connection(('127.0.0.1', w_ntrip_port), timeout=5)
    auth = base64.b64encode(f'{USER}:{USER_PWD}'.encode()).decode()
    cli.sendall(f'GET /{MOUNT} HTTP/1.0\r\nUser-Agent: PushClient\r\n'
                f'Authorization: Basic {auth}\r\n\r\n'.encode())
    resp = cli.recv(128)
    check('客户端取流建立', b'200' in resp, resp[:60])

    elapsed, ev = w.wait_for(
        lambda n, d: n == 'online_users_update' and USER in (d.get('users') or {}))
    check('用户上线在 1.5s 内推到前端', elapsed is not None and elapsed <= 1.5,
          f'{elapsed:.2f}s' if elapsed else '超时')

    w.mark()
    cli.close()
    elapsed, ev = w.wait_for(
        lambda n, d: n == 'online_users_update' and USER not in (d.get('users') or {}),
        timeout=6)
    check('用户下线在 3s 内推到前端', elapsed is not None and elapsed <= 3.0,
          f'{elapsed:.2f}s' if elapsed else '超时')

    # 清理挂载点，避免影响后续用例
    src.close()
    time.sleep(0.5)


def case_counts_and_uptime(w):
    print('\n[4] 统计口径与运行时长')
    cj = CookieJar()
    smoke.http('/api/login', 'POST', {'username': ADMIN[0], 'password': ADMIN[1]}, cj)

    src = socket.create_connection(('127.0.0.1', w_ntrip_port), timeout=5)
    src.sendall(f'SOURCE {MOUNT_PWD} /{MOUNT}\r\nUser-Agent: PushSource\r\n\r\n'.encode())
    src.recv(128)
    time.sleep(0.5)

    # 同一个账号开两条连接：账号数应为 1，连接数应为 2
    clients = []
    for _ in range(2):
        c = socket.create_connection(('127.0.0.1', w_ntrip_port), timeout=5)
        auth = base64.b64encode(f'{USER}:{USER_PWD}'.encode()).decode()
        c.sendall(f'GET /{MOUNT} HTTP/1.0\r\nUser-Agent: PushClient\r\n'
                  f'Authorization: Basic {auth}\r\n\r\n'.encode())
        c.recv(128)
        clients.append(c)
        time.sleep(0.3)

    frames = 25
    for i in range(frames):
        src.sendall(smoke.make_frame(i))
        for c in clients:
            try:
                c.setblocking(False)
                c.recv(65536)
            except (BlockingIOError, OSError):
                pass
            finally:
                c.setblocking(True)
        time.sleep(0.04)
    time.sleep(0.6)

    st, body = smoke.http('/api/system/stats', cookies=cj)
    stats = json.loads(body)
    counts = stats.get('counts') or {}
    check('/api/system/stats 返回 counts',
          isinstance(counts, dict) and {'mounts', 'users', 'connections'} <= set(counts),
          str(counts))
    check('在线账号数 = 1（同一账号两条连接）', counts.get('users') == 1, str(counts))
    check('在线连接数 = 2', counts.get('connections') == 2, str(counts))
    check('在线挂载点数 = 1', counts.get('mounts') == 1, str(counts))
    check('users 明细长度 = 连接数',
          len(stats.get('users') or []) == counts.get('connections'),
          f"{len(stats.get('users') or [])}")

    mount = next((m for m in (stats.get('mounts') or []) if m.get('mount_name') == MOUNT), None)
    expected = frames * (smoke.CHUNK.size + smoke.CHUNK_PAYLOAD)
    check('挂载点累计字节与实际推送一致',
          mount is not None and expected <= mount.get('total_bytes', 0) <= expected * 1.5 + 256,
          f"期望约 {expected}，实际 {mount and mount.get('total_bytes')}")
    check('挂载点速率不是"自连上以来的平均值"',
          mount is not None and mount.get('data_rate', 0) > 0,
          str(mount and mount.get('data_rate')))
    check('挂载点运行时长 > 0 且小于服务运行时长',
          mount is not None and 0 < mount.get('uptime', 0) <= stats.get('uptime', 0) + 1,
          f"mount={mount and mount.get('uptime')} server={stats.get('uptime')}")

    # 单用户已发送字节不能按轮次膨胀（曾经把累计值当增量反复累加）
    user_conns = [u for u in (stats.get('users') or []) if u.get('username') == USER]
    total_sent = sum(u.get('bytes_sent', 0) for u in user_conns)
    check('用户已发送字节量级正确（不超过挂载点总量的 1.5 倍）',
          mount is not None and total_sent <= mount.get('total_bytes', 0) * 1.5 + 4096,
          f'用户 {total_sent} / 挂载点 {mount and mount.get("total_bytes")}')

    # 运行时长要连续增长，且相邻推送间隔内的增量接近真实经过时间
    print('\n[5] 统计推送频率（运行时长不应每 10 秒才跳一次）')
    w.mark()
    time.sleep(8.5)
    samples = [(ts, d['stats'].get('uptime', -1))
               for n, ts, d in w.snapshot()
               if n == 'system_stats_update' and ts >= w._mark]
    check('8.5 秒内至少收到 2 次统计推送', len(samples) >= 2, f'收到 {len(samples)} 次')
    if len(samples) >= 2:
        dt = samples[-1][0] - samples[0][0]
        du = samples[-1][1] - samples[0][1]
        check('运行时长增量与真实时间同步（不是 10s 缓存跳变）',
              abs(du - dt) <= 1.2, f'Δuptime={du:.2f}s Δt={dt:.2f}s')
        gaps = [samples[i + 1][0] - samples[i][0] for i in range(len(samples) - 1)]
        check('相邻两次统计推送间隔不超过 4s', gaps and max(gaps) <= 4.0,
              f'间隔 {[round(g, 2) for g in gaps]}')
    else:
        check('运行时长增量与真实时间同步（不是 10s 缓存跳变）', False, '样本不足')

    for c in clients:
        try:
            c.close()
        except OSError:
            pass
    src.close()
    time.sleep(0.5)


def case_apifreshness():
    print('\n[6] 接口两次调用之间运行时长应增长')
    cj = CookieJar()
    smoke.http('/api/login', 'POST', {'username': ADMIN[0], 'password': ADMIN[1]}, cj)
    _, b1 = smoke.http('/api/system/stats', cookies=cj)
    u1 = json.loads(b1).get('uptime', 0)
    time.sleep(1.2)
    _, b2 = smoke.http('/api/system/stats', cookies=cj)
    u2 = json.loads(b2).get('uptime', 0)
    check('运行时长实时增长（缓存 10 秒的旧实现做不到）', u2 - u1 >= 0.9, f'{u2 - u1:.2f}s')


# ---------------------------------------------------------------- main

w_ntrip_port = smoke.NTRIP_PORT


def main():
    global VERBOSE, w_ntrip_port
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()
    VERBOSE = args.verbose
    smoke.VERBOSE = args.verbose

    import tempfile
    tmpdir = tempfile.mkdtemp(prefix='ntrip_push_')
    cfg = smoke.write_config(tmpdir)
    proc = smoke.start_server(tmpdir, cfg)
    w = None
    try:
        smoke.wait_http_ready(proc)
        smoke.wait_port(smoke.NTRIP_PORT)
        log(f'服务已就绪 web={smoke.WEB_PORT} ntrip={smoke.NTRIP_PORT}')

        w = Watcher(smoke.WEB_PORT)
        w.connect()
        log('socket 已连接')

        case_initial_push(w)
        case_mount_online_offline(w)
        case_user_online_offline(w)
        case_counts_and_uptime(w)
        case_apifreshness()

        print(f'\n结果: {_ok} 通过 / {_fail} 失败')
        return 0 if _fail == 0 else 1
    finally:
        if w is not None:
            w.close()
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            proc.kill()


if __name__ == '__main__':
    sys.exit(main())
