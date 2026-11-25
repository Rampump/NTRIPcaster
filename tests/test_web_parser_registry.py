#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web 解析订阅注册表的行为测试

验证「按挂载点复用线程 + 客户端只持有订阅」这套模型的关键性质：

  1. 同一挂载点多人看只跑 1 个线程（不是一人一线程）
  2. 幂等订阅不会重启线程
  3. 退订只摘掉自己那条边，别人的解析不中断
  4. 订阅归零后留宽限期才停线程，来回切站不会反复重建
  5. 心跳超时自动退订（兜住「直接关浏览器」）
  6. 线程上限 / 单 IP 限流
  7. 源站离线时立即停线程并通知订阅者
  8. status 不回显别人正在看的挂载点

用假时钟 + 假解析线程，不启动真实线程、不读 socket。

用法：
    python tests/test_web_parser_registry.py
    python tests/test_web_parser_registry.py -v
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config
import src.rtcm2 as rtcm2
from src import web_parser_registry as regmod

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


class FakeClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeParser:
    def __init__(self, mount, cb=None):
        self.mount = mount
        self.callback = cb
        self._alive = True

    def is_alive(self):
        return self._alive

    def stop(self):
        self._alive = False


STARTED = []


def fake_start_web_parser(mount, cb=None):
    t = FakeParser(mount, cb)
    STARTED.append(t)
    return t


rtcm2.start_web_parser = fake_start_web_parser


def alive(mount=None):
    return len([t for t in STARTED if t.is_alive() and (mount is None or t.mount == mount)])


def make_registry(clock, online=None):
    reg = regmod.WebParserRegistry(
        push_callback_provider=lambda m: (lambda d: None),
        clock=clock,
    )
    state = online or {}
    reg._mount_online = staticmethod(lambda m: state.get(m, True))
    return reg, state


