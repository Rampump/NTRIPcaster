#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端冒烟测试

覆盖本轮修复涉及的关键行为，不依赖外部服务，自己起一个临时实例：
  1. /health 可用、未登录访问 /api/* 返回 401
  2. NTRIP 1.0 SOURCE 推流 + 1.0 GET 取流
  3. 取流数据完整（序号连续、无重复、无丢失）
  4. 挂载点统计不重复计数（total_bytes 与实际发送量一致）
  5. Digest 认证被明确拒绝，且 401 里只宣告 Basic
  6. 源站断开后立刻重连，1.5s 延迟清理不会把新连接踢下线
  7. 多客户端并发（默认 60 路）不丢包
  8. 用户改名后原口令仍然可用

用法：
    python tests/test_smoke.py            # 默认参数
    python tests/test_smoke.py -v         # 打印更多过程信息
"""

import argparse
import base64
import json
import os
import shutil
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
NTRIP_PORT = int(os.environ.get('SMOKE_NTRIP_PORT', '21011'))
WEB_PORT = int(os.environ.get('SMOKE_WEB_PORT', '25757'))
MOUNT = 'smoke'
MOUNT_PWD = 'mountpass1'
USER = 'smokeuser'
USER_PWD = 'userpass1'
ADMIN = ('admin', 'admin123')
CHUNK_PAYLOAD = 60          # 每块正文长度
CHUNK = struct.Struct('>I')  # 4 字节序号
VERBOSE = False

_ok_count = 0
_fail_count = 0


def log(msg):
    if VERBOSE:
        print(f"    · {msg}")


def check(name, cond, detail=''):
    global _ok_count, _fail_count
    if cond:
        _ok_count += 1
        print(f"  [PASS] {name}")
    else:
        _fail_count += 1
        print(f"  [FAIL] {name} {detail}")


# ---------------------------------------------------------------- 环境准备

def write_config(tmpdir):
    log_dir = os.path.join(tmpdir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    cfg = f"""[app]
name = SmokeCaster
version = 0.0.1
author = smoke
contact = smoke@example.com
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

[web]
port = {WEB_PORT}
realtime_push_interval = 3

[database]
path = {os.path.join(tmpdir, 'smoke.db')}
pool_size = 10
timeout = 30

[logging]
log_dir = {log_dir}
log_level = WARNING
main_log_file = main.log
ntrip_log_file = ntrip.log
error_log_file = errors.log

[security]
secret_key = smoke-test-secret-key-0123456789abcdef
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
    return path


def start_server(tmpdir, config_path):
    env = dict(os.environ)
    env['NTRIP_CONFIG_FILE'] = config_path
    env['DATABASE_PATH'] = os.path.join(tmpdir, 'smoke.db')
    env['LOG_DIR'] = os.path.join(tmpdir, 'logs')
    env['DATA_DIR'] = tmpdir
    env['NTRIP_PORT'] = str(NTRIP_PORT)
    env['WEB_PORT'] = str(WEB_PORT)
    proc = subprocess.Popen(
        [sys.executable, 'main.py'],
        cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return proc


def http(path, method='GET', body=None, cookies=None, timeout=10):
    """不带系统代理的 HTTP 请求"""
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


def wait_http_ready(proc, deadline=30):
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


def wait_port(port, deadline=15):
    start = time.time()
    while time.time() - start < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------- NTRIP 客户端

def basic(user, pwd):
    return base64.b64encode(f'{user}:{pwd}'.encode()).decode()


def open_source(password=MOUNT_PWD, nmea=False):
    s = socket.create_connection(('127.0.0.1', NTRIP_PORT), timeout=5)
    req = f'SOURCE {password} /{MOUNT}\r\nUser-Agent: SmokeSource\r\n'
    if nmea:
        req += 'Source-Agent: NTRIP SmokeSource/1.0\r\n'
    req += '\r\n'
    s.sendall(req.encode())
    s.settimeout(5)
    resp = s.recv(1024)
    return s, resp


def open_client(user=USER, pwd=USER_PWD, ntrip2=False, auth_line=None):
    s = socket.create_connection(('127.0.0.1', NTRIP_PORT), timeout=5)
    auth = auth_line if auth_line is not None else f'Authorization: Basic {basic(user, pwd)}\r\n'
    if ntrip2:
        req = (f'GET /{MOUNT} HTTP/1.1\r\nHost: 127.0.0.1\r\nNtrip-Version: Ntrip/2.0\r\n'
               f'User-Agent: NTRIP SmokeClient/1.0\r\n{auth}\r\n')
    else:
        req = (f'GET /{MOUNT} HTTP/1.0\r\nUser-Agent: NTRIP SmokeClient/1.0\r\n{auth}\r\n')
    s.sendall(req.encode())
    s.settimeout(5)
    try:
        resp = s.recv(512)
    except socket.timeout:
        resp = b''
    return s, resp


def make_frame(seq):
    return CHUNK.pack(seq) + bytes([seq % 251]) * CHUNK_PAYLOAD


def collect(sock, expected_bytes, timeout=8.0):
    """读取直到达到 expected_bytes 或超时"""
    buf = b''
    deadline = time.time() + timeout
    sock.settimeout(1.0)
    while len(buf) < expected_bytes and time.time() < deadline:
        try:
            data = sock.recv(65536)
            if not data:
                break
            buf += data
        except socket.timeout:
            continue
        except OSError:
            break
    return buf


def parse_seqs(buf):
    seqs = []
    step = CHUNK.size + CHUNK_PAYLOAD
    for off in range(0, len(buf) - step + 1, step):
        seq = CHUNK.unpack_from(buf, off)[0]
        if buf[off + CHUNK.size: off + step] != bytes([seq % 251]) * CHUNK_PAYLOAD:
            return seqs, False
        seqs.append(seq)
    return seqs, True


# ---------------------------------------------------------------- 用例

def case_health_and_auth(cookies):
    status, body = http('/health')
    check('/health 返回 200', status == 200, f'got {status}')
    check('/health 含 status=ok', '"status": "ok"' in body or '"status":"ok"' in body, body[:120])

    status, _ = http('/api/system/stats')  # 无 Cookie
    check('未登录访问 /api/system/stats 返回 401', status == 401, f'got {status}')

    status, body = http('/api/app_info')
    check('/api/app_info 免登录可访问', status == 200, f'got {status}')

    status, body = http('/api/login', 'POST', {'username': ADMIN[0], 'password': ADMIN[1]}, cookies)
    check('管理员登录成功', status == 200 and '"success": true' in body.replace('":true', '": true'), body[:120])


def case_create_resources(cookies):
    status, body = http('/api/users', 'POST', {'username': USER, 'password': USER_PWD}, cookies)
    check('创建 NTRIP 用户', status in (200, 201), f'{status} {body[:120]}')

    status, body = http('/api/mounts', 'POST', {'mount': MOUNT, 'password': MOUNT_PWD, 'username': USER}, cookies)
    check('创建挂载点并绑定用户', status in (200, 201), f'{status} {body[:120]}')


def case_digest_rejected():
    digest_line = (f'Authorization: Digest username="{USER}", realm="NTRIP", nonce="abc", '
                   f'uri="/{MOUNT}", response="deadbeef"\r\n')
    s, resp = open_client(auth_line=digest_line)
    text = resp.decode('utf-8', 'replace')
    check('Digest 请求被拒绝（401）', '401' in text, text[:160])
    check('401 只宣告 Basic', 'WWW-Authenticate: Basic' in text, text[:200])
    check('401 不再宣告 Digest', 'WWW-Authenticate: Digest' not in text, text[:200])
    s.close()


def case_sourcetable():
    s = socket.create_connection(('127.0.0.1', NTRIP_PORT), timeout=5)
    s.sendall(b'GET / HTTP/1.0\r\nUser-Agent: NTRIP Smoke/1.0\r\n\r\n')
    s.settimeout(5)
    buf = b''
    try:
        while len(buf) < 4096:
            data = s.recv(4096)
            if not data:
                break
            buf += data
    except socket.timeout:
        pass
    s.close()
    text = buf.decode('utf-8', 'replace')
    check('SOURCETABLE 响应 200', 'SOURCETABLE 200 OK' in text, text[:100])
    check('SOURCETABLE 含 CAS/NET 行', 'CAS;' in text and 'NET;' in text, text[:200])


def case_stream_and_stats(cookies, n_chunks=200):
    """推流 → 取流 → 校验完整性与统计"""
    src, resp = open_source()
    ok_up = b'ICY 200 OK' in resp
    check('SOURCE 推流认证通过', ok_up, resp[:100])
    if not ok_up:
        src.close()
        return

    time.sleep(0.5)  # 等挂载点上线
    cli, resp = open_client()
    check('GET 取流认证通过', b'ICY 200 OK' in resp, resp[:100])

    expected = n_chunks * (CHUNK.size + CHUNK_PAYLOAD)
    reader = {}
    t = threading.Thread(target=lambda: reader.update(buf=collect(cli, expected, timeout=15)))
    t.start()

    time.sleep(0.3)
    for seq in range(n_chunks):
        src.sendall(make_frame(seq))
        time.sleep(0.004)
    t.join(timeout=20)

    buf = reader.get('buf', b'')
    seqs, payload_ok = parse_seqs(buf)
    check('取流字节数正确', len(buf) == expected, f'期望 {expected}，实际 {len(buf)}')
    check('帧载荷未被破坏', payload_ok)
    check('序号严格连续且无重复', seqs == list(range(n_chunks)),
          f'收到 {len(seqs)} 帧，首尾 {seqs[:2]}..{seqs[-2:]}' if seqs else '无数据')

    # 挂载点统计口径：不能把同一块数据记两次
    # 注意：/api/system/stats 用的是 10s 刷新的缓存，这里要用实时接口
    status, body = http('/api/mounts/online', cookies=cookies)
    total = None
    try:
        data = json.loads(body)
        info = (data.get('online_mounts') or {}).get(MOUNT) or {}
        total = (info.get('statistics') or {}).get('total_bytes')
    except Exception as exc:
        log(f'解析统计失败: {exc}')
    check('挂载点 total_bytes 未重复计数',
          total is not None and expected <= total <= expected + 4 * CHUNK.size,
          f'发送 {expected}，统计 {total}')

    # 转发器计数是实时值（不经过 10s 缓存）
    status, body = http('/api/system/stats', cookies=cookies)
    data = json.loads(body) if body else {}
    fwd = data.get('forwarder', {})
    check('转发器有发送统计', fwd.get('total_bytes_sent', 0) >= expected,
          str(fwd))

    cli.close()
    src.close()
    return expected


def case_reconnect_race(cookies):
    """源站断线后立即重连，延迟清理不应把新连接踢掉"""
    src, resp = open_source()
    if b'ICY 200 OK' not in resp:
        check('重连用例前置推流成功', False, resp[:80])
        return
    src.sendall(make_frame(1))
    time.sleep(0.2)
    src.close()          # 断开，触发 1.5s 延迟清理
    time.sleep(0.2)

    t0 = time.time()
    src2, resp2 = open_source()   # 立刻重连
    reconnect_ms = (time.time() - t0) * 1000
    ok = b'ICY 200 OK' in resp2
    check('源站可立即重连', ok, resp2[:100])
    # 旧实现会在 add_mount_connection 里持 mount_lock join 解析线程（最长 5s）
    check('重连响应无 5 秒级停顿', reconnect_ms < 2000, f'耗时 {reconnect_ms:.0f}ms')
    if ok:
        for seq in range(2, 8):
            src2.sendall(make_frame(seq))
            time.sleep(0.01)

    time.sleep(2.2)      # 等旧连接的延迟清理定时器触发

    status, body = http(f'/api/mount/{MOUNT}/online', cookies=cookies)
    online = False
    try:
        online = json.loads(body).get('online', False)
    except Exception:
        pass
    check('延迟清理未误删新连接（挂载点仍在线）', online, body[:160])

    if online:
        cli, r = open_client()
        time.sleep(0.2)
        # 必须先连上再发数据：新客户端只收"加入之后"的数据，不回放历史
        for seq in range(10, 16):
            src2.sendall(make_frame(seq))
            time.sleep(0.01)
        buf = collect(cli, 6 * (CHUNK.size + CHUNK_PAYLOAD), timeout=6)
        check('重连后新客户端仍能收到数据', len(buf) > 0, f'收到 {len(buf)} 字节')
        cli.close()
    src2.close()


def case_concurrent_clients(cookies, clients=60, chunks=150):
    # 每个连接用独立账号：默认限制是"每用户最多 3 个连接"，
    # 共用同一账号时第 4 条起会被 401 拒绝（这条限制另有专门用例覆盖）。
    users = []
    for i in range(clients):
        name = f'load{i:03d}'
        status, _ = http('/api/users', 'POST', {'username': name, 'password': 'loadpass1'}, cookies)
        if status in (200, 201) or status == 400:   # 400 = 已存在（重复跑时）
            users.append(name)

    src, resp = open_source()
    if b'ICY 200 OK' not in resp:
        # 可能上一轮源站在线，等它被清理
        time.sleep(2.5)
        src, resp = open_source()
    check('并发用例推流建立', b'ICY 200 OK' in resp, resp[:80])
    if b'ICY 200 OK' not in resp:
        src.close()
        return

    time.sleep(0.3)
    expected = chunks * (CHUNK.size + CHUNK_PAYLOAD)
    results = [None] * clients
    socks = []

    def worker(i):
        try:
            s, r = open_client(user=users[i], pwd='loadpass1')
            if b'ICY 200 OK' not in r:
                results[i] = ('no-auth', r[:60])
                s.close()
                return
            buf = collect(s, expected, timeout=25)
            seqs, intact = parse_seqs(buf)
            results[i] = ('ok' if (intact and seqs == list(range(chunks))) else 'bad',
                          f'{len(buf)}B/{len(seqs)}帧')
            s.close()
        except Exception as exc:
            results[i] = ('err', str(exc)[:80])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]
    for t in threads:
        t.start()
    time.sleep(1.0)
    for seq in range(chunks):
        src.sendall(make_frame(seq))
        time.sleep(0.005)
    for t in threads:
        t.join(timeout=30)

    good = sum(1 for r in results if r and r[0] == 'ok')
    bad = [(i, r) for i, r in enumerate(results) if not r or r[0] != 'ok']
    check(f'{clients} 路并发全部收齐且有序', good == clients, f'成功 {good}/{clients}，异常示例 {bad[:3]}')

    status, body = http('/api/system/stats', cookies=cookies)
    dropped = 0
    try:
        dropped = json.loads(body).get('forwarder', {}).get('dropped_bytes', 0)
    except Exception:
        pass
    check('并发期间无丢弃字节', dropped == 0, f'dropped_bytes={dropped}')

    src.close()


def ensure_source(retries=3):
    """确保挂载点有源站在线（取流要求挂载点在线）"""
    for _ in range(retries):
        src, resp = open_source()
        if b'ICY 200 OK' in resp:
            src.sendall(make_frame(0))
            time.sleep(0.3)
            return src
        src.close()
        time.sleep(2.0)
    return None


def case_ntrip2_stream():
    """NTRIP 2.0 取流：裸字节流，不能有 chunked 包装"""
    src = ensure_source()
    check('NTRIP2 用例前置：挂载点在线', src is not None)
    if src is None:
        return

    cli, r = open_client(ntrip2=True)
    head = r.decode('utf-8', 'replace')
    check('NTRIP 2.0 返回 200', '200 OK' in head, head[:120])
    check('NTRIP 2.0 响应带 Ntrip-Version', 'Ntrip-Version' in head, head[:200])
    check('NTRIP 2.0 未使用 chunked', 'chunked' not in head.lower(), head[:200])

    time.sleep(0.2)
    n = 5
    for seq in range(100, 100 + n):
        src.sendall(make_frame(seq))
        time.sleep(0.01)
    buf = collect(cli, n * (CHUNK.size + CHUNK_PAYLOAD), timeout=6)
    seqs, intact = parse_seqs(buf)
    check('NTRIP 2.0 数据为裸流且完整',
          len(buf) == n * (CHUNK.size + CHUNK_PAYLOAD) and intact and seqs == list(range(100, 100 + n)),
          f'{len(buf)}B {seqs[:5]}')
    cli.close()
    src.close()
    time.sleep(0.5)


def case_user_conn_limit(cookies):
    """单用户并发连接数限制（默认 3）"""
    src = ensure_source()
    check('限流用例前置：挂载点在线', src is not None)
    if src is None:
        return

    name = 'limituser'
    http('/api/users', 'POST', {'username': name, 'password': 'limitpass1'}, cookies)
    held = []
    ok = 0
    for _ in range(3):
        s, r = open_client(user=name, pwd='limitpass1')
        if b'ICY 200 OK' in r:
            ok += 1
            held.append(s)
        else:
            s.close()
    check('同一用户前 3 条连接允许', ok == 3, f'成功 {ok}')

    s, r = open_client(user=name, pwd='limitpass1')
    check('第 4 条连接被限流拒绝', b'401' in r, r[:100])
    s.close()
    for s in held:
        try:
            s.close()
        except OSError:
            pass
    src.close()
    time.sleep(1.0)


def case_rename_keeps_password(cookies, db_path):
    """改名不应清掉口令"""
    status, body = http('/api/users', 'POST', {'username': 'renuser', 'password': 'renpass123'}, cookies)
    check('创建改名测试用户', status in (200, 201), f'{status} {body[:100]}')

    status, body = http('/api/users/renuser', 'PUT', {'username': 'renuser2'}, cookies)
    check('用户改名成功', status == 200, f'{status} {body[:120]}')

    # 直接读同库校验旧口令仍有效（不依赖 NTRIP 端口状态）
    os.environ['DATABASE_PATH'] = db_path
    sys.path.insert(0, PROJECT_ROOT)
    from src import database as db
    users = {u[1]: u[2] for u in db.get_all_users()}
    stored = users.get('renuser2')
    check('改名后口令哈希仍可用', stored is not None and db.verify_password(stored, 'renpass123'),
          '旧口令校验失败' if stored else '用户不存在')


# ---------------------------------------------------------------- 主流程

def main():
    global VERBOSE
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--keep', action='store_true', help='保留临时目录')
    args = parser.parse_args()
    VERBOSE = args.verbose

    tmpdir = tempfile.mkdtemp(prefix='ntrip_smoke_')
    config_path = write_config(tmpdir)
    proc = start_server(tmpdir, config_path)
    print(f"临时实例: NTRIP:{NTRIP_PORT} Web:{WEB_PORT} 数据目录:{tmpdir}")

    try:
        if not wait_http_ready(proc):
            print('服务启动超时')
            return 2
        if not wait_port(NTRIP_PORT):
            print('NTRIP 端口未就绪')
            return 2

        cookies = CookieJar()
        print('\n[1] 健康检查与鉴权边界')
        case_health_and_auth(cookies)

        print('\n[2] 创建用户与挂载点')
        case_create_resources(cookies)

        print('\n[3] Digest 认证必须被拒绝')
        case_digest_rejected()

        print('\n[4] SOURCETABLE')
        case_sourcetable()

        print('\n[5] 推流/取流完整性与统计口径')
        case_stream_and_stats(cookies)

        print('\n[6] NTRIP 2.0 取流')
        case_ntrip2_stream()

        print('\n[7] 断线重连竞态')
        case_reconnect_race(cookies)

        print('\n[8] 单用户连接数限制')
        case_user_conn_limit(cookies)

        print('\n[9] 并发客户端')
        case_concurrent_clients(cookies)

        print('\n[10] 用户改名保留口令')
        case_rename_keeps_password(cookies, os.path.join(tmpdir, 'smoke.db'))

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        tail = ''
        try:
            tail = proc.stdout.read().decode('utf-8', 'replace')[-2500:]
        except Exception:
            pass

    print(f"\n结果: {_ok_count} 通过 / {_fail_count} 失败")
    if _fail_count:
        print('--- 服务输出尾部 ---')
        print(tail)
    if args.keep:
        print(f'临时目录保留: {tmpdir}')
    else:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return 1 if _fail_count else 0


if __name__ == '__main__':
    sys.exit(main())
