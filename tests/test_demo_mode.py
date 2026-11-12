#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示模式与匿名公开访问的行为测试

覆盖三组开关组合，全部用 Flask 测试客户端在进程内跑，不启真实端口、不碰真实数据库：

  A. 默认（demo_mode=false, public_access=false）
     未登录一律 401 / 跳登录页；登录后写操作正常
  B. 演示模式（demo_mode=true）
     改、删、改密码、重启全部 403；新增用户/挂载点、启停解析放行
  C. 匿名公开（public_access=true）
     未登录可看实时数据并自行发起解析；用户明细与源站 IP 不下发

用法：
    python tests/test_demo_mode.py
    python tests/test_demo_mode.py -v
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config
import src.rtcm2 as rtcm2
import src.connection as connection

VERBOSE = False
PASS = 0
FAIL = []


def check(name, got, want):
    global PASS
    if got == want:
        PASS += 1
        if VERBOSE:
            print(f"    [PASS] {name}")
        return True
    FAIL.append(f"{name}: got={got!r} want={want!r}")
    print(f"    [FAIL] {name}: got={got!r} want={want!r}")
    return False


# ---------------------------------------------------------------- 测试替身
STARTED = []


class FakeParserThread:
    """替掉真实解析线程：只记录是否被启动/停止，不读 socket、不算 MSM"""

    def __init__(self, mount, cb=None):
        self.mount = mount
        self.callback = cb
        self._alive = True
        STARTED.append(self)

    def is_alive(self):
        return self._alive

    def stop(self):
        self._alive = False


def fake_start_web_parser(mount, cb=None):
    return FakeParserThread(mount, cb)


rtcm2.start_web_parser = fake_start_web_parser


class StubDB:
    """只覆盖 Web 层会调用的方法，不落盘"""

    def verify_admin(self, username, password):
        return (username, password) == ('admin', 'admin123')

    def get_all_users(self):
        return []

    def get_all_mounts(self):
        return []

    def add_user(self, username, password):
        return True, 'ok'

    def add_mount(self, mount, password, user_id=None):
        return True, 'ok'

    def delete_user(self, username):
        return True, 'ok'

    def delete_mount(self, mount):
        return True, 'ok'

    def update_admin_password(self, username, password):
        return True, 'ok'


class StubForwarder:
    pass


from src.web import WebManager  # noqa: E402  （必须在打桩之后导入）

wm = WebManager(StubDB(), StubForwarder(), time.time())
app = wm.app
app.config['TESTING'] = True

# 注册一个在线挂载点，供解析订阅用例使用
connection.get_connection_manager().add_mount_connection('AAA', '127.0.0.1', 'tests')


def new_client():
    return app.test_client()


def login(client):
    return client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})


def alive_count():
    return len([t for t in STARTED if t.is_alive()])


def case_a_default_closed():
    print("[A] 默认：未登录不可访问")
    config.DEMO_MODE = False
    config.PUBLIC_ACCESS = False

    c = new_client()
    r = c.get('/')
    check('未登录访问首页被重定向', r.status_code, 302)
    check('重定向到登录页', '/login' in (r.headers.get('Location') or ''), True)
    check('未登录 /api/users', c.get('/api/users').status_code, 401)
    check('未登录 /api/public/...', c.get('/api/public/mount/rtcm-parse/status').status_code, 401)
    check('未登录无权发起解析',
          c.post('/api/public/mount/AAA/rtcm-parse/start').status_code, 401)

    check('默认管理员可登录', login(c).status_code, 200)
    check('登录后首页', c.get('/').status_code, 200)
    check('登录后用户列表', c.get('/api/users').status_code, 200)
    r = c.delete('/api/users/nobody')
    check('关闭演示模式时不返回 demo_readonly', (r.get_json() or {}).get('code'), None)


