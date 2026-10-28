#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端→后端→数据库 写入一致性测试

专门验证「页面上点新增/保存之后，值到底有没有按预期写进库」：
  · 用户：新增 / 改口令 / 改名 / 改名同时改口令 / 删除（含挂载点解绑）
  · 挂载点：新增（user_id / username 两种绑定方式）/ 改口令 / 改名 / 换绑定 / 解绑 / 删除
  · 管理员：改口令
  · 接口不回传口令哈希与明文

用法：
    python tests/test_web_crud.py
    python tests/test_web_crud.py -v --keep
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from http.cookiejar import CookieJar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('SMOKE_NTRIP_PORT', '21021')
os.environ.setdefault('SMOKE_WEB_PORT', '25767')

import test_smoke as smoke  # noqa: E402

ADMIN = smoke.ADMIN
_ok = 0
_fail = 0
DB_PATH = None


def check(name, cond, detail=''):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name} {detail}")


def q(sql, args=()):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def user_row(name):
    rows = q("SELECT id, username, password FROM users WHERE username = ?", (name,))
    return rows[0] if rows else None


def mount_row(name):
    rows = q("SELECT id, mount, password, user_id FROM mounts WHERE mount = ?", (name,))
    return rows[0] if rows else None


def verify(stored, plain):
    """用与被测服务同一套算法校验口令哈希"""
    import hashlib
    import hmac
    if not stored:
        return False
    if '$' not in stored:
        return hmac.compare_digest(stored, plain)
    salt, digest = stored.split('$', 1)
    key = hashlib.pbkdf2_hmac('sha256', plain.encode(), salt.encode(), 10000)
    return hmac.compare_digest(key.hex(), digest)


def api(cookies, path, method='GET', body=None):
    status, text = smoke.http(path, method, body, cookies)
    try:
        data = json.loads(text) if text else None
    except json.JSONDecodeError:
        data = None
    return status, data, text


# ------------------------------------------------------------------ 用例

def case_user_crud(cookies):
    print('\n[A] 用户写入')
    status, data, text = api(cookies, '/api/users', 'POST', {'username': 'cruduser', 'password': 'pass1234'})
    check('新增用户返回 201', status == 201, f'{status} {text[:120]}')
    row = user_row('cruduser')
    check('用户已落库', row is not None, '库里查不到')
    check('落库的是哈希而不是明文', row and 'pass1234' not in row['password'], row['password'] if row else '')
    check('哈希可用原口令校验', row and verify(row['password'], 'pass1234'))
    uid = row['id'] if row else None

    status, data, text = api(cookies, '/api/users', 'POST', {'username': 'cruduser', 'password': 'pass1234'})
    check('重复用户名被拒', status == 400, f'{status} {text[:100]}')
    check('重复新增未产生第二行',
          len(q("SELECT id FROM users WHERE username='cruduser'")) == 1)

    status, data, text = api(cookies, '/api/users', 'POST', {'username': ADMIN[0], 'password': 'pass1234'})
    check('不允许创建与管理员同名的用户', status == 400, f'{status} {text[:100]}')
    check('未在 users 表里留下同名行', user_row(ADMIN[0]) is None)

    # 只改口令
    status, data, text = api(cookies, '/api/users/cruduser', 'PUT', {'password': 'newpass99'})
    row = user_row('cruduser')
    check('改口令返回 200', status == 200, f'{status} {text[:120]}')
    check('新口令生效', row and verify(row['password'], 'newpass99'), row['password'] if row else '')
    check('旧口令失效', row and not verify(row['password'], 'pass1234'))
    check('改名接口未篡改用户名', row and row['username'] == 'cruduser')
    check('用户 ID 未变化', row and row['id'] == uid)

    # 只改名
    status, data, text = api(cookies, '/api/users/cruduser', 'PUT', {'username': 'cruduser2'})
    row = user_row('cruduser2')
    check('改名返回 200', status == 200, f'{status} {text[:120]}')
    check('新用户名已落库', row is not None, '库里查不到 cruduser2')
    check('改名后旧记录已消失', user_row('cruduser') is None)
    check('改名后原口令仍可用', row and verify(row['password'], 'newpass99'))

    # 改名 + 改口令（同一次保存，前端编辑框就是这种提交）
    status, data, text = api(cookies, '/api/users/cruduser2',
                             'PUT', {'username': 'cruduser3', 'password': 'bothpass77'})
    row = user_row('cruduser3')
    check('同时改名+改口令返回 200', status == 200, f'{status} {text[:120]}')
    check('同时改名：新名字已落库', row is not None, '库里查不到 cruduser3')
    check('同时改名：新口令已落库', row is not None and verify(row['password'], 'bothpass77'),
          '口令仍为旧值 bothpass77 未写入' if row else '')
    if row:
        print(f"       → 返回消息: {data.get('message') if data else text[:80]}")

    # 不存在的用户
    status, data, text = api(cookies, '/api/users/nosuchuser', 'PUT', {'password': 'whatever1'})
    check('修改不存在用户返回 400', status == 400, f'{status} {text[:100]}')

    # 删除
    status, data, text = api(cookies, '/api/users/cruduser3', 'DELETE')
    check('删除用户返回 200', status == 200, f'{status} {text[:120]}')
    check('用户行已删除', user_row('cruduser3') is None)


