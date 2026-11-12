# -*- coding: utf-8 -*-
"""Web 端 RTCM 实时解析的订阅注册表

改造前的模型是「全局单例」：
    RTCM2ParserManager.start_realtime_parsing() 会主动杀掉上一个挂载点的解析线程，
    push_callback 用 socketio.emit() 无房间广播，前端收到推送也不校验 mount。
    结果是 A 访客点 B 站，A 自己的界面会被一起带过去，数据还会串台。

改造后的模型是「按挂载点复用线程 + 客户端只持有订阅」：
    - 同一个挂载点无论多少人看，只跑 1 个解析线程（MSM 解析吃 CPU，不能按人开线程）；
    - 客户端（用 cid 标识）只持有一条订阅边，退订只摘掉自己那条；
    - 订阅集合空了之后不立刻停线程，留 GRACE_SECONDS 宽限期，避免来回切站反复重建；
    - 心跳超时（CLIENT_TTL）自动退订，兜住「直接关浏览器 / 断网」的僵尸订阅；
    - 线程总数超过 WEB_PARSER_LIMIT 时新订阅返回 429，防止公网被刷爆 CPU。

数据下发仍然走 Socket.IO 房间：房间名 mount:<挂载点>，由 web 层在 socket 事件里
join/leave。本模块只负责线程与引用计数，不直接碰 socket，便于单独测试。
"""

import threading
import time
from collections import deque
from typing import Any, Callable, Dict, Optional

from . import config
from .logger import log_debug, log_info, log_warning


class SubscribeRejected(Exception):
    """订阅被业务规则拒绝（挂载点不存在/离线、线程启动失败等），对应 HTTP 400"""

    def __init__(self, message, code='rejected'):
        super().__init__(message)
        self.message = message
        self.code = code


class SubscribeBusy(Exception):
    """订阅被限流或线程池打满，对应 HTTP 429"""

    def __init__(self, message, code='busy'):
        super().__init__(message)
        self.message = message
        self.code = code


