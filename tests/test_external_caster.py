#!/usr/bin/env python3
"""外部 Caster 端到端测试：拉表 → 融合 → relay → 数据入 forwarder → 释放归号。

运行：python tests/test_external_caster.py
使用隔离临时数据库与本地假上游，不碰真实 2rtk.db。
"""
import os
import sys
import tempfile
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ['DATABASE_PATH'] = os.path.join(tempfile.mkdtemp(), 'external.db')
os.environ['EXTERNAL_CASTER_ENABLED'] = 'true'
os.environ['EXTERNAL_CASTER_UI_ENABLED'] = 'false'
os.environ['EXTERNAL_UA_POOL_ENABLED'] = 'true'

from src import database, external_caster  # noqa: E402
import src.forwarder as forwarder  # noqa: E402

FAILS = []


def check(name, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'} {name}: {actual!r} (expected {expected!r})")
    if not ok:
        FAILS.append(name)


class FakeUpstream:
    def __init__(self):
        self.server = __import__('socket').socket()
        self.server.setsockopt(__import__('socket').SOL_SOCKET,
                               __import__('socket').SO_REUSEADDR, 1)
        self.server.bind(('127.0.0.1', 0))
        self.server.listen(8)
        self.port = self.server.getsockname()[1]
        self.stop = threading.Event()
        self.requests = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        import socket
        while not self.stop.is_set():
            try:
                conn, _ = self.server.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        req = b''
        while b'\r\n\r\n' not in req:
            part = conn.recv(4096)
            if not part:
                return
            req += part
        first = req.split(b'\r\n')[0].decode('ascii', 'replace')
        ua = next((l for l in req.decode('ascii', 'replace').split('\r\n')
                   if l.lower().startswith('user-agent:')), '')
        self.requests.append((first, ua))
        if first.startswith('GET / HTTP'):
            body = (b'STR;MP1;Fake;RTCM 3;1;0;0;N;CHN;1;2;0;0;gen;N;B;N;9600;\r\n'
                    b'ENDSOURCETABLE\r\n')
            conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: '
                         + str(len(body)).encode() + b'\r\n\r\n' + body)
            conn.close()
        elif first.startswith('GET /MP1 HTTP'):
            conn.sendall(b'ICY 200 OK\r\n\r\n')
            for _ in range(12):
                try:
                    conn.sendall(b'\x33\x00\x01\x02\x03\x04\x05\x06\x07\x08')
                except OSError:
                    break
                time.sleep(0.1)
            conn.close()
        else:
            conn.sendall(b'HTTP/1.1 404 Not Found\r\n\r\n')
            conn.close()

    def close(self):
        self.stop.set()
        self.server.close()


def main():
    print('[1] 初始化隔离库与假上游')
    database.init_db()
    upstream = FakeUpstream()
    ok, source_id = database.create_external_caster({
        'name': 'fake', 'host': '127.0.0.1', 'port': upstream.port})
    check('创建外部源', ok, True)
    ok, _ = database.create_external_account({
        'source_id': source_id, 'username': 'u1', 'password_enc': ''})
    check('创建账号', ok, True)

    print('[2] 启动 manager 并强制拉表')
    mgr = external_caster.get_manager()
    mgr.start()
    check('强制刷新', mgr.force_refresh(source_id), True)
    rows = []
    for _ in range(40):
        time.sleep(0.1)
        rows = mgr.get_fused_rows()
        if rows:
            break
    check('融合表挂载点', [r['mount'] for r in rows], ['MP1'])
    check('STR 原样缓存', rows[0]['str_line'].startswith('STR;MP1;'), True)
    check('匿名拉表带 UA', any('User-Agent:' in ua for _, ua in upstream.requests), True)

    print('[3] relay 数据入 forwarder（不能走 add_client）')
    chunks = []
    original = forwarder.upload_data
    forwarder.upload_data = lambda mount, data: chunks.append((mount, len(data)))
    acquired = mgr.acquire('MP1', 'ntrip:test-1')
    check('acquire', acquired, True)
    time.sleep(0.8)
    check('收到数据块', len(chunks) > 0, True)
    check('数据挂载点正确', all(m == 'MP1' for m, _ in chunks), True)
    check('relay 状态', mgr.get_relay_status()['MP1']['state'], 'running')
    check('外部统计有值', mgr.get_stats('MP1')['total_bytes'] > 0, True)
    forwarder.upload_data = original

    print('[4] 释放与账号归还')
    check('release', mgr.release('MP1', 'ntrip:test-1'), True)
    time.sleep(6.2)
    check('relay 已关闭', mgr.get_relay_status(), {})
    check('账号已归还', bool(database.take_account(source_id, 'other:holder')), True)

    mgr.stop()
    upstream.close()
    if FAILS:
        print(f'失败 {len(FAILS)} 项: {FAILS}')
        sys.exit(1)
    print('全部通过')


