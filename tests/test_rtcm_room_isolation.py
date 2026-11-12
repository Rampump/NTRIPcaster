#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RTCM 推送的房间隔离测试（防「串台」回归）

改造成「按挂载点房间下发」之前，push_callback 用的是无房间广播，
所有人都会收到所有人的解析数据；同时前端收到推送还会把当前挂载点改成推送来源，
于是别人切站会把你自己的界面一起带走。这组用例锁死这两点：

  1. 订阅 AAA 的客户端只收到 AAA 的数据
  2. 订阅 BBB 的客户端只收到 BBB 的数据
  3. 切站后立刻停止接收旧站数据
  4. 退订后不再接收任何数据

用 Flask-SocketIO 的测试客户端（支持 rooms），进程内运行，不开端口。

用法：
    python tests/test_rtcm_room_isolation.py
    python tests/test_rtcm_room_isolation.py -v
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config

VERBOSE = False
PASS = 0
FAIL = []


def check(name, got, want):
    global PASS
    if got == want:
        PASS += 1
        if VERBOSE:
            print(f"  [PASS] {name}")
        return True
    FAIL.append(f"{name}: got={got!r} want={want!r}")
    print(f"  [FAIL] {name}: got={got!r} want={want!r}")
    return False


class StubDB:
    def verify_admin(self, username, password):
        return False

    def get_all_users(self):
        return []

    def get_all_mounts(self):
        return []


class StubForwarder:
    pass


from src.web import WebManager  # noqa: E402


def rtcm_events(client):
    return [m for m in client.get_received() if m['name'] == 'rtcm_realtime_data']


def main():
    global VERBOSE
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    VERBOSE = args.verbose

    saved_public = config.PUBLIC_ACCESS
    config.PUBLIC_ACCESS = True   # 测试客户端是匿名身份

    try:
        wm = WebManager(StubDB(), StubForwarder(), time.time())
        app = wm.app
        app.config['TESTING'] = True

        a = wm.socketio.test_client(app)
        b = wm.socketio.test_client(app)

        print("[1] 匿名客户端可连接")
        check('A 已连接', a.is_connected(), True)
        check('B 已连接', b.is_connected(), True)
        a.get_received()
        b.get_received()

        print("[2] 订阅不同挂载点")
        a.emit('rtcm_subscribe', {'mount_name': 'AAA'})
        b.emit('rtcm_subscribe', {'mount_name': 'BBB'})
        time.sleep(0.05)
        a.get_received()
        b.get_received()

        push_a = wm._make_push_callback('AAA')
        push_b = wm._make_push_callback('BBB')

        print("[3] 向 AAA 推送")
        push_a({'mount_name': 'AAA', 'data_type': 'station_info', 'station_id': 'S1'})
        time.sleep(0.05)
        check('A 收到 AAA 数据', len(rtcm_events(a)), 1)
        check('B 没收到 AAA 数据', len(rtcm_events(b)), 0)

        print("[4] 向 BBB 推送")
        push_b({'mount_name': 'BBB', 'data_type': 'station_info', 'station_id': 'S2'})
        time.sleep(0.05)
        check('A 没收到 BBB 数据', len(rtcm_events(a)), 0)
        check('B 收到 BBB 数据', len(rtcm_events(b)), 1)

        print("[5] A 切到 BBB")
        a.emit('rtcm_subscribe', {'mount_name': 'BBB'})
        time.sleep(0.05)
        a.get_received()
        b.get_received()
        push_a({'mount_name': 'AAA', 'data_type': 'station_info'})
        push_b({'mount_name': 'BBB', 'data_type': 'station_info'})
        time.sleep(0.05)
        ea = rtcm_events(a)
        eb = rtcm_events(b)
        check('切站后 A 不再收 AAA', len(ea), 1)
        check('切站后 A 收到 BBB', ea[0]['args'][0]['mount_name'], 'BBB')
        check('B 仍只收 BBB', len(eb), 1)

        print("[6] B 退订后不再收")
        b.emit('rtcm_unsubscribe', {})
        time.sleep(0.05)
        a.get_received()
        b.get_received()
        push_b({'mount_name': 'BBB', 'data_type': 'station_info'})
        time.sleep(0.05)
        check('B 退订后无数据', len(rtcm_events(b)), 0)
        check('A 仍在收 BBB', len(rtcm_events(a)), 1)

        a.disconnect()
        b.disconnect()
    finally:
        config.PUBLIC_ACCESS = saved_public

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