def main():
    global VERBOSE
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    VERBOSE = args.verbose

    # 固定阈值，避免受本机 config.ini 影响
    saved = (config.WEB_PARSER_LIMIT, config.WEB_PARSER_CLIENT_TTL,
             config.WEB_PARSER_GRACE_SECONDS, config.WEB_PARSER_IP_RATE)
    config.WEB_PARSER_LIMIT = 3
    config.WEB_PARSER_CLIENT_TTL = 45
    config.WEB_PARSER_GRACE_SECONDS = 5
    config.WEB_PARSER_IP_RATE = 0

    clock = FakeClock()
    reg, online = make_registry(clock)
    events = []
    reg._event_sink = lambda ev, payload, m: events.append((ev, payload, m))

    try:
        print("[1] 同一挂载点多人看只跑一个线程")
        s = reg.subscribe('c1', 'A', '1.1.1.1')
        check('首个订阅者', s['subscribers'], 1)
        check('启动 1 个线程', alive(), 1)
        s = reg.subscribe('c2', 'A', '2.2.2.2')
        check('第二个订阅者', s['subscribers'], 2)
        check('线程数仍是 1', alive(), 1)
        check('c2 状态正确', reg.status('c2')['my_mount'], 'A')

        print("[2] 幂等订阅")
        reg.subscribe('c1', 'A', '1.1.1.1')
        check('重复订阅不重建线程', alive(), 1)
        check('订阅者数不变', reg.status('c1')['my_subscribers'], 2)

        print("[3] 不同挂载点互不影响")
        reg.subscribe('c3', 'B', '3.3.3.3')
        check('两个线程', alive(), 2)

        print("[4] 退订只摘掉自己那条边")
        reg.unsubscribe('c2')
        check('c2 已无订阅', reg.status('c2')['my_mount'], None)
        check('A 的线程仍在跑', alive('A'), 1)

        print("[5] 订阅归零后宽限期才停")
        reg.unsubscribe('c1')
        check('宽限期内线程仍存活', alive('A'), 1)
        clock.advance(4)
        reg.sweep()
        check('4 秒时还没停', alive('A'), 1)
        clock.advance(2)
        reg.sweep()
        check('6 秒后已停', alive('A'), 0)

        print("[6] 心跳超时自动退订")
        reg.subscribe('c4', 'B', '4.4.4.4')
        clock.advance(46)
        reg.sweep()
        check('超时后失去订阅', reg.status('c4')['my_mount'], None)
        clock.advance(6)
        reg.sweep()
        check('B 的线程被回收', alive('B'), 0)

        print("[7] 心跳续期不超时")
        reg.subscribe('c5', 'C', '5.5.5.5')
        clock.advance(30)
        check('心跳返回在解析', reg.touch('c5'), True)
        clock.advance(30)
        reg.sweep()
        check('续期后仍在解析', reg.status('c5')['my_mount'], 'C')

        print("[8] 线程上限")
        config.WEB_PARSER_LIMIT = 2   # 当前活跃 1 个，再开 1 个正好到顶
        reg.subscribe('c6', 'D', '6.6.6.6')
        try:
            reg.subscribe('c7', 'A', '7.7.7.7')
            check('超限应抛错', False, True)
        except regmod.SubscribeBusy as e:
            check('超限错误码', e.code, 'too_many_parsers')
        config.WEB_PARSER_LIMIT = 3

        print("[9] 单 IP 限流")
        config.WEB_PARSER_IP_RATE = 2
        reg2, _ = make_registry(clock)
        reg2.subscribe('x1', 'A', '9.9.9.9')
        reg2.subscribe('x2', 'B', '9.9.9.9')
        try:
            reg2.subscribe('x3', 'C', '9.9.9.9')
            check('限流应抛错', False, True)
        except regmod.SubscribeBusy as e:
            check('限流错误码', e.code, 'rate_limited')
        reg2.stop_all()
        config.WEB_PARSER_IP_RATE = 0

        print("[10] 源站离线立即停线程并通知")
        online['C'] = False
        events.clear()
        reg.sweep()
        check('C 的线程已停', alive('C'), 0)
        check('推送了停止事件', [e[0] for e in events], ['rtcm_parse_stopped'])
        check('停止原因', events[0][1]['reason'], 'mount_offline')
        check('订阅关系已清', reg.status('c5')['my_mount'], None)

        print("[11] status 不回显别人的挂载点")
        reg.subscribe('c8', 'A', '8.8.8.8')
        st = reg.status('c8')
        check('非管理员看不到清单', st['web_mounts'], [])
        check('自己的挂载点仍正确', st['my_mount'], 'A')

        print("[12] stop_all")
        stopped = reg.stop_all()
        check('停掉的挂载点', sorted(stopped), ['A', 'D'])
        check('全部线程已停', alive(), 0)

        print("[13] 切站不被宽限期空闲线程阻挡（BUG 回归）")
        config.WEB_PARSER_LIMIT = 2
        clock2 = FakeClock()
        reg4, _ = make_registry(clock2)
        base = len(STARTED)

        def alive4(mount=None):
            return len([t for t in STARTED[base:]
                        if t.is_alive() and (mount is None or t.mount == mount)])

        reg4.subscribe('s1', 'A', '1.1.1.1')
        check('切换前 A 在跑', alive4('A'), 1)
        reg4.subscribe('s1', 'B', '1.1.1.1')   # A 进宽限期，B 启动
        check('切换后 B 在跑', alive4('B'), 1)
        check('A 仍在宽限期存活', alive4('A'), 1)
        check('有订阅者的线程仅 1 个', reg4._active_count_locked(), 1)
        # 存活线程总数已达 limit=2，但 A 是空闲宽限期线程，不应阻挡新订阅
        try:
            reg4.subscribe('s2', 'C', '2.2.2.2')
            ok = True
        except regmod.SubscribeBusy:
            ok = False
        check('切站后可订阅新挂载点(不报任务已满)', ok, True)
        check('C 已启动', alive4('C'), 1)
        reg4.stop_all()
    finally:
        (config.WEB_PARSER_LIMIT, config.WEB_PARSER_CLIENT_TTL,
         config.WEB_PARSER_GRACE_SECONDS, config.WEB_PARSER_IP_RATE) = saved

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