def case_b_demo_mode():
    print("[B] 演示模式：只能新增")
    config.DEMO_MODE = True
    config.PUBLIC_ACCESS = False

    c = new_client()
    login(c)

    r = c.put('/api/admin/password', json={'password': 'newpass123'})
    check('改管理员密码 403', r.status_code, 403)
    check('403 带稳定错误码', r.get_json().get('code'), 'demo_readonly')
    check('删用户 403', c.delete('/api/users/foo').status_code, 403)
    check('改用户 403', c.put('/api/users/foo', json={'password': 'x123456'}).status_code, 403)
    check('删挂载点 403', c.delete('/api/mounts/AAA').status_code, 403)
    check('改挂载点 403', c.put('/api/mounts/AAA', json={'password': 'x123456'}).status_code, 403)
    check('重启服务 403', c.post('/api/system/restart').status_code, 403)
    check('全局停止旁路解析 403', c.post('/api/bypass/stop-all').status_code, 403)
    check('初始化挂载点 403',
          c.post('/api/mount/initialize', json={'mount_name': 'BBB'}).status_code, 403)

    check('新增用户放行',
          c.post('/api/users', json={'username': 'demo1', 'password': 'demo123'}).status_code, 201)
    check('新增挂载点放行',
          c.post('/api/mounts', json={'mount': 'BBB', 'password': 'demo123'}).status_code, 201)

    r = c.post('/api/mount/AAA/rtcm-parse/start')
    check('启停解析放行', r.status_code, 200)
    check('解析线程已启动', alive_count(), 1)
    r = c.get('/api/mount/rtcm-parse/status')
    check('状态回显自己的挂载点', r.get_json()['status']['my_mount'], 'AAA')
    r = c.post('/api/mount/rtcm-parse/heartbeat', json={'mount_name': 'AAA'})
    check('心跳放行', r.status_code, 200)
    check('心跳确认在解析', r.get_json()['parsing'], True)

    other = new_client()
    r = other.post('/api/login', json={'username': 'someone', 'password': 'admin123'})
    check('非默认管理员被拒', r.status_code, 403)
    check('拒绝原因', (r.get_json() or {}).get('code'), 'demo_account')


def case_c_public_access():
    print("[C] 匿名公开访问")
    config.DEMO_MODE = False
    config.PUBLIC_ACCESS = True

    anon = new_client()
    check('匿名首页 200', anon.get('/').status_code, 200)
    html = anon.get('/').get_data(as_text=True)
    check('首页注入登录态 false', '__LOGGED_IN__ = false' in html, True)
    check('匿名不渲染用户管理入口', 'data-page="users"' in html, False)
    check('匿名仍可进实时数据', 'data-page="monitor"' in html, True)

    check('匿名仍无权访问 /api/users', anon.get('/api/users').status_code, 401)
    check('匿名无权访问 /api/system/stats', anon.get('/api/system/stats').status_code, 401)

    r = anon.get('/api/public/mount/rtcm-parse/status')
    check('匿名可查解析状态', r.status_code, 200)
    check('匿名初始无订阅', r.get_json()['status']['my_mount'], None)

    r = anon.post('/api/public/mount/AAA/rtcm-parse/start')
    check('匿名可发起解析', r.status_code, 200)
    check('挂载点线程被复用而不是重建', alive_count(), 1)
    r = anon.get('/api/public/mount/rtcm-parse/status')
    check('匿名看到自己的订阅', r.get_json()['status']['my_mount'], 'AAA')
    check('匿名看不到别人的挂载点列表', r.get_json()['status']['web_mounts'], [])

    check('匿名退订', anon.post('/api/public/mount/rtcm-parse/stop').status_code, 200)

    r = anon.post('/api/public/mount/NOSUCH/rtcm-parse/start')
    check('离线挂载点被拒', r.status_code, 400)
    check('离线原因', (r.get_json() or {}).get('code'), 'mount_offline')

    resp = anon.get('/api/public/mount/rtcm-parse/status')
    check('下发客户端标识 Cookie', 'ntrip_cid=' in (resp.headers.get('Set-Cookie') or ''), True)


def main():
    global VERBOSE
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    VERBOSE = args.verbose

    try:
        case_a_default_closed()
        case_b_demo_mode()
        case_c_public_access()
    finally:
        # 恢复配置，避免影响同进程内的其他测试
        config.DEMO_MODE = False
        config.PUBLIC_ACCESS = False

    print()
    if FAIL:
        print(f"结果: {PASS} 通过 / {len(FAIL)} 失败")
        for f in FAIL:
            print("  !", f)
        return 1
    print(f"结果: {PASS} 通过 / 0 失败")
    return 0


if __name__ == '__main__':
    sys.exit(main())