def case_mount_crud(cookies):
    print('\n[B] 挂载点写入')
    api(cookies, '/api/users', 'POST', {'username': 'binduser', 'password': 'bindpass1'})
    bind = user_row('binduser')

    status, data, text = api(cookies, '/api/mounts', 'POST',
                             {'mount': 'crudmnt', 'password': 'mpass1234', 'user_id': bind['id']})
    check('新增挂载点返回 201', status == 201, f'{status} {text[:120]}')
    row = mount_row('crudmnt')
    check('挂载点已落库', row is not None)
    check('挂载点口令按哈希存储', row and 'mpass1234' not in row['password'])
    check('挂载点口令可校验', row and verify(row['password'], 'mpass1234'))
    check('user_id 绑定已写入', row and row['user_id'] == bind['id'], row['user_id'] if row else None)

    # 用用户名绑定（PUT 支持，POST 见下方单独用例）
    status, data, text = api(cookies, '/api/mounts', 'POST',
                             {'mount': 'crudmntbyname', 'password': 'mpass1234', 'username': 'binduser'})
    check('POST 支持 username 绑定', status == 201, f'{status} {text[:120]}')
    rowb = mount_row('crudmntbyname')
    check('username 绑定的 user_id 已写入', rowb and rowb['user_id'] == bind['id'],
          f"user_id={rowb['user_id'] if rowb else None}")

    # 只改口令
    status, data, text = api(cookies, '/api/mounts/crudmnt', 'PUT', {'password': 'newmpass9'})
    row = mount_row('crudmnt')
    check('改挂载点口令返回 200', status == 200, f'{status} {text[:120]}')
    check('新挂载点口令生效', row and verify(row['password'], 'newmpass9'))
    check('挂载点名未变', row and row['mount'] == 'crudmnt')
    check('改口令未清掉绑定', row and row['user_id'] == bind['id'],
          f"user_id={row['user_id'] if row else None}")

    # 请求体不带任何绑定字段 → 必须保持原绑定
    status, data, text = api(cookies, '/api/mounts/crudmnt', 'PUT', {'password': 'newmpass8'})
    row = mount_row('crudmnt')
    check('未传绑定字段时保持原绑定', row and row['user_id'] == bind['id'],
          f"user_id={row['user_id'] if row else None}")

    # 改挂载点名
    status, data, text = api(cookies, '/api/mounts/crudmnt', 'PUT', {'mount_name': 'crudmnt2'})
    row = mount_row('crudmnt2')
    check('改挂载点名返回 200', status == 200, f'{status} {text[:120]}')
    check('新挂载点名已落库', row is not None)
    check('旧挂载点名已消失', mount_row('crudmnt') is None)
    check('改名后口令保留', row and verify(row['password'], 'newmpass8'))
    check('改名后绑定保留', row and row['user_id'] == bind['id'])

    # 改名到已存在的名字
    status, data, text = api(cookies, '/api/mounts/crudmnt2', 'PUT', {'mount_name': 'crudmntbyname'})
    check('重名挂载点被拒', status == 400, f'{status} {text[:120]}')

    # 解绑
    status, data, text = api(cookies, '/api/mounts/crudmnt2', 'PUT', {'username': ''})
    row = mount_row('crudmnt2')
    check('清空用户名可解绑', status == 200 and row and row['user_id'] is None,
          f"{status} user_id={row['user_id'] if row else None}")

    # 重新绑定
    status, data, text = api(cookies, '/api/mounts/crudmnt2', 'PUT', {'username': 'binduser'})
    row = mount_row('crudmnt2')
    check('按用户名重新绑定', status == 200 and row and row['user_id'] == bind['id'],
          f"{status} user_id={row['user_id'] if row else None}")

    # 绑定不存在的用户
    status, data, text = api(cookies, '/api/mounts/crudmnt2', 'PUT', {'username': 'ghostuser'})
    row = mount_row('crudmnt2')
    check('绑定不存在用户被拒', status == 400, f'{status} {text[:100]}')
    check('被拒后绑定未被破坏', row and row['user_id'] == bind['id'],
          f"user_id={row['user_id'] if row else None}")

    # 删除用户后，绑定应被清空
    status, data, text = api(cookies, '/api/users/binduser', 'DELETE')
    row = mount_row('crudmnt2')
    check('删除用户后挂载点解绑', row and row['user_id'] is None,
          f"user_id={row['user_id'] if row else None}")

    # 删除挂载点
    status, data, text = api(cookies, '/api/mounts/crudmnt2', 'DELETE')
    check('删除挂载点返回 200', status == 200, f'{status} {text[:120]}')
    check('挂载点行已删除', mount_row('crudmnt2') is None)
    api(cookies, '/api/mounts/crudmntbyname', 'DELETE')


