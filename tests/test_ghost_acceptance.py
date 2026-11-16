#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""幽灵连接修复的现场验收（对应 conn-fix-spec §4 三步）

与 test_connection_prune.py 的区别：那份是纯逻辑单测（FakeSocket 替身），
这份是**真实 socket + 真实服务进程**的现场验收，回答规格书那三个问题：

  验收 1  单次漏删是否消失
          连一条 → 客户端侧 RST 掐断（不发 FIN）→ 等一会儿 → 再连一条。
          第二条必须 200；日志里应能看到 prune 的 WARNING（若有残留）。

  验收 2  残留是否为 0
          连续 20 次「连 → 断」后，/api/connections 里该用户应为 0 条，
          且日志里「已连接」与「已断开」计数相等。

  验收 3  上限是否真的只剩 3
          并发开 4 条，恰好 3 条 200、第 4 条 401，
          且 401 响应里能看到 `User connection limit exceeded (max: 3)`。

用法：
    python tests/test_ghost_acceptance.py
    python tests/test_ghost_acceptance.py -v
"""

import argparse
import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NTRIP_PORT = int(os.environ.get('GHOST_NTRIP_PORT', '21031'))
WEB_PORT = int(os.environ.get('GHOST_WEB_PORT', '25777'))
MOUNT = 'ghost'
MOUNT_PWD = 'mountpass1'
USER = 'ghostuser'
USER_PWD = 'userpass1'
ADMIN = ('admin', 'admin123')
LIMIT = 3                      # 与 config 里 MAX_CONNECTIONS_PER_USER 默认值一致
RST_LINGER = 0                 # SO_LINGER on/0 → close() 发 RST 而不是 FIN

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


# ---------------------------------------------------------------- 环境

def write_config(tmpdir):
    log_dir = os.path.join(tmpdir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    cfg = f"""[app]
name = GhostCaster
version = 0.0.1
author = ghost
contact = ghost@example.com
website = http://example.com

[caster]
country = CHN
latitude = 30.0
longitude = 120.0

[network]
host = 127.0.0.1
max_connections = 2000
buffer_size = 81920
max_buffer_size = 655360

[ntrip]
port = {NTRIP_PORT}
max_connections_per_user = {LIMIT}
connection_idle_prune = 600
connection_reconcile_interval = 10

[web]
port = {WEB_PORT}
realtime_push_interval = 3

[database]
path = {os.path.join(tmpdir, 'ghost.db')}
pool_size = 10
timeout = 30

[logging]
log_dir = {log_dir}
log_level = DEBUG
main_log_file = main.log
ntrip_log_file = ntrip.log
error_log_file = errors.log

[security]
secret_key = ghost-acceptance-secret-key-0123456789ab
session_timeout = 3600

[admin]
username = {ADMIN[0]}
password = {ADMIN[1]}

[data_forwarding]
sender_threads = 4
client_send_queue_size = 200
client_pending_max_bytes = 1048576