def test_reload_config_stops_old_fetcher(monkeypatch):
    """reload_config 必须停掉旧 fetcher，不能只清字典留下重复线程。"""
    mgr = external_caster.ExternalCasterManager()
    monkeypatch.setattr(external_caster.config, 'EXTERNAL_CASTER_ENABLED', True)
    monkeypatch.setattr(
        external_caster.database, 'list_external_casters',
        lambda **kwargs: [{'id': 7, 'enabled': 1}])

    def fake_loop(source_id, stop_event):
        stop_event.wait(5)

    monkeypatch.setattr(mgr, '_fetch_loop', fake_loop)
    mgr._start_fetcher(7)
    old_event = mgr._fetch_stop_events[7]
    mgr.reload_config()
    new_event = mgr._fetch_stop_events[7]
    check('旧 fetcher stop_event 已置位', old_event.is_set(), True)
    check('新 fetcher stop_event 未置位', new_event.is_set(), False)
    check('fetcher 字典仍只有一个', len(mgr._fetch_threads), 1)
    mgr.stop()
    check('stop 后 fetcher 清空', mgr._fetch_threads, {})


def test_try_fetch_singleflight(monkeypatch):
    """定时/按需/强制刷新不得并发拉同一张 STR 表。"""
    mgr = external_caster.ExternalCasterManager()
    release = threading.Event()
    calls = []

    def fake_fetch(source, fail_count=0, demand=False):
        calls.append(source['id'])
        release.wait(2)

    monkeypatch.setattr(mgr, '_fetch_once', fake_fetch)
    source = {'id': 9}
    results = []
    t = threading.Thread(target=lambda: results.append(mgr._try_fetch(source)))
    t.start()
    time.sleep(0.05)
    check('并发拉取被单飞拦住', mgr._try_fetch(source), False)
    release.set()
    t.join(timeout=2)
    check('首个拉取成功', results, [True])
    check('上游只被拉一次', calls, [9])


def test_fetch_loop_waits_until_due(monkeypatch):
    """next_fetch_at 未到期时不得拉取；分段等待醒来后必须重新判断时间。"""
    mgr = external_caster.ExternalCasterManager()
    calls = []
    due = time.time() + 2.0  # 2 秒后才到期，跨过 60s 分段边界的逻辑等价路径
    monkeypatch.setattr(
        external_caster.database, 'get_external_caster',
        lambda sid: {'id': sid, 'enabled': 1, 'name': 'x', 'next_fetch_at': due})

    def fake_try_fetch(src, fail_count):
        calls.append(src['id'])
        raise RuntimeError('should not fetch before due')

    monkeypatch.setattr(mgr, '_try_fetch', fake_try_fetch)
    stop = threading.Event()
    # 直接调用循环，1.2 秒后停掉：期间不应发生任何拉取
    t = threading.Thread(target=lambda: mgr._fetch_loop(1, stop), daemon=True)
    t.start()
    time.sleep(1.2)
    stop.set()
    t.join(timeout=2)
    check('未到期不拉取', calls, [])
    check('分段等待期间没有误触发', len(calls), 0)


def test_delete_source_cleans_children():
    """删除外部源必须同时清掉 external_mounts / external_accounts 子表行。

    回归：连接未开启 PRAGMA foreign_keys 时 ON DELETE CASCADE 不生效，
    删除源会留下孤儿挂载点行，导致同一 mount 出现重复缓存。
    """
    db = database
    db.init_db()

    ok, sid = db.create_external_caster({
        'name': 'dup-src', 'host': 'example.test', 'port': 2101})
    check('创建源成功', ok, True)
    db.replace_external_mounts(sid, [('DUP', 'STR;DUP;x', 0, 0)])
    check('挂载点已写入', len(db.get_external_mounts(sid)), 1)

    ok, _ = db.delete_external_caster(sid)
    check('删除源成功', ok, True)
    with db.db_lock, db._connect() as conn:
        mounts = conn.execute(
            "SELECT COUNT(*) FROM external_mounts WHERE source_id = ?",
            (sid,)).fetchone()[0]
        accounts = conn.execute(
            "SELECT COUNT(*) FROM external_accounts WHERE source_id = ?",
            (sid,)).fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    check('删除后无孤儿挂载点', mounts, 0)
    check('删除后无孤儿账号', accounts, 0)
    check('外键已开启', fk, 1)


if __name__ == '__main__':
    main()