def case_online_consistency(cookies):
    """改口令 / 改名之后，在线状态、STR 表、源站连接是否跟着变"""
    print('\n[C] 在线挂载点的改口令 / 改名')
    import select
    import socket

    api(cookies, '/api/mounts', 'POST', {'mount': 'livemnt', 'password': 'livepass1'})
    src = socket.create_connection(('127.0.0.1', smoke.NTRIP_PORT), timeout=5)
    src.sendall(b'SOURCE livepass1 /livemnt\r\nUser-Agent: CrudSource\r\n'
                b'Source-Agent: NTRIP CrudSource/1.0\r\n\r\n')
    src.settimeout(5)
    resp = src.recv(1024)
    check('源站推流成功', b'ICY 200 OK' in resp, resp[:100])
    time.sleep(0.6)

    status, data, text = api(cookies, '/api/mounts')
    live = [m for m in (data or []) if m.get('mount') == 'livemnt']
    check('挂载点显示在线', live and live[0].get('active') is True, str(live)[:160])

    # 改口令：源站用的是旧口令，必须被踢下线，不能继续推
    status, data, text = api(cookies, '/api/mounts/livemnt', 'PUT', {'password': 'newlive99'})
    check('改在线挂载点口令返回 200', status == 200, f'{status} {text[:120]}')
    time.sleep(1.0)
    closed = False
    try:
        r, _, _ = select.select([src], [], [], 1.0)
        if r:
            closed = (src.recv(1) == b'')
        else:
            # 没数据也可能已经关了；再写一次看是否报错
            try:
                src.sendall(b'\xd3\x00\x13')
            except OSError:
                closed = True
    except OSError:
        closed = True
    check('改口令后旧口令的源站被断开', closed, '源站仍在推流，等于口令修改没生效')

    status, data, text = api(cookies, '/api/mounts')
    live = [m for m in (data or []) if m.get('mount') == 'livemnt']
    check('改口令后在线状态被清除', live and live[0].get('active') is False, str(live)[:160])

    status, data, text = api(cookies, '/api/str-table')
    str_names = list((data or {}).get('str_data', {}).keys())
    check('改口令后 STR 表不再残留', 'livemnt' not in str_names, str(str_names)[:120])

    try:
        src.close()
    except OSError:
        pass

    # 重新用新口令推流后改名
    src = socket.create_connection(('127.0.0.1', smoke.NTRIP_PORT), timeout=5)
    src.sendall(b'SOURCE newlive99 /livemnt\r\nUser-Agent: CrudSource\r\n'
                b'Source-Agent: NTRIP CrudSource/1.0\r\n\r\n')
    src.settimeout(5)
    resp = src.recv(1024)
    check('新口令可重新推流', b'ICY 200 OK' in resp, resp[:100])
    time.sleep(0.6)

    status, data, text = api(cookies, '/api/mounts/livemnt', 'PUT', {'mount_name': 'livemnt2'})
    check('在线状态下改名返回 200', status == 200, f'{status} {text[:120]}')
    time.sleep(1.0)
    check('改名已落库', mount_row('livemnt2') is not None and mount_row('livemnt') is None)

    status, data, text = api(cookies, '/api/mounts')
    names = [m.get('mount') for m in (data or []) if m.get('active')]
    check('在线列表里不再出现旧名', 'livemnt' not in names, f'在线: {names}')
    status, data, text = api(cookies, '/api/str-table')
    str_names = list((data or {}).get('str_data', {}).keys())
    check('STR 表里不再出现旧名', 'livemnt' not in str_names, f'STR: {str_names}')

    try:
        src.close()
    except OSError:
        pass
    api(cookies, '/api/mounts/livemnt2', 'DELETE')