class WebParserRegistry:
    """Web 实时解析线程的订阅注册表（线程安全）"""

    #: 巡检间隔（秒）：既处理心跳超时，也处理宽限期到点的停机
    SWEEP_INTERVAL = 2.0
    #: 限流窗口（秒）
    RATE_WINDOW = 60.0

    def __init__(self, push_callback_provider=None, event_sink=None, clock=None):
        # push_callback_provider(mount) -> callback，由 web 层注入（回调里往房间 emit）
        self._push_provider: Optional[Callable[[str], Callable]] = push_callback_provider
        # event_sink(event, payload, mount)，用于把「源站离线」「线程被停」推给订阅者
        self._event_sink: Optional[Callable[[str, Dict, str], None]] = event_sink
        self._clock = clock or time.time

        self._lock = threading.RLock()
        # cid -> {'mount': str|None, 'last_seen': float, 'ip': str}
        self._clients: Dict[str, Dict[str, Any]] = {}
        # mount -> {'thread': obj, 'cids': set(), 'started_at': float, 'stop_at': float|None}
        self._mounts: Dict[str, Dict[str, Any]] = {}
        # ip -> deque[timestamp]，滑动窗口限流
        self._rate: Dict[str, deque] = {}

        self._sweeper: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------ 工具
    def _limit(self) -> int:
        try:
            return max(1, int(getattr(config, 'WEB_PARSER_LIMIT', 4)))
        except (TypeError, ValueError):
            return 4

    def _ttl(self) -> float:
        try:
            return max(5.0, float(getattr(config, 'WEB_PARSER_CLIENT_TTL', 45)))
        except (TypeError, ValueError):
            return 45.0

    def _grace(self) -> float:
        try:
            return max(0.0, float(getattr(config, 'WEB_PARSER_GRACE_SECONDS', 5)))
        except (TypeError, ValueError):
            return 5.0

    @staticmethod
    def _is_alive(entry: Dict[str, Any]) -> bool:
        thread = entry.get('thread')
        try:
            return bool(thread is not None and thread.is_alive())
        except Exception:
            return False

    def _active_count_locked(self) -> int:
        return sum(1 for e in self._mounts.values() if self._is_alive(e))

    def _check_rate_locked(self, ip: str) -> bool:
        try:
            limit = int(getattr(config, 'WEB_PARSER_IP_RATE', 10))
        except (TypeError, ValueError):
            limit = 10
        if limit <= 0 or not ip:
            return True
        now = self._clock()
        bucket = self._rate.get(ip)
        if bucket is None:
            bucket = deque()
            self._rate[ip] = bucket
        while bucket and now - bucket[0] > self.RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    # -------------------------------------------------------------- 订阅管理
    def subscribe(self, cid: str, mount: str, ip: str = '') -> Dict[str, Any]:
        """订阅某挂载点的实时解析

        幂等：同一客户端重复订阅同一挂载点不会重启线程、不消耗限流额度。
        切换挂载点时自动退掉旧订阅（引用计数 -1）。
        """
        if not cid:
            raise SubscribeRejected('缺少客户端标识', 'bad_request')
        if not mount:
            raise SubscribeRejected('缺少挂载点名称', 'bad_request')

        with self._lock:
            now = self._clock()
            client = self._clients.get(cid)
            if client is None:
                client = {'mount': None, 'last_seen': now, 'ip': ip}
                self._clients[cid] = client
            client['last_seen'] = now
            if ip:
                client['ip'] = ip

            entry = self._mounts.get(mount)
            # 已经在该挂载点上：幂等返回，顺手把宽限期撤销
            if client.get('mount') == mount and entry is not None and self._is_alive(entry):
                entry['cids'].add(cid)
                entry['stop_at'] = None
                return self._snapshot_locked(mount)

            if not self._check_rate_locked(ip):
                raise SubscribeBusy('操作过于频繁，请稍后再试', 'rate_limited')

            # 先退掉自己的旧订阅；引用计数归零时旧线程进入宽限期
            self._release_locked(cid)

            if entry is None or not self._is_alive(entry):
                self._mounts.pop(mount, None)
                if self._active_count_locked() >= self._limit():
                    raise SubscribeBusy('当前解析任务已满，请稍后再试', 'too_many_parsers')
                self._start_thread_locked(mount)
                entry = self._mounts[mount]

            entry['cids'].add(cid)
            entry['stop_at'] = None
            client['mount'] = mount
            log_debug(f"客户端 {cid[:8]} 订阅 [挂载点: {mount}]，当前订阅者 {len(entry['cids'])}")
            return self._snapshot_locked(mount)

    def unsubscribe(self, cid: str) -> Optional[str]:
        """退订当前客户端持有的订阅，返回被退订的挂载点"""
        with self._lock:
            mount = self._release_locked(cid)
            client = self._clients.get(cid)
            if client is not None:
                client['last_seen'] = self._clock()
            if mount:
                log_debug(f"客户端 {cid[:8]} 退订 [挂载点: {mount}]")
            return mount

    def touch(self, cid: str, mount: str = None) -> bool:
        """刷新客户端心跳；返回该客户端当前是否真的在解析

        mount 参数只用于日志/兼容，实际以注册表里记录的关系为准——
        避免前端传一个过期的挂载点名把心跳错记到别处。
        """
        with self._lock:
            client = self._clients.get(cid)
            if client is None:
                return False
            client['last_seen'] = self._clock()
            current = client.get('mount')
            if not current:
                return False
            entry = self._mounts.get(current)
            if entry is None or not self._is_alive(entry):
                # 线程已经不在了（源站离线 / 被上限挤掉）：清掉本地关系，
                # 让前端的状态轮询看到“未解析”，绿色自动恢复。
                client['mount'] = None
                return False
            entry['stop_at'] = None
            return True

    def _release_locked(self, cid: str) -> Optional[str]:
        client = self._clients.get(cid)
        if not client:
            return None
        mount = client.get('mount')
        if not mount:
            return None
        entry = self._mounts.get(mount)
        if entry is not None:
            entry['cids'].discard(cid)
            if not entry['cids']:
                entry['stop_at'] = self._clock() + self._grace()
        client['mount'] = None
        return mount

    def _start_thread_locked(self, mount: str) -> None:
        """启动挂载点的解析线程（调用方必须持锁）"""
        try:
            from .rtcm2 import start_web_parser
        except Exception as e:  # pragma: no cover - 依赖导入失败属于环境问题
            log_warning(f"导入解析器失败: {e}")
            raise SubscribeRejected('解析器不可用', 'parser_unavailable')

        callback = None
        if self._push_provider:
            try:
                callback = self._push_provider(mount)
            except Exception as e:
                log_warning(f"构建推送回调失败 [挂载点: {mount}]: {e}")

        try:
            thread = start_web_parser(mount, callback)
        except Exception as e:
            log_warning(f"启动 Web 解析线程失败 [挂载点: {mount}]: {e}")
            raise SubscribeRejected('解析线程启动失败', 'thread_failed')

        self._mounts[mount] = {
            'thread': thread,
            'cids': set(),
            'started_at': self._clock(),
            'stop_at': None,
        }
        log_info(f"Web 解析线程已启动 [挂载点: {mount}]，当前线程数 {self._active_count_locked()}")

    def _stop_entry(self, mount: str, entry: Dict[str, Any]) -> None:
        """停线程（不要在持锁状态下调用，stop() 会 join 解析线程）"""
        thread = entry.get('thread')
        if thread is not None:
            try:
                thread.stop()
            except Exception as e:
                log_warning(f"停止 Web 解析线程失败 [挂载点: {mount}]: {e}")
        # 如果还有订阅者，说明是源站离线被动停的，必须通知前端复原界面
        cids = entry.get('cids') or set()
        if cids and self._event_sink:
            try:
                self._event_sink('rtcm_parse_stopped', {
                    'mount_name': mount,
                    'reason': 'mount_offline',
                }, mount)
            except Exception as e:
                log_debug(f"推送解析停止事件失败: {e}")
        log_info(f"Web 解析线程已停止 [挂载点: {mount}]")

    def stop_all(self) -> list:
        """停止所有 Web 解析线程（返回被停的挂载点列表）"""
        with self._lock:
            entries = list(self._mounts.items())
            self._mounts.clear()
            for client in self._clients.values():
                client['mount'] = None
        for mount, entry in entries:
            self._stop_entry(mount, entry)
        return [m for m, _ in entries]

    # ------------------------------------------------------------------ 巡检
    def sweep(self) -> None:
        """清理僵尸订阅与到点的宽限期线程"""
        now = self._clock()
        ttl = self._ttl()
        to_stop = []
        need_online_check = []

        with self._lock:
            # 1) 心跳超时的客户端：退订并遗忘
            for cid, client in list(self._clients.items()):
                if now - (client.get('last_seen') or 0) <= ttl:
                    continue
                mount = self._release_locked(cid)
                if mount:
                    log_debug(f"客户端 {cid[:8]} 心跳超时，已退订 [挂载点: {mount}]")
                self._clients.pop(cid, None)

            # 2) 线程已死 / 宽限期到点的挂载点
            for mount, entry in list(self._mounts.items()):
                if not self._is_alive(entry):
                    self._mounts.pop(mount, None)
                    continue
                stop_at = entry.get('stop_at')
                if not entry['cids'] and stop_at and now >= stop_at:
                    self._mounts.pop(mount, None)
                    to_stop.append((mount, entry))
                    continue
                if entry['cids']:
                    # 源站在线性检查放到锁外做：它要拿连接管理器的锁，
                    # 在持锁状态下等待会连带阻塞所有订阅请求
                    need_online_check.append((mount, entry))

            # 3) 限流窗口清理，防止 _rate 字典无界增长
            for ip, bucket in list(self._rate.items()):
                while bucket and now - bucket[0] > self.RATE_WINDOW:
                    bucket.popleft()
                if not bucket:
                    self._rate.pop(ip, None)

        # 锁外查在线状态，再回到锁内摘除
        offline = [mount for mount, _ in need_online_check if not self._mount_online(mount)]
        if offline:
            with self._lock:
                for mount in offline:
                    entry = self._mounts.pop(mount, None)
                    if entry is None:
                        continue
                    for cid in list(entry['cids']):
                        client = self._clients.get(cid)
                        if client and client.get('mount') == mount:
                            client['mount'] = None
                    to_stop.append((mount, entry))

        for mount, entry in to_stop:
            self._stop_entry(mount, entry)

    @staticmethod
    def _mount_online(mount: str) -> bool:
        try:
            from . import connection
            return bool(connection.get_connection_manager().is_mount_online(mount))
        except Exception:
            # 查询失败时按“在线”处理，避免误杀正在用的解析线程
            return True

    def _sweep_loop(self) -> None:
        while not self._stop_event.wait(self.SWEEP_INTERVAL):
            try:
                self.sweep()
            except Exception as e:  # pragma: no cover
                log_warning(f"解析注册表巡检异常: {e}")

    def start(self) -> None:
        """启动后台巡检线程（幂等）"""
        with self._lock:
            if self._sweeper is not None and self._sweeper.is_alive():
                return
            self._stop_event.clear()
            self._sweeper = threading.Thread(
                target=self._sweep_loop, name='web-parser-sweeper', daemon=True
            )
            self._sweeper.start()
            log_info(f"Web 解析注册表巡检已启动（上限 {self._limit()} 线程 / 心跳 {int(self._ttl())}s）")

    def shutdown(self) -> None:
        """停止巡检并停掉所有解析线程（进程退出时调用）"""
        self._stop_event.set()
        sweeper = self._sweeper
        if sweeper is not None and sweeper.is_alive():
            sweeper.join(timeout=3)
        self._sweeper = None
        self.stop_all()

    # ------------------------------------------------------------------ 查询
    def _snapshot_locked(self, mount: str) -> Dict[str, Any]:
        entry = self._mounts.get(mount) or {}
        return {
            'mount': mount,
            'subscribers': len(entry.get('cids') or ()),
            'web_threads': self._active_count_locked(),
            'limit': self._limit(),
        }

    def status(self, cid: str) -> Dict[str, Any]:
        """返回「该客户端视角」的解析状态

        不回显别人的挂载点：同一时刻可能有多个挂载点在被不同访客解析，
        把别人的挂载点暴露出去既没必要也不礼貌。
        """
        with self._lock:
            client = self._clients.get(cid) if cid else None
            my_mount = (client or {}).get('mount')
            entry = self._mounts.get(my_mount) if my_mount else None
            if entry is None or not self._is_alive(entry):
                my_mount = None
            return {
                'my_mount': my_mount,
                'my_parsing': bool(my_mount),
                'my_subscribers': len(entry.get('cids') or ()) if entry else 0,
                # 兼容旧前端字段：现在是「我」的挂载点，不再是全局唯一
                'current_web_mount': my_mount,
                'web_parsers': self._active_count_locked(),
                'web_mounts': sorted(
                    m for m, e in self._mounts.items() if self._is_alive(e)
                ) if _is_admin_view() else [],
                'limit': self._limit(),
            }

    def get_parser(self, mount: str):
        """取某个挂载点正在运行的解析线程（没有则 None）

        Web 解析线程现在由本注册表持有，不再登记在 RTCM2ParserManager 里，
        所以读取解析结果的地方要经这里拿线程，否则会一直读到空。
        """
        with self._lock:
            entry = self._mounts.get(mount)
            if entry and self._is_alive(entry):
                return entry.get('thread')
            return None

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'clients': len(self._clients),
                'web_parsers': self._active_count_locked(),
                'mounts': {
                    m: len(e.get('cids') or ())
                    for m, e in self._mounts.items()
                    if self._is_alive(e)
                },
                'limit': self._limit(),
            }


def _is_admin_view() -> bool:
    """是否处于「管理员查看」上下文

    挂载点清单只给登录后台的人看；匿名公开接口不能借 status 把别人
    正在看的挂载点列出来。用 flask 的 session 判断，脱离请求上下文时返回 False。
    """
    try:
        from flask import has_request_context, session as flask_session
        if not has_request_context():
            return False
        return bool(flask_session.get('admin_logged_in'))
    except Exception:
        return False
