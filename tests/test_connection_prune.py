#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连接记账 / 幽灵连接回归测试

覆盖 2rtk-caster-conn-fix-spec 的四条主线：
  1.1 remove_user_connection 匹配语义（只按 id 精确删、未命中要可见、
      兜底一次最多删一条、不 close 别人的 socket）
  1.2 幽灵记录老化自愈（prune_stale_connections / reconcile）
  1.3 NTRIP 1.0 的 401 带原因文本
  1.4 try_reserve_slot 检查+占位原子，两处鉴权口径统一

运行：python tests/test_connection_prune.py
"""

import os
import sys
import socket
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.connection import ConnectionManager  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


class FakeSocket:
    """可控的 socket 替身：模拟存活 / 对端关闭 / SO_ERROR 异常"""

    def __init__(self, alive=True, error=False):
        self.alive = alive
        self.error = error
        self.closed = False

    def getsockopt(self, level, opt):
        if self.error:
            raise OSError('bad socket')
        return 0

    def recv(self, n, flags=0):
        if not self.alive:
            return b''
        raise BlockingIOError()

    def close(self):
        self.closed = True


def new_mgr():
    return ConnectionManager()


# ---------------------------------------------------------------- 1.1
def test_remove_semantics():
    print("\n[1.1] remove_user_connection 匹配语义")
    m = new_mgr()
    cid1 = m.add_user_connection('u1', 'MNT', '1.1.1.1')
    cid2 = m.add_user_connection('u1', 'MNT', '1.1.1.1')
    cid3 = m.add_user_connection('u1', 'MNT2', '1.1.1.1')
    check('3 条连接计数正确', m.get_user_connection_count('u1') == 3,
          m.get_user_connection_count('u1'))

    # 按 id 删除，只删目标
    check('按 id 删除返回 True', m.remove_user_connection('u1', cid2) is True)
    left = [c['connection_id'] for c in m.online_users['u1']]
    check('只删了目标那条', cid2 not in left and cid1 in left and cid3 in left, left)
    check('计数同步为 2', m.get_user_connection_count('u1') == 2)

    # 陈旧 id：不能误删同用户其他会话，且返回 False
    before = len(m.online_users['u1'])
    check('陈旧 id 返回 False', m.remove_user_connection('u1', 'stale-id') is False)
    check('陈旧 id 不误删', len(m.online_users['u1']) == before)

    # 只给 mount_name：一次最多删一条（最老的）
    oldest = min(m.online_users['u1'], key=lambda c: c['connect_time'])['connection_id']
    ok = m.remove_user_connection('u1', None, 'MNT')
    check('按挂载点兜底只删 1 条', ok and len(m.online_users['u1']) == 1, len(m.online_users.get('u1') or []))
    check('兜底删的是最老的一条', m.online_users['u1'][0]['connection_id'] != oldest)

    # 兜底也不 close 别人的 socket
    sock = FakeSocket()
    m.add_user_connection('u2', 'MNT', '2.2.2.2', client_socket=sock)
    m.remove_user_connection('u2', None, 'MNT')
    check('移除记录不 close socket', sock.closed is False)

    # 不存在用户
    check('不存在用户返回 False', m.remove_user_connection('nobody', 'x') is False)

    # 兜底只吃 MNT，MNT2 的那条必须活着；计数不漂移
    check('兜底不碰别的挂载点', [c['connection_id'] for c in m.online_users['u1']] == [cid3],
          [c['connection_id'] for c in m.online_users.get('u1') or []])
    check('计数不漂移', m.get_user_connection_count('u1') == 1
          and m.user_connection_count.get('u1') == 1)


def test_no_cross_kill():
    print("\n[1.1-b] 禁止跨连接兜底打死健康会话")
    m = new_mgr()
    ids = [m.add_user_connection('u', 'MNT', '1.1.1.1') for _ in range(3)]
    # 模拟调用方漏传 connection_id（forwarder.add_client 的默认值就是 None）
    m.remove_user_connection('u', None, 'MNT')
    survived = [c['connection_id'] for c in m.online_users['u']]
    check('只死一条，另两条存活', len(survived) == 2 and ids[0] not in survived, survived)


# ---------------------------------------------------------------- 1.2
def _inject(m, username, mount, sock=None):
    """直接往表里塞一条记录，绕过 try_reserve_slot 的自动 prune

    try_reserve_slot 会先清掉本用户的失效记录，所以用 add_user_connection
    连续塞 dead socket 是塞不进去的——构造幽灵记录必须直接落表。
    """
    cid = f"{username}_{mount}_{time.time_ns()}"
    m.online_users.setdefault(username, []).append(
        m._new_connection_record(username, mount, '1.1.1.1', '', '1.0', sock, cid))
    return cid


def test_prune_dead_socket():
    print("\n[1.2] prune：socket 已失效的记录自动清理")
    m = new_mgr()
    dead = FakeSocket(alive=False)
    live = FakeSocket(alive=True)
    _inject(m, 'u', 'MNT', dead)
    _inject(m, 'u', 'MNT', live)

    n = m.prune_stale_connections()
    check('清掉 1 条', n == 1, n)
    check('剩余 1 条', m.get_user_connection_count('u') == 1, m.get_user_connection_count('u'))
    remaining = m.online_users['u'][0]
    check('存活的那条被保留', remaining['client_socket'] is live)

    # 没有 socket 的（测试/内部调用）不应被误杀
    m2 = new_mgr()
    m2.add_user_connection('u', 'MNT', '1.1.1.1')
    check('无 socket 的记录不按 socket 判死', m2.prune_stale_connections() == 0)


def test_prune_idle_timeout():
    print("\n[1.2] prune：空闲超时的记录自动清理")
    m = new_mgr()
    m.add_user_connection('u', 'MNT', '1.1.1.1')
    # 手动把 last_activity 拨回去
    m.online_users['u'][0]['last_activity'] = time.time() - 10000
    n = m.prune_stale_connections(timeout=1800, check_socket=False)
    check('空闲超时被清理', n == 1, n)
    check('用户记录已清空', 'u' not in m.online_users)

    m2 = new_mgr()
    m2.add_user_connection('u', 'MNT', '1.1.1.1')
    n2 = m2.prune_stale_connections(timeout=1800, check_socket=False)
    check('刚连上的记录不被误杀', n2 == 0)


def test_prune_self_heal():
    """规格书验收 1：20 次连断后计数必须为 0"""
    print("\n[1.2-c] 自愈：20 次「连接后漏删」全部被兜住")
    m = new_mgr()
    for _ in range(20):
        # 模拟断开路径漏删（既不 remove 也不通知）——记录留在表里，socket 已死
        _inject(m, 'joverking', '2RTK', FakeSocket(alive=False))
    check('20 条幽灵都在表里', m.get_user_connection_count('joverking') == 20,
          m.get_user_connection_count('joverking'))
    n = m.prune_stale_connections()
    check('20 条全部清理', n == 20, n)
    check('计数归零', m.get_user_connection_count('joverking') == 0,
          m.get_user_connection_count('joverking'))
    check('用户已从表里消失', 'joverking' not in m.online_users)


def test_count_derived():
    print("\n[2.3] 计数从 online_users 派生，不漂移")
    m = new_mgr()
    m.add_user_connection('u', 'MNT', '1.1.1.1')
    # 人为制造漂移：把缓存改成错的
    m.user_connection_count['u'] = 99
    m.mount_connection_count['MNT'] = 99
    check('get_user_connection_count 以记录为准', m.get_user_connection_count('u') == 1)
    check('get_mount_connection_count 以记录为准', m.get_mount_connection_count('MNT') == 1)
    # 下一次写操作会把缓存纠正回来
    m.remove_user_connection('u', m.online_users['u'][0]['connection_id'])
    check('写后缓存已被纠正', m.user_connection_count.get('u', 0) == 0)


def test_reconcile():
    print("\n[1.2] reconcile：与转发器对账")
    m = new_mgr()
    fake_fwd = types.SimpleNamespace(client_lock=threading.Lock(), clients={})
    import src.forwarder as fwd_mod
    orig = fwd_mod.get_forwarder
    fwd_mod.get_forwarder = lambda: fake_fwd
    try:
        cid = m.add_user_connection('u', 'MNT', '1.1.1.1')
        m.online_users['u'][0]['connect_time'] = time.time() - 10  # 超过 2 秒宽限
        result = m.reconcile()
        check('记账里有、forwarder 里没有 → 记为孤儿',
              any(cid[-6:] in o for o in result['orphans']), result['orphans'])
    finally:
        fwd_mod.get_forwarder = orig


# ---------------------------------------------------------------- 1.4
def test_reserve_slot():
    print("\n[1.4] try_reserve_slot：检查+占位原子")
    m = new_mgr()
    results = []
    lock = threading.Lock()

    def worker():
        ok, msg, cid = m.try_reserve_slot('u', 'MNT', '1.1.1.1', limit=3)
        with lock:
            results.append((ok, msg, cid))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    granted = [r for r in results if r[0]]
    denied = [r for r in results if not r[0]]
    check('并发 10 条只放行 3 条', len(granted) == 3, len(granted))
    check('其余 7 条被拒', len(denied) == 7, len(denied))
    check('真实计数 = 3', m.get_user_connection_count('u') == 3)
    check('拒绝原因带上限文本',
          all('connection limit exceeded' in r[1] for r in denied),
          [r[1] for r in denied][:2])
    check('放行的 id 互不相同', len({r[2] for r in granted}) == 3)

    # limit=0 表示不限
    m2 = new_mgr()
    for _ in range(10):
        ok, _, _ = m2.try_reserve_slot('u', 'MNT', '1.1.1.1', limit=0)
        if not ok:
            break
    check('limit=0 不限制', m2.get_user_connection_count('u') == 10,
          m2.get_user_connection_count('u'))


def test_reserve_prunes_before_count():
    print("\n[1.2+1.4] 限流前先 prune 幽灵记录")
    m = new_mgr()
    # 3 条幽灵（socket 已死）打满上限
    for _ in range(3):
        m.add_user_connection('u', 'MNT', '1.1.1.1', client_socket=FakeSocket(alive=False))
    ok, msg, cid = m.try_reserve_slot('u', 'MNT', '1.1.1.1', limit=3)
    check('幽灵被清掉后允许新连接', ok is True, msg)
    check('清理后只剩新连接', m.get_user_connection_count('u') == 1,
          m.get_user_connection_count('u'))


# ---------------------------------------------------------------- 1.3
def test_auth_challenge_reason():
    print("\n[1.3] NTRIP 1.0 的 401 带原因文本")
    from src.ntrip import NTRIPHandler

    class Catcher:
        def __init__(self):
            self.buf = b''

        def sendall(self, b):
            self.buf += b

    fake = types.SimpleNamespace(
        ntrip_version="1.0",
        client_socket=Catcher(),
        _send_response=lambda *a, **k: None,
    )
    NTRIPHandler.send_auth_challenge(
        fake, "User connection limit exceeded (max: 3)")
    text = fake.client_socket.buf.decode('utf-8', 'replace')
    check('首行是 401', text.startswith('SOURCETABLE 401 Unauthorized\r\n'), text[:40])
    check('带 X-Reason 头', 'X-Reason: User connection limit exceeded (max: 3)' in text, text)
    check('响应体含原因', text.rstrip().endswith('User connection limit exceeded (max: 3)'), text)
    check('保留 WWW-Authenticate', 'WWW-Authenticate: Basic' in text)
    check('头部与体之间有空行', '\r\n\r\n' in text)

    # 默认消息不留 X-Reason，避免噪音
    fake2 = types.SimpleNamespace(
        ntrip_version="1.0",
        client_socket=Catcher(),
        _send_response=lambda *a, **k: None,
    )
    NTRIPHandler.send_auth_challenge(fake2)
    text2 = fake2.client_socket.buf.decode('utf-8', 'replace')
    check('默认消息不带 X-Reason', 'X-Reason' not in text2, text2)


def test_auth_message_newline_injection():
    print("\n[1.3] 原因文本里的换行不会破坏报文")
    from src.ntrip import NTRIPHandler

    class Catcher:
        def __init__(self):
            self.buf = b''

        def sendall(self, b):
            self.buf += b

    fake = types.SimpleNamespace(ntrip_version="1.0", client_socket=Catcher(),
                                 _send_response=lambda *a, **k: None)
    NTRIPHandler.send_auth_challenge(fake, "bad\r\nInjected: yes")
    text = fake.client_socket.buf.decode('utf-8', 'replace')
    head = text.split('\r\n\r\n')[0]
    lines = head.split('\r\n')
    # 换行被压成空格，所以"注入"只会作为同行文本出现，不能成为独立头部行
    check('没有独立的注入行', not any(l.strip().lower().startswith('injected:') for l in lines),
          lines)
    check('原因被压成单行', 'X-Reason: bad Injected: yes' in lines, lines)
    check('头部行数不变', len(lines) == 4, lines)


def main():
    test_remove_semantics()
    test_no_cross_kill()
    test_prune_dead_socket()
    test_prune_idle_timeout()
    test_prune_self_heal()
    test_count_derived()
    test_reconcile()
    test_reserve_slot()
    test_reserve_prunes_before_count()
    test_auth_challenge_reason()
    test_auth_message_newline_injection()

    print(f"\n=== 通过 {PASS} / 失败 {FAIL} ===")
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