def case_admin_and_leak(cookies):
    print('\n[D] 管理员口令与敏感字段')
    before = q("SELECT password FROM admins WHERE username = 'admin'")[0]['password']
    status, data, text = api(cookies, '/api/admin/password', 'PUT', {'password': 'adminpass9'})
    after = q("SELECT password FROM admins WHERE username = 'admin'")[0]['password']
    check('设置页改管理员口令返回 200', status == 200, f'{status} {text[:120]}')
    check('管理员新口令已落库', verify(after, 'adminpass9'))
    check('管理员口令已变化', before != after)

    status, data, text = api(cookies, '/api/users')
    raw = text
    hashes = [r['password'] for r in q("SELECT password FROM users")] + \
             [r['password'] for r in q("SELECT password FROM mounts")] + [after]
    leaked = [h for h in hashes if h and h in raw]
    check('用户列表不泄露任何口令哈希', not leaked, f'泄露 {len(leaked)} 条')

    status, data, text = api(cookies, '/api/mounts')
    leaked = [h for h in hashes if h and h in text]
    check('挂载点列表不泄露任何口令哈希', not leaked, f'泄露 {len(leaked)} 条')

    # 复原管理员口令，避免影响其它用例
    api(cookies, '/api/admin/password', 'PUT', {'password': ADMIN[1]})


def main():
    global DB_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()
    smoke.VERBOSE = args.verbose

    tmpdir = tempfile.mkdtemp(prefix='ntrip_crud_')
    DB_PATH = os.path.join(tmpdir, 'smoke.db')
    cfg = smoke.write_config(tmpdir)
    proc = smoke.start_server(tmpdir, cfg)
    print(f"临时实例: NTRIP:{smoke.NTRIP_PORT} Web:{smoke.WEB_PORT} 库:{DB_PATH}")

    try:
        if not smoke.wait_http_ready(proc):
            print('服务启动超时')
            return 2
        cookies = CookieJar()
        status, text = smoke.http('/api/login', 'POST',
                                  {'username': ADMIN[0], 'password': ADMIN[1]}, cookies)
        if status != 200:
            print(f'登录失败: {status} {text[:200]}')
            return 2
        time.sleep(0.5)
        case_user_crud(cookies)
        case_mount_crud(cookies)
        case_online_consistency(cookies)
        case_admin_and_leak(cookies)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        tail = ''
        try:
            tail = proc.stdout.read().decode('utf-8', 'replace')[-2000:]
        except Exception:
            pass

    print(f"\n结果: {_ok} 通过 / {_fail} 失败")
    if _fail:
        print('--- 服务输出尾部 ---')
        print(tail)
    if args.keep:
        print(f'临时目录保留: {tmpdir}')
    return 1 if _fail else 0


if __name__ == '__main__':
    sys.exit(main())