[performance]
shutdown_timeout = 8
"""
    path = os.path.join(tmpdir, 'config.ini')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(cfg)
    return path, log_dir


def start_server(tmpdir, config_path):
    env = dict(os.environ)
    env['NTRIP_CONFIG_FILE'] = config_path
    env['DATABASE_PATH'] = os.path.join(tmpdir, 'ghost.db')
    env['LOG_DIR'] = os.path.join(tmpdir, 'logs')
    env['DATA_DIR'] = tmpdir
    env['NTRIP_PORT'] = str(NTRIP_PORT)
    env['WEB_PORT'] = str(WEB_PORT)
    return subprocess.Popen([sys.executable, 'main.py'], cwd=PROJECT_ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def http(path, method='GET', body=None, cookies=None, timeout=10):
    url = f'http://127.0.0.1:{WEB_PORT}{path}'
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(cookies if cookies is not None else CookieJar()),
    )
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')


def wait_ready(proc, deadline=40):
    start = time.time()
    while time.time() - start < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode('utf-8', 'replace') if proc.stdout else ''
            raise RuntimeError(f"服务进程已退出:\n{out}")
        try:
            status, _ = http('/health', timeout=2)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------- NTRIP

def basic(user, pwd):
    return base64.b64encode(f'{user}:{pwd}'.encode()).decode()


def open_source():
    s = socket.create_connection(('127.0.0.1', NTRIP_PORT), timeout=5)
    s.sendall(f'SOURCE {MOUNT_PWD} /{MOUNT}\r\nUser-Agent: GhostSource\r\n\r\n'.encode())
    s.settimeout(5)
    resp = s.recv(1024)
    return s, resp


def push(src, seq, stop):
    """持续推流，让订阅端有数据可收（否则发送线程发现不了断链）"""
    frame = struct.Struct('>I').pack(seq) + bytes([seq % 251]) * 60
    while not stop.is_set():
        try:
            src.sendall(frame)
        except OSError:
            break
        seq += 1
        time.sleep(0.02)


def carousel(src, stop):
    t = threading.Thread(target=push, args=(src, 0, stop), daemon=True)
    t.start()
    return t


def open_client(user=USER, pwd=USER_PWD):
    s = socket.create_connection(('127.0.0.1', NTRIP_PORT), timeout=5)
    req = (f'GET /{MOUNT} HTTP/1.0\r\nUser-Agent: NTRIP GhostClient/1.0\r\n'
           f'Authorization: Basic {basic(user, pwd)}\r\n\r\n')
    s.sendall(req.encode())
    s.settimeout(5)
    try:
        resp = s.recv(1024)
    except socket.timeout:
        resp = b''
    return s, resp


def rst_close(sock):
    """不发 FIN，直接 RST 掐断（模拟现场那种"客户端突然消失"）"""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                        struct.pack('ii', 1, RST_LINGER))
        sock.close()
    except OSError:
        pass


def read_tail(path, n=400):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def grep(lines, pattern):
    rx = re.compile(pattern)
    return [l for l in lines if rx.search(l)]


# ---------------------------------------------------------------- 用例

def setup(cookies):
    print("\n[准备] 建用户 / 建挂载点 / 起源站")
    st, body = http('/api/login', 'POST',
                    {'username': ADMIN[0], 'password': ADMIN[1]}, cookies)
    check('管理员登录', st == 200, f'{st} {body[:100]}')

    st, body = http('/api/users', 'POST', {'username': USER, 'password': USER_PWD}, cookies)
    log(f'user: {st} {body[:80]}')
    st, body = http('/api/mounts', 'POST',
                    {'mount': MOUNT, 'password': MOUNT_PWD, 'username': USER}, cookies)
    log(f'mount: {st} {body[:80]}')

    src, resp = open_source()
    check('源站接入成功', b'ICY 200 OK' in resp, resp[:120])
    stop = threading.Event()
    carousel(src, stop)
    time.sleep(0.6)
    return src, stop


def user_conns(cookies):
    """从 /api/connections 取该用户的实时连接明细（含 prune）"""
    st, body = http(f'/api/connections?username={USER}', cookies=cookies)
    if st != 200:
        return None, body
    try:
        data = json.loads(body)
    except Exception:
        return None, body
    conns = data.get('connections', data.get('data', data))
    if isinstance(conns, dict):
        conns = conns.get(USER, [])
    return conns, body


def case1_single_leak(cookies, log_dir):
    print("\n[验收 1] 单次漏删是否消失（RST 掐断 → 重连）")
    s1, r1 = open_client()
    check('第一条连接 200', b'ICY 200 OK' in r1, r1[:120])
    time.sleep(1.2)

    # 客户端侧 RST 掐断：不发 FIN，服务端靠 prune / MSG_PEEK 才能发现
    rst_close(s1)
    log('已发 RST')
    time.sleep(3.0)

    s2, r2 = open_client()
    check('RST 之后重连仍然 200（不再 401）', b'ICY 200 OK' in r2, r2[:200])
    check('重连没被限流误伤',
          b'connection limit exceeded' not in r2.lower(), r2[:200])
    s2.close()
    time.sleep(1.0)

    tail = read_tail(os.path.join(log_dir, 'ntrip.log'))
    warns = grep(tail, r'清理 \d+ 条残留连接记录|socket 已失效|超过 \d+s$')
    log(f'prune 相关日志 {len(warns)} 条')
    check('没有出现"连接数超限"的拒绝', not grep(tail, r'连接数超限'), 
          [l[:120] for l in grep(tail, r'连接数超限')][:2])


def case2_20_cycles(cookies, log_dir):
    print("\n[验收 2] 20 次「连 → 断」后残留为 0")
    for i in range(20):
        s, resp = open_client()
        if b'ICY 200 OK' not in resp:
            check(f'第 {i+1} 次连接成功', False, resp[:160])
            s.close()
            continue
        time.sleep(0.35)
        if i % 2 == 0:
            rst_close(s)          # 一半用 RST（模拟突然掉线）
        else:
            s.close()             # 一半用正常 FIN
        time.sleep(0.45)

    time.sleep(3.0)
    conns, raw = user_conns(cookies)
    if conns is None:
        check('/api/connections 可访问', False, raw[:200])
        return
    check('20 次连断后该用户残留 0 条', len(conns) == 0,
          json.dumps(conns, ensure_ascii=False)[:300])
    check('连接明细里没有 closed 的僵尸',
          all(not c.get('closed') for c in conns), conns[:2])

    # §2.1：一次断开只记一条日志 —— 「已连接」条数必须等于
    # "Client disconnected" 条数（原来是 remove_client 内一次 +
    #  _keep_connection_alive finally 一次，断开数会翻倍）。
    main_lines = read_tail(os.path.join(log_dir, 'main.log'), n=5000)
    ntrip_lines = read_tail(os.path.join(log_dir, 'ntrip.log'), n=5000)
    connected = len(grep(main_lines, r'开始订阅RTCM数据'))
    disconnected = len(grep(ntrip_lines, r'Client disconnected:'))
    print(f"    · 已连接 {connected} 条 / Client disconnected {disconnected} 条")
    check('一次断开只记一条（连接数 == 断开数）', connected == disconnected,
          f'connected={connected} disconnected={disconnected}')
    check('日志里没有"移除连接未命中"的异常',
          not grep(ntrip_lines, r'移除连接未命中'),
          [l[:100] for l in grep(ntrip_lines, r'移除连接未命中')][:2])


def case3_limit(cookies):
    print("\n[验收 3] 并发 4 条：恰好 3 条 200、第 4 条 401 且带原因文本")
    # 先把可能残留的清干净
    http('/api/connections/prune', 'POST', {}, cookies=cookies)
    time.sleep(0.4)

    barrier = threading.Barrier(4)
    results = [None] * 4

    def one(idx):
        try:
            barrier.wait(timeout=10)
        except Exception:
            pass
        try:
            s, resp = open_client()
            results[idx] = (s, resp)
        except Exception as e:
            results[idx] = (None, repr(e).encode())

    threads = [threading.Thread(target=one, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    oks = [r for r in results if r and b'ICY 200 OK' in (r[1] or b'')]
    denied = [r for r in results if r and b'401' in (r[1] or b'')]
    print(f"    · 放行 {len(oks)} 条，拒绝 {len(denied)} 条")

    check('恰好 3 条放行', len(oks) == LIMIT, len(oks))
    check('恰好 1 条被拒', len(denied) == 1, len(denied))
    if denied:
        text = denied[0][1].decode('utf-8', 'replace')
        print(f"    · 401 响应:\n{text}")
        check('401 响应里能看到上限原因',
              'User connection limit exceeded (max: 3)' in text, text[:400])
        check('401 带 X-Reason 头（NTRIP 1.0 可诊断）',
              'X-Reason:' in text, text[:300])
        check('401 结论行是 SOURCETABLE 401',
              'SOURCETABLE 401 Unauthorized' in text, text[:100])

    # 收尾：关掉放行的连接，计数必须回到 0
    for r in oks:
        try:
            r[0].close()
        except Exception:
            pass
    time.sleep(3.0)
    conns, _ = user_conns(cookies)
    check('收尾后计数回到 0', conns is not None and len(conns) == 0,
          json.dumps(conns, ensure_ascii=False)[:200] if conns else 'n/a')


# ---------------------------------------------------------------- main

def main():
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()
    VERBOSE = args.verbose

    tmpdir = tempfile.mkdtemp(prefix='ghost-')
    config_path, log_dir = write_config(tmpdir)
    print(f"临时目录: {tmpdir}")
    print(f"配置:     {config_path}")
    proc = start_server(tmpdir, config_path)
    cookies = CookieJar()
    src = stop = None
    try:
        if not wait_ready(proc):
            print('\n[FATAL] 服务未在 40s 内就绪')
            return 2
        print('服务已就绪')

        src, stop = setup(cookies)
        case1_single_leak(cookies, log_dir)
        case2_20_cycles(cookies, log_dir)
        case3_limit(cookies)
    finally:
        if stop is not None:
            stop.set()
        if src is not None:
            try:
                src.close()
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        out = b''
        try:
            out = proc.stdout.read() or b''
        except Exception:
            pass
        if _fail and out:
            print('\n--- 服务输出（尾部）---')
            print(out.decode('utf-8', 'replace')[-3000:])

    print(f"\n=== 通过 {_ok} / 失败 {_fail} ===")
    if _fail == 0:
        print(f"验收全部通过。日志保留在: {log_dir}")
    return 0 if _fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
