#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import json
import socket
import secrets
import threading
from threading import Lock, RLock, Thread
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

from . import config
from . import logger
from .logger import log_system_event, log_error, log_warning, log_info, log_debug
from .rtcm2_manager import parser_manager as rtcm_manager  # 导入RTCM2解析管理器

@dataclass
class MountInfo:
    """挂载点信息数据类"""
    mount_name: str
    ip_address: str = ""
    user_agent: str = ""
    protocol_version: str = "1.0"
    connect_time: float = field(default_factory=time.time)
    connect_datetime: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    last_update: float = field(default_factory=time.time)
    
    # 添加socket引用，用于强制关闭连接
    client_socket: Optional[object] = None
    
    # 基站信息
    station_id: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    height: Optional[float] = None
    
    # 地理信息
    country: Optional[str] = None  # 国家代码（如CHN）
    city: Optional[str] = None     # 城市名称（如Beijing）
    
    # 数据统计
    total_bytes: int = 0
    total_messages: int = 0
    data_rate: float = 0.0
    data_count: int = 0
    last_data_time: Optional[float] = None

    # 近 10 秒的 (时间戳, 字节数) 样本，用于算"当前速率"。
    # 原来 data_rate = total_bytes / uptime 是自连上以来的累计平均：
    # 一条已经推了两小时的流，即使此刻完全没数据，速率也照样显示成正常值。
    rate_samples: deque = field(default_factory=lambda: deque(maxlen=2000), repr=False, compare=False)
    
    # 状态信息
    status: str = 'online'  # 'online', 'offline'
    
    # STR表信息
    str_data: str = ""
    initial_str_generated: bool = False
    final_str_generated: bool = False
    
    custom_info: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def uptime(self) -> float:
        """运行时间（秒）"""
        return time.time() - self.connect_time
    
    @property
    def idle_time(self) -> float:
        """空闲时间（秒）"""
        if self.last_data_time:
            return time.time() - self.last_data_time
        return self.uptime
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，用于JSON序列化"""
        
        return {
            'mount_name': self.mount_name,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'protocol_version': self.protocol_version,
            'connect_time': self.connect_time,
            'connect_datetime': self.connect_datetime,
            'last_update': self.last_update,
            'station_id': self.station_id,
            'lat': self.lat,
            'lon': self.lon,
            'height': self.height,
            'country': self.country,
            'city': self.city,
            'total_bytes': self.total_bytes,
            'total_messages': self.total_messages,
            'data_rate': self.data_rate,
            'data_count': self.data_count,
            'last_data_time': self.last_data_time,
            'status': self.status,
            'str_data': self.str_data,
            'initial_str_generated': self.initial_str_generated,
            'final_str_generated': self.final_str_generated,
            'custom_info': self.custom_info
        }
class ConnectionManager:
    """连接和挂载点管理器 - 统一管理在线挂载点、用户连接和STR表"""
    
    def __init__(self):
        # 在线挂载点表: {mount_name: MountInfo}
        self.online_mounts: Dict[str, MountInfo] = {}
        # 在线用户表: {user_id: [connection_info, ...]}
        self.online_users = defaultdict(list)
        # 用户连接计数: {username: count}
        self.user_connection_count = defaultdict(int)
        # 挂载点计数: {mount_name: count}
        self.mount_connection_count = defaultdict(int)
        # 统计信息
        self.total_connections = 0
        self.rejected_connections = 0
        self.clients = {}  # 活跃客户端
        
        self.mount_lock = RLock()
        self.user_lock = RLock()
        
        
    def print_active_connections(self):
        """实时打印当前所有活跃的NTRIP连接信息"""
        with self.mount_lock:
            # print("\n=== 当前活跃的NTRIP连接状态 ===")
            # print(f"活跃挂载点总数: {len(self.online_mounts)}")
            
            # if not self.online_mounts:
            #     print("当前没有活跃的挂载点连接")
            # else:
            #     for mount_name, mount_info in self.online_mounts.items():
            #         uptime = mount_info.uptime
            #         print(f"挂载点: {mount_name} | IP: {mount_info.ip_address} | 连接时长: {uptime:.1f}秒 | 状态: {mount_info.status}")
            
            # print(f"活跃用户连接总数: {len(self.online_users)}")
            # for username, connections in self.online_users.items():
            #     for conn_info in connections:
            #         print(f"用户: {username} | 连接ID: {conn_info.get('connection_id', 'N/A')} | IP: {conn_info.get('ip_address', 'N/A')} | 挂载点: {conn_info.get('mount_name', 'N/A')}")
            
            # 打印详细的连接统计
            # print(f"总连接数: {self.total_connections}, 拒绝连接数: {self.rejected_connections}")
            
            # print("=== 连接状态打印完毕 ===\n")
            pass
    
    def force_refresh_connections(self):
        """刷新连接状态

        原实现遍历所有挂载点算了一遍 invalid_mounts 却直接丢弃，纯属空转；
        这里保留接口但只做轻量清理，实际僵尸判定见 cleanup_zombie_connections()。
        """
        self.print_active_connections()
    
    def cleanup_zombie_connections(self):
        """清理僵尸连接（纯应用层判定）

        原实现在每个源站连接到来时 fork 一次 `netstat -an`，且硬编码端口 2101：
        一旦改了 NTRIP 端口，established_ips 恒为空，会把所有在线挂载点误判为
        "僵尸"并强杀（每次源站重连都会踢掉全部在线挂载点）。
        这里改为基于应用层空闲时间的判定，不再依赖系统命令，也不再有端口硬编码。
        """
        try:
            timeout = getattr(config, 'MOUNT_TIMEOUT', 1800)
            with self.mount_lock:
                zombie_mounts = [
                    mount_name for mount_name, mount_info in self.online_mounts.items()
                    if mount_info.idle_time > timeout
                ]
            
            for mount_name in zombie_mounts:
                log_warning(f"清理空闲超时的挂载点连接: {mount_name} (空闲超过 {timeout}s)")
                self.remove_mount_connection(mount_name, "空闲超时清理")
            
            if zombie_mounts:
                log_info(f"已清理 {len(zombie_mounts)} 个空闲超时连接")
        except Exception as e:
            log_error(f"清理僵尸连接时发生异常: {e}", exc_info=True)
    
    def add_mount_connection(self, mount_name, ip_address, user_agent="", protocol_version="1.0", client_socket=None):
        """添加挂载点连接上传端"""
        with self.mount_lock:
            if mount_name in self.online_mounts:
                log_debug(f"挂载点 {mount_name} 仍在线程表中，可能是相同IP重复连接的清理过程")
               
                del self.online_mounts[mount_name]
                # 重连场景：旧的下游订阅连接必须一并清理，否则 user_count 错位
                self._drop_mount_subscribers(mount_name)
            
            log_debug(f"开始创建挂载点连接 - 名称: {mount_name}, IP: {ip_address}, User-Agent: {user_agent}, 协议版本: {protocol_version}")
            
            # 创建挂载点信息
            mount_info = MountInfo(
                mount_name=mount_name,
                ip_address=ip_address,
                user_agent=user_agent,
                protocol_version=protocol_version,
                client_socket=client_socket
            )
            
            # 添加到在线挂载点表
            self.online_mounts[mount_name] = mount_info
            log_debug(f"挂载点 {mount_name} 已添加到在线列表，当前在线挂载点数量: {len(self.online_mounts)}")
            
            # 生成初始STR表（只改内存字段，仍在锁内）
            self._generate_initial_str(mount_name)

        # 启动STR修正解析流程必须放在锁外：它会创建/停掉解析线程，
        # stop_parser 需要 join 旧线程，持 mount_lock 时源站重连会把所有
        # 鉴权、在线查询、SOURCETABLE 一起堵住（最坏 5 秒）。
        self.start_str_correction(mount_name)

        log_info(f"挂载点 {mount_name} 已上线，IP: {ip_address}当前在线挂载点数量: {len(self.online_mounts)}")
        log_debug(f"挂载点 {mount_name} 连接成功，初始状态: {mount_info.status}, 连接时间: {mount_info.connect_datetime}")
        
        self.print_active_connections()
        
        return True, "Mount point connected successfully"
    
    def remove_mount_connection(self, mount_name, reason="主动断开"):
        """移除挂载点连接（上传端断开）"""
        with self.mount_lock:
            if mount_name in self.online_mounts:
                mount_info = self.online_mounts[mount_name]
                
                # 强制关闭socket
                if mount_info.client_socket:
                    try:
                        mount_info.client_socket.close()
                        log_info(f"已强制关闭挂载点 {mount_name} 的socket连接")
                    except Exception as e:
                        log_warning(f"关闭挂载点 {mount_name} socket连接失败: {e}")
                
                # 记录断开信息
                log_debug(f"挂载点 {mount_name} 已断开 详情: {reason}, 状态: {mount_info.status}, 总字节数: {mount_info.total_bytes}, 数据速率: {mount_info.data_rate:.2f} B/s")
                log_debug(f"挂载点 {mount_name} 统计信息 - 总消息数: {mount_info.total_messages}, 数据包数: {mount_info.data_count}, 空闲时间: {mount_info.idle_time:.1f}秒")
                
                # 判断断开原因 用于调试
                if mount_info.status == "online":
                    actual_reason = reason if reason != "主动断开" else "正常断开"
                else:
                    actual_reason = "异常离线"
                
                del self.online_mounts[mount_name]

                mount_info.rate_samples.clear()
                mount_info.data_rate = 0.0

                # 挂载点（源站）下线时，把仍订阅它的下游连接一并清掉：
                # mount_connection_count 统计的是"下游拉流连接数"，只应由
                # add_user_connection(+1)/remove_user_connection(-1) 管理。
                # 原来在这里直接 pop(mount_name)，会让"在线用户数"（来自 online_users）
                # 与"挂载点详情的人头数"（来自 mount_connection_count）永久错位——
                # 顶部显示有 1 个用户连接，挂载点详情却显示 0。
                # 正确做法是断开下游连接，让 remove_user_connection 把两边计数同步减到 0。
                self._drop_mount_subscribers(mount_name)

                log_info(f"挂载点 {mount_name} 已下线，连接时长: {mount_info.uptime:.1f}秒，原因: {actual_reason}")
                log_debug(f"挂载点 {mount_name} 移除完成，剩余在线挂载点数量: {len(self.online_mounts)}")
                self.print_active_connections()
                
                return True
            else:
                log_debug(f"尝试移除不存在的挂载点: {mount_name}")
                return False
    
    def _drop_mount_subscribers(self, mount_name):
        """断开并清理所有订阅指定挂载点的下游连接。

        源站下线时调用，让 user_connection_count / mount_connection_count 同步归零，
        避免"在线用户数"与"挂载点详情人头数"错位。
        只清理 connection 侧记录；socket 由 forwarder 侧的发送线程自行检测关闭，
        这里只标记 closed 让下游尽快感知断线。socket 的实际 close 交给
        持有它的线程（forwarder.remove_client），记账层不越界关别人的 socket。
        """
        with self.user_lock:
            usernames = list(self.online_users.keys())
            for username in usernames:
                conns = self.online_users.get(username)
                if not conns:
                    continue
                victims = [c for c in conns if c.get('mount_name') == mount_name]
                if not victims:
                    continue
                for conn in victims:
                    conn['closed'] = True
                    self._remove_one(username, conn['connection_id'],
                                     reason=f'挂载点 {mount_name} 下线')
                    log_debug(f"挂载点 {mount_name} 下线，清理下游用户 {username} 的连接")
                if not self.online_users.get(username):
                    self.online_users.pop(username, None)
            self._verify_counts(where='drop_mount_subscribers')
    
    def _generate_initial_str(self, mount_name: str):
        """生成初始STR表"""
        parse_result = {}  
        self._process_str_data(mount_name, parse_result, mode="initial")
    def _apply_data_stats(self, mount_info: MountInfo, data_size: int, now: float):
        """累计字节并刷新"当前速率"（10 秒滑动窗口）

        调用方必须已持有 mount_lock。
        """
        mount_info.last_update = now
        mount_info.last_data_time = now
        mount_info.total_bytes += data_size
        mount_info.data_count += 1

        samples = mount_info.rate_samples
        samples.append((now, data_size))
        horizon = now - 10.0
        while samples and samples[0][0] < horizon:
            samples.popleft()

        window = now - samples[0][0] if samples else 0.0
        # 样本跨度太短（刚连上/刚恢复）时不做除法，避免出现几 MB/s 的虚高尖峰
        if window >= 1.0:
            mount_info.data_rate = sum(size for _, size in samples) / window
        else:
            mount_info.data_rate = float(data_size)

    def _update_message_statistics(self, mount_name: str, parsed_messages, data_size: int) -> bool:
        """更新挂载点基本统计信息"""
        current_time = time.time()

        with self.mount_lock:
            mount_info = self.online_mounts.get(mount_name)
            if mount_info is None:
                log_debug(f"统计更新失败 - 挂载点 {mount_name} 不在线")
                return False

            self._apply_data_stats(mount_info, data_size, current_time)

        return True
    
    def update_mount_data(self, mount_name: str, data_size: int) -> bool:
        """更新挂载点数据统计"""
        if mount_name not in self.online_mounts:
            return False
        
        return self._update_message_statistics(mount_name, None, data_size)
    
    def get_mount_str_data(self, mount_name: str) -> Optional[str]:
        """获取挂载点的STR表数据（本地优先，外部缓存兜底）"""
        with self.mount_lock:
            mount_info = self.online_mounts.get(mount_name)
            if mount_info is not None and mount_info.str_data:
                return mount_info.str_data
        ext = _external_manager()
        if ext:
            for row in ext.get_fused_rows():
                if row['mount'] == mount_name:
                    return row['str_line']
        return None

    def get_all_str_data(self) -> Dict[str, str]:
        """获取所有挂载点的STR表数据（本地 + 外部融合，本地优先）"""
        str_data = {}
        with self.mount_lock:
            for mount_name, mount_info in self.online_mounts.items():
                if mount_info.str_data:
                    str_data[mount_name] = mount_info.str_data
        ext = _external_manager()
        if ext:
            for row in ext.get_fused_rows():
                str_data.setdefault(row['mount'], row['str_line'])
        return str_data

    def get_all_str_data_split(self) -> Dict[str, Dict[str, str]]:
        """按来源分组的 STR 表数据：本机 + 外部（外部不覆盖同名本机）"""
        local: Dict[str, str] = {}
        with self.mount_lock:
            for mount_name, mount_info in self.online_mounts.items():
                if mount_info.str_data:
                    local[mount_name] = mount_info.str_data

        external: Dict[str, str] = {}
        ext = _external_manager()
        if ext:
            for row in ext.get_fused_rows():
                mount_name = row['mount']
                if mount_name in local or mount_name in external:
                    continue
                line = row['str_line']
                if line.split(';')[0].strip() != 'STR':
                    line = ';'.join([
                        'STR', mount_name, 'external', 'RTCM 3', '1', '0',
                        'GPS', '2RTK', 'CHN', str(row['lat'] or 0),
                        str(row['lon'] or 0), '0', '0', 'external', 'N', 'B',
                        'N', '9600', 'external'])
                external[mount_name] = line
        return {'local': local, 'external': external}

    
    # ------------------------------------------------------------------
    # 连接记账：增删的唯一入口
    #
    # 契约（全部调用方都必须遵守）：
    #   * 一条连接 = online_users[username] 里的一个 dict
    #   * 所有计数（user_connection_count / mount_connection_count）都由
    #     online_users **派生**，写操作后即时校验，不再"两份事实各自加减"
    #   * 一次 remove 调用最多移除一条记录，避免"按挂载点整批兜底"打死
    #     同用户的其他健康会话
    #   * 这里不负责关 socket：socket 生命周期归持有它的线程
    #     （ntrip._keep_connection_alive / forwarder 发送线程），
    #     记账层 close 别人的 socket 会让"谁关了这条连接"不可追踪
    # ------------------------------------------------------------------

    def _new_connection_record(self, username, mount_name, ip_address, user_agent,
                               protocol_version, client_socket, connection_id=None):
        """构造一条连接记录（不落表）"""
        now = time.time()
        if not connection_id:
            # 连接 ID 必须唯一：原来只用秒级时间戳，同一秒内的多条连接会撞 ID，
            # 关闭其中一条时按 ID 匹配会误删同用户同挂载点的其他连接。
            connection_id = f"{username}_{mount_name}_{int(now)}_{secrets.token_hex(3)}"
        return {
            'connection_id': connection_id,
            'username': username,
            'mount_name': mount_name,
            'ip_address': ip_address,
            'user_agent': user_agent,
            'protocol_version': protocol_version,
            'connect_time': now,
            'connect_datetime': datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S'),
            'last_activity': now,
            'bytes_sent': 0,
            'closed': False,
            'client_socket': client_socket,
        }

    @staticmethod
    def _peek_socket_state(client_socket):
        """socket 是否还活着

        ★ 必须在未持有 user_lock 时调用：非阻塞 recv 可能阻塞（Windows 下
        send 方向打满时），持锁调用会把所有鉴权线程一起堵住。

        返回 'alive' / 'dead' / 'unknown'。与 ntrip._keep_connection_alive 同法：
        先看 SO_ERROR，再用 MSG_PEEK 看对端是否已发 FIN（正常断开不触发 SO_ERROR）。
        """
        if client_socket is None:
            return 'unknown'
        try:
            if client_socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) != 0:
                return 'dead'
        except (OSError, AttributeError, ValueError):
            return 'dead'
        try:
            if client_socket.recv(1, socket.MSG_PEEK) == b'':
                return 'dead'
        except (BlockingIOError, InterruptedError):
            pass
        except (ConnectionResetError, ConnectionAbortedError):
            return 'dead'
        except (OSError, AttributeError, ValueError):
            return 'dead'
        return 'alive'

    def _plan_prune(self, timeout, now, socket_states):
        """纯函数：算出应该清理哪些记录

        无副作用、不持锁、不碰 socket，便于单测。
        返回 [(username, connection_id, reason), ...]
        """
        plan = []
        for username, conns in self.online_users.items():
            for conn in conns:
                if conn.get('closed'):
                    plan.append((username, conn['connection_id'], '已被标记关闭'))
                    continue
                if socket_states.get(conn['connection_id']) == 'dead':
                    plan.append((username, conn['connection_id'], 'socket 已失效'))
                    continue
                last = conn.get('last_activity') or conn.get('connect_time') or now
                if timeout and timeout > 0 and (now - last) > timeout:
                    idle = now - last
                    plan.append((username, conn['connection_id'], f'空闲 {idle:.0f}s 超过 {timeout}s'))
        return plan

    def prune_stale_connections(self, timeout=None, check_socket=True, reason_tag='prune'):
        """清理幽灵连接（漏删的连接不会自愈，这里是唯一兜底）

        判据两条，任一命中即按**自己的 connection_id** 走正常移除路径：
          1. socket 已失效（SO_ERROR != 0 / MSG_PEEK 读到 EOF）
          2. 空闲时间超过 CONNECTION_IDLE_PRUNE（未配置则用 connection_timeout）
        """
        try:
            if timeout is None:
                timeout = getattr(config, 'CONNECTION_IDLE_PRUNE', 0) \
                    or getattr(config, 'CONNECTION_TIMEOUT', 1800)

            # 第一段：缩小临界区——只抄一份快照出来，socket 探测在锁外做
            with self.user_lock:
                snapshot = [
                    (username, conn['connection_id'], conn.get('client_socket'),
                     conn.get('closed', False),
                     conn.get('last_activity') or conn.get('connect_time') or time.time())
                    for username, conns in self.online_users.items()
                    for conn in conns
                ]

            now = time.time()
            socket_states = {}
            if check_socket and snapshot:
                for _, cid, sock, closed, _ in snapshot:
                    if closed:
                        socket_states[cid] = 'dead'
                        continue
                    socket_states[cid] = self._peek_socket_state(sock)

            with self.user_lock:
                plan = self._plan_prune(timeout, now, socket_states)
                for username, cid, reason in plan:
                    self._remove_one(username, cid, reason=reason)
                if plan:
                    # 记 WARNING 而不是 DEBUG：漏删必须能在日志里数出来
                    log_warning(
                        f"清理 {len(plan)} 条残留连接记录 [{reason_tag}]: "
                        + '; '.join(f"{u}#{c[-6:]}({r})" for u, c, r in plan)
                    )
            self._verify_counts(where=reason_tag)
            return len(plan)
        except Exception as e:
            log_error(f"清理残留连接时发生异常: {e}", exc_info=True)
            return 0

    def _remove_one(self, username, connection_id, reason="断开"):
        """按 connection_id 精确移除一条记录（必须已持有 user_lock）

        返回 True/False。未命中时记 WARNING 并把当前记录数打出来——
        原来这里是静默失败，才导致"日志里没有断开记录、连接数只升不降"。
        """
        conns = self.online_users.get(username)
        if not conns:
            log_warning(f"移除连接未命中: 用户 {username} 无在线记录 "
                        f"(connection_id={connection_id}, 原因: {reason})")
            return False

        for i, conn in enumerate(conns):
            if conn['connection_id'] == connection_id:
                mount = conn['mount_name']
                conn['closed'] = True
                del conns[i]
                # 写路径主动维护缓存：不更新的话 _verify_counts 每次都要"纠正"，
                # 日志里会刷满假的漂移告警，真的漂移反而被淹没。
                if conns:
                    self.user_connection_count[username] = len(conns)
                else:
                    self.online_users.pop(username, None)
                    self.user_connection_count[username] = 0
                left = self.mount_connection_count.get(mount, 0) - 1
                self.mount_connection_count[mount] = max(0, left)
                log_info(f"用户 {username} 已从挂载点 {mount} 断开 ({reason})")
                return True

        log_warning(
            f"移除连接未命中: 用户 {username} connection_id={connection_id} 不存在 "
            f"(原因: {reason}, 该用户当前有 {len(conns)} 条记录: "
            f"{[c['connection_id'][-6:] for c in conns]})"
        )
        return False

    def _verify_counts(self, where=""):
        """计数与 online_users 对账（必须已持有 user_lock）

        两套事实长期必然漂移，所以每次写操作后校验一次，漂了就按派生值纠正并 WARNING。
        """
        users = {name: len(conns) for name, conns in self.online_users.items() if conns}
        mounts = defaultdict(int)
        for conns in self.online_users.values():
            for conn in conns:
                mounts[conn['mount_name']] += 1

        for name, expect in users.items():
            actual = self.user_connection_count.get(name, 0)
            if actual != expect:
                log_warning(f"连接计数漂移已纠正 [{where}] 用户 {name}: {actual} -> {expect}")
                self.user_connection_count[name] = expect
        for name in list(self.user_connection_count.keys()):
            if name not in users:
                if self.user_connection_count[name]:
                    log_warning(f"连接计数漂移已纠正 [{where}] 用户 {name}: "
                                f"{self.user_connection_count[name]} -> 0")
                del self.user_connection_count[name]

        for mount, expect in mounts.items():
            actual = self.mount_connection_count.get(mount, 0)
            if actual != expect:
                log_warning(f"连接计数漂移已纠正 [{where}] 挂载点 {mount}: {actual} -> {expect}")
                self.mount_connection_count[mount] = expect
        for mount in list(self.mount_connection_count.keys()):
            if mount not in mounts:
                if self.mount_connection_count[mount]:
                    log_warning(f"连接计数漂移已纠正 [{where}] 挂载点 {mount}: "
                                f"{self.mount_connection_count[mount]} -> 0")
                del self.mount_connection_count[mount]

    def try_reserve_slot(self, username, mount_name=None, ip_address="", user_agent="",
                         protocol_version="1.0", client_socket=None, limit=None,
                         connection_id=None):
        """限流判定 + 占位，同一把 user_lock 内完成。

        ★ 这是唯一允许的"检查连接数"入口。原来两处鉴权各写一遍
        "读计数 → 比较 → 返回"，读和加之间不在同一把锁里，并发重连可以
        同时通过检查（现场两条并发探测就有一条挤进第 4 条）。

        返回 (True, None, connection_id) 或 (False, reason, None)。
        limit=0 或负数表示不限制。
        """
        try:
            if limit is None:
                limit = getattr(config, 'MAX_CONNECTIONS_PER_USER', 3)

            with self.user_lock:
                # 限流必须基于 prune 后的真实数；socket 探测在锁内做，
                # 但只探测本用户，代价可接受（正常 0~3 条）
                self._prune_locked_unsafe(username)
                current = len(self.online_users.get(username) or [])

                if limit and limit > 0 and current >= limit:
                    log_warning(
                        f"拒绝连接: 用户 {username} 已达上限 "
                        f"({current}/{limit}), 挂载点 {mount_name}, IP {ip_address}"
                    )
                    return False, f"User connection limit exceeded (max: {limit})", None

                record = self._new_connection_record(
                    username, mount_name, ip_address, user_agent,
                    protocol_version, client_socket, connection_id
                )
                self.online_users.setdefault(username, []).append(record)
                # 写路径主动维护缓存，_verify_counts 只用来兜底抓真漂移
                self.user_connection_count[username] = len(self.online_users[username])
                self.mount_connection_count[mount_name] = \
                    self.mount_connection_count.get(mount_name, 0) + 1
                self._verify_counts(where='reserve')
                log_info(f"用户 {username} IP: {ip_address} 已连接，从挂载点 {mount_name} 开始订阅RTCM数据")
                log_debug(f"连接ID生成: {record['connection_id']}, "
                          f"用户 {username} 当前连接数: {current + 1}/{limit or '∞'}")
                return True, None, record['connection_id']
        except Exception as e:
            log_error(f"预留连接名额失败: {e}", exc_info=True)
            return False, "Internal error while reserving connection slot", None

    def _prune_locked_unsafe(self, username):
        """只清理指定用户的失效记录（调用方已持有 user_lock，socket 可能阻塞）

        仅供 try_reserve_slot 用：鉴权路径上不能放大到全表探测。
        超时判据同样生效，但是用"最老的先清"，不按挂载点整批。
        """
        conns = self.online_users.get(username)
        if not conns:
            return
        timeout = getattr(config, 'CONNECTION_IDLE_PRUNE', 0) \
            or getattr(config, 'CONNECTION_TIMEOUT', 1800)
        now = time.time()
        for conn in list(conns):
            cid = conn['connection_id']
            reason = None
            if conn.get('closed'):
                reason = '已被标记关闭'
            elif self._peek_socket_state(conn.get('client_socket')) == 'dead':
                reason = 'socket 已失效'
            elif timeout and timeout > 0 and (now - (conn.get('last_activity') or now)) > timeout:
                reason = f"空闲超过 {timeout}s"
            if reason:
                self._remove_one(username, cid, reason=f'prune:{reason}')
        self._verify_counts(where='reserve-prune')

    def add_user_connection(self, username, mount_name, ip_address, user_agent="", protocol_version="1.0", client_socket=None):
        """添加用户连接（不判限流；限流请用 try_reserve_slot）"""
        ok, _, connection_id = self.try_reserve_slot(
            username, mount_name, ip_address, user_agent,
            protocol_version, client_socket, limit=0
        )
        return connection_id

    def remove_user_connection(self, username, connection_id=None, mount_name=None):
        """移除用户连接（一次调用最多移除一条）

        语义：
          * connection_id 非空 → **只按 id 严格匹配**；未命中记 WARNING，不再
            退化成"按挂载点整批删"，避免一次陈旧 id 把同用户其他会话打死
          * connection_id 为空 + mount_name → 兜底：只删该用户在该挂载点上
            **最老的一条**（漏传 connection_id 的调用方不该一次杀掉全部会话）
          * 两者都为空 → 删该用户最老的一条
        不 close socket（由持有它的线程负责）；只把记录标记 closed=True 并移除。
        """
        with self.user_lock:
            conns = self.online_users.get(username)
            if not conns:
                if connection_id or mount_name:
                    log_debug(f"移除连接跳过: 用户 {username} 已无在线记录 "
                              f"(connection_id={connection_id}, mount={mount_name})")
                return False

            if connection_id:
                removed = self._remove_one(username, connection_id, reason='断开连接')
                self._verify_counts(where='remove')
                return removed

            candidates = [c for c in conns if not mount_name or c['mount_name'] == mount_name]
            if not candidates:
                log_debug(f"移除连接跳过: 用户 {username} 在挂载点 {mount_name} 上无记录")
                return False
            oldest = min(candidates, key=lambda c: c.get('connect_time') or 0)
            removed = self._remove_one(username, oldest['connection_id'],
                                       reason=f'兜底移除(mount={mount_name})')
            self._verify_counts(where='remove-fallback')
            return removed

    def mark_connection_closed(self, username, connection_id):
        """只标记关闭，不移除（供析构/延迟清理用）"""
        with self.user_lock:
            for conn in self.online_users.get(username) or []:
                if conn['connection_id'] == connection_id:
                    conn['closed'] = True
                    return True
            return False
    
    def update_mount_data_stats(self, mount_name, data_size):
        """更新挂载点数据统计

        由源站接收线程逐块调用，必须持锁：原来在锁外先判断再改，
        与 remove_mount_connection 并发时会 KeyError（源站刚断、包还在路上）。
        """
        now = time.time()
        with self.mount_lock:
            mount_info = self.online_mounts.get(mount_name)
            if mount_info is None:
                # 外部挂载点不进 online_mounts（避免占用判定冲突），
                # 统计走 external_caster 的独立同结构字典。
                ext = _external_manager()
                if ext and ext.is_mount_available(mount_name):
                    ext.record_data(mount_name, data_size)
                    return True
                return False
            self._apply_data_stats(mount_info, data_size, now)
            return True
    
    def update_user_activity(self, username, connection_id, bytes_sent=0):
        """更新用户状态"""
        with self.user_lock:
            if username not in self.online_users:
                log_debug(f"用户状态更新失败 - 用户 {username} 不在线")
                return False
            
            connection_found = False
            for conn in self.online_users[username]:
                if conn['connection_id'] == connection_id:
                    old_bytes = conn['bytes_sent']
                    conn['last_activity'] = time.time()
                    conn['bytes_sent'] += bytes_sent
                    connection_found = True
                    
                    # 移除频繁的用户活动更新debug日志，避免刷屏
                    # if bytes_sent > 0:
                    #     log_debug(f"用户 {username} 信息更新: 连接ID={connection_id}, 本次发送={bytes_sent}B, 累计发送={conn['bytes_sent']}B (原{old_bytes}B), 挂载点={conn['mount_name']}")
                    break
            
            if not connection_found:
                log_debug(f"用户状态更新失败 - 连接ID {connection_id} 不存在于用户 {username}")
                return False
            
            return True
    
    def is_mount_online(self, mount_name):
        """检查本地源站是否在线（上传侧占用判定专用，不要改成 available）"""
        with self.mount_lock:
            return mount_name in self.online_mounts

    def is_mount_available(self, mount_name):
        """消费侧可用判定：本地源在线 OR 外部缓存存在"""
        with self.mount_lock:
            if mount_name in self.online_mounts:
                return True
        ext = _external_manager()
        return bool(ext and ext.is_mount_available(mount_name))

    def get_user_connection_count(self, username, prune=False):
        """获取用户连接数

        prune=True 时先清理该用户的失效记录再返回（前端展示用，保证数字可信）。
        鉴权路径不要用这个再自己判上限——那条路必须走 try_reserve_slot，
        否则"检查"和"占位"不是原子的。
        """
        if prune:
            self.prune_stale_connections_for(username)
        with self.user_lock:
            conns = self.online_users.get(username)
            if conns is not None:
                # 以 online_users 为准（派生事实），字典仅作缓存
                return len(conns)
            return 0

    def prune_stale_connections_for(self, username):
        """单个用户的残留清理（前端/后台调用，不在鉴权热路径上）"""
        with self.user_lock:
            if username not in self.online_users:
                return 0
        return self.prune_stale_connections(reason_tag=f'user:{username}')

    def get_user_connect_time(self, username):
        """获取用户最新连接时间"""
        with self.user_lock:
            if username in self.online_users and self.online_users[username]:
                
                latest_connection = max(self.online_users[username], key=lambda x: x['connect_time'])
                return latest_connection['connect_datetime']
            return None
    
    def list_user_connections(self, username):
        """列出某用户的在线连接明细（不含 socket），供后台排查幽灵连接"""
        with self.user_lock:
            conns = self.online_users.get(username) or []
            return [
                {
                    'connection_id': c['connection_id'],
                    'short_id': c['connection_id'][-6:],
                    'mount_name': c['mount_name'],
                    'ip_address': c['ip_address'],
                    'connect_time': c['connect_time'],
                    'connect_datetime': c.get('connect_datetime'),
                    'last_activity': c.get('last_activity'),
                    'idle_seconds': round(time.time() - (c.get('last_activity') or time.time()), 1),
                    'bytes_sent': c.get('bytes_sent', 0),
                    'closed': bool(c.get('closed')),
                }
                for c in conns
            ]

    def reconcile(self):
        """全局巡检：prune + 与转发器对账

        返回 {'pruned': n, 'orphans': [...], 'dangling': [...]}
          orphans  —— 记账里有、forwarder 里没有该 connection_id 的记录
          dangling —— forwarder 里有、记账里没有的 connection_id
        两者都记 WARNING，便于人工定位"谁漏了清理"。
        """
        pruned = self.prune_stale_connections(reason_tag='reconcile')
        orphans, dangling = [], []
        try:
            from . import forwarder as forwarder_mod
            fwd = forwarder_mod.get_forwarder()
            with fwd.client_lock:
                live_ids = {
                    c.get('connection_id')
                    for clients in fwd.clients.values() for c in clients
                    if c.get('connection_id')
                }
            with self.user_lock:
                known_ids = set()
                for username, conns in self.online_users.items():
                    for conn in conns:
                        cid = conn['connection_id']
                        known_ids.add(cid)
                        # 刚建记录时 forwarder 还没注册，2 秒内不算孤儿
                        if cid not in live_ids and (time.time() - conn['connect_time']) > 2.0:
                            orphans.append(f"{username}#{cid[-6:]}")
                dangling = [cid for cid in live_ids if cid not in known_ids]
        except Exception as e:
            log_warning(f"与转发器对账失败: {e}")

        if orphans:
            log_warning(f"发现 {len(orphans)} 条记账孤儿（forwarder 无对应客户端）: {orphans}")
        if dangling:
            log_warning(f"发现 {len(dangling)} 条转发器悬挂连接（记账无对应记录）: "
                        f"{[c[-6:] for c in dangling]}")
        return {'pruned': pruned, 'orphans': orphans, 'dangling': dangling}
    
    def get_mount_connection_count(self, mount_name):
        """获取挂载点连接数（从 online_users 派生，避免缓存漂移）"""
        with self.user_lock:
            return sum(
                1 for conns in self.online_users.values()
                for conn in conns if conn['mount_name'] == mount_name
            )
    
    def get_online_mounts(self):
        """获取在线挂载点列表"""
        with self.mount_lock:
            return {name: info.to_dict() for name, info in self.online_mounts.items()}
    
    def get_online_users(self):
        """获取在线用户列表

        剔除 socket 对象：返回结果会被直接 emit 到前端，只要混进一个不可序列化的
        对象，整条 online_users_update 就会失败（前端列表永远不更新）。
        """
        with self.user_lock:
            return {
                name: [{k: v for k, v in conn.items() if k != 'client_socket'} for conn in conns]
                for name, conns in self.online_users.items()
            }

    def get_online_signature(self):
        """轻量签名，用于判断"上下线有没有变化"

        推送循环每 0.5 秒比对一次。签名只取名字和连接数，不构造 to_dict()，
        3000 个挂载点下比每次全量深拷贝便宜两个数量级。
        """
        with self.mount_lock:
            mounts = tuple(sorted(self.online_mounts.keys()))
        with self.user_lock:
            users = tuple(sorted((name, len(conns)) for name, conns in self.online_users.items()))
        return mounts, users
    
    def get_mount_info(self, mount_name):
        """获取挂载点信息（持锁：字典可能被另一个线程并发增删）"""
        with self.mount_lock:
            mount_info = self.online_mounts.get(mount_name)
            return mount_info.to_dict() if mount_info is not None else None
    
    def get_user_connections(self, username):
        """获取用户连接信息"""
        return self.online_users.get(username, [])
    
    def get_mount_statistics(self, mount_name: str) -> Optional[Dict[str, Any]]:
        """获取挂载点统计信息（外部源字段对齐）"""
        with self.mount_lock:
            mount_info = self.online_mounts.get(mount_name)
            if mount_info is None:
                ext = _external_manager()
                stats = ext.get_stats(mount_name) if ext else None
                if not stats:
                    return None
                return {
                    'mount_name': mount_name,
                    'status': 'external',
                    'source_type': 'external',
                    'uptime': 0,
                    'data_count': stats['data_count'],
                    'total_bytes': stats['total_bytes'],
                    'total_messages': 0,
                    'data_rate': stats['data_rate'],
                    'user_count': 0,
                    'str_generated': True,
                }
            
            return {
                'mount_name': mount_name,
                'status': mount_info.status,
                'source_type': 'local',
                'uptime': mount_info.uptime,
                'total_bytes': mount_info.total_bytes,
                'data_rate': mount_info.data_rate,
                'data_count': mount_info.data_count
            }
    
    def generate_mount_list(self):
        """生成挂载点列表数据（本地 + 外部，本地优先）"""
        mount_list = []
        ext = _external_manager()
        external_rows = ext.get_fused_rows() if ext else []

        with self.mount_lock:
            for mount_name, mount_info in self.online_mounts.items():
               
                if mount_info.str_data:
                    mount_list.append(mount_info.str_data)

                else:
                    # 生成默认的NTRIP格式信息
                    mount_data = [
                        'STR',
                        mount_name, #挂载点名称
                        'none',  # 城市名称或其他描述 默认none
                        'RTCM 3.3',  # format
                        '1005(10)',  # format_details
                        '0',  # carrier
                        'GPS',  # nav_system
                        '2RTK',  # network
                        'CHN',  # country
                        str(mount_info.lat) if mount_info.lat is not None else '0.0',  # latitude
                        str(mount_info.lon) if mount_info.lon is not None else '0.0',  # longitude
                        '0',  # nmea
                        '0',  # solution
                        mount_info.user_agent or 'unknown',  # generator
                        'N',  # compression
                        'B',  # authentication
                        'N',  # fee
                        '500',  # bitrate
                        'NO'  # misc
                    ]
                    mount_info_str = ';'.join(mount_data)
                    mount_list.append(mount_info_str)
                    log_info(f"已为挂载点 {mount_name} 创建STR: {mount_info_str}", 'connection_manager')

        # 外部挂载点按缓存原样补齐；同名已被本地占用的跳过（本地永远优先）
        local_names = set()
        for line in mount_list:
            parts = line.split(';')
            if len(parts) > 1:
                local_names.add(parts[1].strip())
        for row in external_rows:
            if row['mount'] in local_names:
                continue
            line = row['str_line']
            mount_list.append(line if line.split(';')[0].strip() == 'STR'
                               else ';'.join(['STR', row['mount'], 'external', 'RTCM 3',
                                              '1', '0', 'GPS', '2RTK', 'CHN',
                                              str(row['lat'] or 0), str(row['lon'] or 0),
                                              '0', '0', 'external', 'N', 'B', 'N',
                                              '9600', 'external']))
            local_names.add(row['mount'])
        return mount_list
    
    def get_statistics(self):
        """获取总体统计信息

        口径说明（前端"用户数"曾经对不上就出在这里）：
        - total_users       在线**账号**数（同一账号多条连接只算一个人）
        - total_connections 在线**连接**数（每条 NTRIP 拉流连接算一条）
        - users             逐连接的明细，长度等于 total_connections
        """
        with self.mount_lock, self.user_lock:
            ext = _external_manager()
            fused_rows = ext.get_fused_rows() if ext else []
            # 「在线」只算本机在线挂载点 + 外部正在出数据的 relay。
            # 外部 STR 缓存是「可用」列表，不能当成在线，否则刷新瞬间会掉到 0。
            active_external = 0
            if ext:
                now = time.time()
                fresh_seconds = max(30, int(getattr(
                    config, 'EXTERNAL_READ_TIMEOUT', 20) or 20))
                for state in ext.get_relay_status().values():
                    last_data = state.get('last_data_at') or 0
                    if (state.get('state') == 'running'
                            and now - last_data <= fresh_seconds):
                        active_external += 1
            local_online = len(self.online_mounts)
            total_mounts = local_online + active_external
            # 本机已占用的同名挂载点，外部缓存里不再计入「可用」与 mounts 列表，
            # 否则首页 external_available 会比外部分组多出一条 2RTK（本地优先口径
            # 与 generate_mount_list / get_all_str_data_split 保持一致）。
            local_names = set(self.online_mounts.keys())
            external_fused = [row for row in fused_rows
                              if row['mount'] not in local_names]
            external_available = len(external_fused)
            total_users = len(self.online_users)
            total_connections = sum(len(connections) for connections in self.online_users.values())

            # 挂载点用户数直接从 online_users 派生，不再读 mount_connection_count 缓存
            users_per_mount = defaultdict(int)
            for connections in self.online_users.values():
                for conn in connections:
                    users_per_mount[conn['mount_name']] += 1
            
            mount_stats = []
            for mount_name, mount_info in self.online_mounts.items():
                mount_stats.append({
                    'mount_name': mount_name,
                    'ip_address': mount_info.ip_address,
                    'uptime': mount_info.uptime,
                    'data_count': mount_info.data_count,
                    'total_bytes': mount_info.total_bytes,
                    'total_messages': mount_info.total_messages,
                    'data_rate': mount_info.data_rate,
                    'user_count': users_per_mount.get(mount_name, 0),
                    'status': mount_info.status,
                    'source_type': 'local',
                    'str_generated': mount_info.final_str_generated
                })

            # 外部挂载点单独存、字段对齐；不进 online_mounts。
            # 同名本机挂载点已在上面加入 local 行，这里跳过，避免首页分组重复。
            if ext and external_fused:
                for row in external_fused:
                    mount_name = row['mount']
                    stats = ext.get_stats(mount_name) or {}
                    users_here = users_per_mount.get(mount_name, 0)
                    mount_stats.append({
                        'mount_name': mount_name,
                        'ip_address': f'external:{row["source_name"]}',
                        'uptime': 0,
                        'data_count': stats.get('data_count', 0),
                        'total_bytes': stats.get('total_bytes', 0),
                        'total_messages': 0,
                        'data_rate': stats.get('data_rate', 0.0),
                        'user_count': users_here,
                        'status': 'external',
                        'source_type': 'external',
                        'source_name': row['source_name'],
                        'str_generated': True,
                    })
            
            user_stats = []
            for username, connections in self.online_users.items():
                for conn in connections:
                    user_stats.append({
                        'username': username,
                        'mount_name': conn['mount_name'],
                        'ip_address': conn['ip_address'],
                        'connect_time': conn['connect_time'],
                        'bytes_sent': conn['bytes_sent']
                    })
            
            return {
                'total_mounts': total_mounts,
                'local_online_mounts': local_online,
                'external_relay_mounts': active_external,
                'external_available_mounts': external_available,
                'total_users': total_users,
                'total_connections': total_connections,
                'mounts': mount_stats,
                'users': user_stats
            }
    
    def start_str_correction(self, mount_name: str):
        """启动30秒RTCM解析并修正STR"""
        if mount_name not in self.online_mounts:
            log_warning(f"无法启动STR修正，挂载点 {mount_name} 不在线")
            return

        success = rtcm_manager.start_parser(
            mount_name=mount_name,
            mode="str_fix",
            duration=30
        )
        
        if not success:
            log_error(f"启动STR修正解析失败 [挂载点: {mount_name}]")
            return
            
        log_info(f"已启动STR修正解析 [挂载点: {mount_name}]，将在30秒后修正STR表")
        
        
        def wait_and_correct():
            log_debug(f"开始等待STR修正完成 [挂载点: {mount_name}]")
            time.sleep(35)  
            log_debug(f"等待完成，开始获取解析结果 [挂载点: {mount_name}]")
            
            parse_result = rtcm_manager.get_result(mount_name)
            log_debug(f"获取到解析结果 [挂载点: {mount_name}]: {parse_result is not None}")
            
            if parse_result:
                log_debug(f"解析结果内容 [挂载点: {mount_name}]: {parse_result}")
                
                self._process_str_data(mount_name, parse_result, mode="correct")
            else:
                log_warning(f"未获取到STR修正解析结果 [挂载点: {mount_name}]")
                log_debug(f"STR修正失败 - 挂载点: {mount_name}, 可能原因: 解析超时、数据不足或解析器异常")
            
            
            log_debug(f"停止解析器 [挂载点: {mount_name}]")
            rtcm_manager.stop_parser(mount_name)
            log_debug(f"STR修正流程完成 [挂载点: {mount_name}]")
        
        threading.Thread(target=wait_and_correct, daemon=True).start()

    def _process_str_data(self, mount_name: str, parse_result: dict, mode: str = "correct"):
        """统一的STR处理函数：支持初始生成、修正和重新生成模式
        
        Args:
            mount_name: 挂载点名称
            parse_result: 解析结果字典
            mode: 处理模式 - "initial"(初始生成), "correct"(修正), "regenerate"(重新生成)
        """
        log_debug(f"开始STR处理 [挂载点: {mount_name}, 模式: {mode}]")
        log_debug(f"解析结果详情: {parse_result}")
        
        with self.mount_lock:
           
            if mount_name not in self.online_mounts:
                log_debug(f"挂载点 {mount_name} 不在线，无法处理STR")
                return
            
            mount_info = self.online_mounts[mount_name]
            original_str = mount_info.str_data
            
            if mode == "initial":
                
                str_parts = self._create_initial_str_parts(mount_name, parse_result)
            elif mode in ["correct", "regenerate"]:
                
                if not original_str:
                    log_warning(f"挂载点 {mount_name} 无初始STR数据，切换到初始生成模式")
                    str_parts = self._create_initial_str_parts(mount_name, parse_result)
                else:
                    log_debug(f"原始STR [挂载点: {mount_name}]: {original_str}")
                    str_parts = original_str.split(';')
                    if len(str_parts) < 19:
                        log_error(f"STR格式错误，无法处理 [挂载点: {mount_name}] - 字段数量: {len(str_parts)}, 期望: 19")
                        return
                    
                    self._update_str_fields(str_parts, parse_result, mode)
            else:
                log_error(f"未知的STR处理模式: {mode}")
                return
            
            processed_str = ";".join(str_parts)
            log_debug(f"处理后STR [挂载点: {mount_name}]: {processed_str}")
           
            
            mount_info.str_data = processed_str
            if mode == "initial":
                mount_info.initial_str_generated = True
            else:
                mount_info.final_str_generated = True
            
            if mode == "correct":
                if original_str != processed_str:
                    log_info(f"{mount_name}已修正STR: {processed_str}")

                else:
                    log_info(f"STR表修正完成 [挂载点: {mount_name}]，无需更新")
                    log_info(f"当前STR: {processed_str}")
            elif mode == "initial":
                log_info(f"[挂载点: {mount_name}]STR已生成: {processed_str}")
            
            log_debug(f"STR处理流程结束 [挂载点: {mount_name}]，模式: {mode}, 最终状态: final_str_generated={mount_info.final_str_generated}")
    
    
    def _create_initial_str_parts(self, mount_name: str, parse_result: dict) -> list:
        """创建初始STR字段列表"""
        from . import config
        
        mount_info = self.online_mounts[mount_name]
        app_author = config.APP_AUTHOR.replace(' ', '') if config.APP_AUTHOR else '2rtk'
        
        identifier = parse_result.get("city") or mount_info.city or "none"
        country_code = parse_result.get("country") or mount_info.country or config.CASTER_COUNTRY
        latitude = parse_result.get("lat") or config.CASTER_LATITUDE
        longitude = parse_result.get("lon") or config.CASTER_LONGITUDE

        str_parts = [
            "STR",                          # 0: type
            mount_name,                     # 1: mountpoint
            identifier,                     # 2: identifier
            "RTCM3.x",                     # 3: format
            parse_result.get("message_types_str", "1005"),  # 4: format-details
            "0",                           # 5: 这里我不在使用RTCM标准，使用统计后的频段信息
            parse_result.get("gnss_combined", "GPS"),       # 6: nav-system
            app_author,                     # 7: network
            country_code,                   # 8: country
            f"{latitude:.4f}",             # 9: latitude
            f"{longitude:.4f}",            # 10: longitude
            "0",                           # 11: nmea
            "0",                           # 12: solution
            "2RTK_NtirpCaster",           # 13: generator
            "N",                           # 14: compression
            "B",                           # 15: authentication
            "N",                           # 16: fee
            "500",                         # 17: bitrate (这里RTCM.py中的计算方式有问题，后续要修改)
            "NO"                           # 18: misc
        ]
        
        self._update_str_fields(str_parts, parse_result, "initial")
        
        return str_parts
    
    def _update_str_fields(self, str_parts: list, parse_result: dict, mode: str = "correct"):
        """根据解析结果更新STR字段
        
        Args:
            str_parts: STR字段列表
            parse_result: 解析结果字典
            mode: 处理模式 - "initial"(初始生成), "correct"(修正), "regenerate"(重新生成)
        """
       
        if parse_result.get("city"):
            str_parts[2] = parse_result["city"]
        
       
        if parse_result.get("message_types_str"):
            str_parts[4] = parse_result["message_types_str"]
        
        
        if parse_result.get("carrier_combined"):
            carrier_info = parse_result["carrier_combined"]
            
            str_parts[5] = carrier_info  # 直接填入载波相位信息，如L1、L1+L5+B1等.不遵守RTCM标准.
            #标准的STR格式参考 https://software.rtcm-ntrip.org/wiki/STR
        
        # 4. 更新nav-system字段（第7个字段，导航系统）
        if parse_result.get("gnss_combined"):
            str_parts[6] = parse_result["gnss_combined"]
        
        # 5. 更新country字段（第9个字段，国家代码）
        if parse_result.get("country"):
            # rtcm2.py已经进行了2字符到3字符的转换，直接使用
            str_parts[8] = parse_result["country"]
        
        # 6. （第10个字段，纬度）
        if parse_result.get("lat"):
            str_parts[9] = f"{parse_result['lat']:.4f}"
        
        # 7. （第11个字段，经度）
        if parse_result.get("lon"):
            str_parts[10] = f"{parse_result['lon']:.4f}"
        
        # 8. （第14个字段）
        str_parts[13] = "2RTK_NtirpCaster"
        
        # 9. （第17个字段）
        str_parts[16] = "N"
        
        # 10.（第18个字段，比特率）
        if parse_result.get("bitrate"):
            bitrate_bps = parse_result["bitrate"]  
            str_parts[17] = str(int(bitrate_bps))  
        
        # 11. STR表最后一个字段.改为yes或no 用来判断STR是否修正
        if mode == "initial":
            str_parts[-1] = "NO"  # 初始生成时标记为未校验
        else:  # correct 或 regenerate 模式
            str_parts[-1] = "YES"  # 修正后标记为已校验

    def check_mount_exists(self, mount_name: str) -> bool:

        return mount_name in self.online_mounts
    

_connection_manager = None
_manager_lock = Lock()

#*****************************
# 连接管理 后期扩展使用 API管理 连接状态
#*****************************

def _external_manager():
    """延迟导入外部 caster 管理器，避免模块间循环导入"""
    if not config.EXTERNAL_CASTER_ENABLED:
        return None
    from . import external_caster
    return external_caster.get_manager()


def get_connection_manager():
    """获取全局连接管理器实例"""
    global _connection_manager
    if _connection_manager is None:
        with _manager_lock:
            if _connection_manager is None:
                _connection_manager = ConnectionManager()
    return _connection_manager

def add_mount_connection(mount_name, ip_address, user_agent="", protocol_version="1.0"):
    """添加挂载点连接"""
    return get_connection_manager().add_mount_connection(mount_name, ip_address, user_agent, protocol_version)

def remove_mount_connection(mount_name):
    """移除挂载点连接"""
    return get_connection_manager().remove_mount_connection(mount_name)

def add_user_connection(username, mount_name, ip_address, user_agent="", protocol_version="1.0", client_socket=None):
    """添加用户连接"""
    return get_connection_manager().add_user_connection(username, mount_name, ip_address, user_agent, protocol_version, client_socket)

def remove_user_connection(username, connection_id=None, mount_name=None):
    """移除用户连接"""
    return get_connection_manager().remove_user_connection(username, connection_id, mount_name)

def update_user_activity(username, connection_id, bytes_sent=0):
    """更新用户活动"""
    return get_connection_manager().update_user_activity(username, connection_id, bytes_sent)

def is_mount_online(mount_name):
    """检查本地源站是否在线（上传侧）"""
    return get_connection_manager().is_mount_online(mount_name)

def is_mount_available(mount_name):
    """消费侧可用判定：本地在线 OR 外部缓存可用"""
    return get_connection_manager().is_mount_available(mount_name)

def get_user_connection_count(username, prune=False):
    """获取用户连接数"""
    return get_connection_manager().get_user_connection_count(username, prune=prune)

def try_reserve_slot(username, mount_name=None, ip_address="", user_agent="",
                     protocol_version="1.0", client_socket=None, limit=None,
                     connection_id=None):
    """限流判定 + 占位（原子）"""
    return get_connection_manager().try_reserve_slot(
        username, mount_name, ip_address, user_agent,
        protocol_version, client_socket, limit, connection_id
    )

def prune_stale_connections(timeout=None, check_socket=True, reason_tag='prune'):
    """清理残留（幽灵）连接记录"""
    return get_connection_manager().prune_stale_connections(timeout, check_socket, reason_tag)

def list_user_connections(username):
    """列出某用户在线连接明细"""
    return get_connection_manager().list_user_connections(username)

def reconcile():
    """全局巡检：prune + 与转发器对账"""
    return get_connection_manager().reconcile()

def update_mount_data(mount_name, data_size):
    """更新挂载点数据"""
    return get_connection_manager().update_mount_data(mount_name, data_size)

def update_mount_data_stats(mount_name, data_size):
    """更新挂载点数据统计"""
    return get_connection_manager().update_mount_data_stats(mount_name, data_size)

def get_mount_str_data(mount_name):
    """获取挂载点 STR（本地优先，外部缓存兜底）"""
    return get_connection_manager().get_mount_str_data(mount_name)

def get_all_str_data():
    """获取全部 STR（本地 + 外部融合）"""
    return get_connection_manager().get_all_str_data()

def get_all_str_data_split():
    """获取按来源分组的 STR（本机 / 外部）"""
    return get_connection_manager().get_all_str_data_split()

def get_statistics():
    """获取统计信息"""
    return get_connection_manager().get_statistics()

def get_mount_statistics(mount_name):
    """获取挂载点统计信息"""
    return get_connection_manager().get_mount_statistics(mount_name)

def generate_mount_list():
    """生成挂载点列表数据"""
    return get_connection_manager().generate_mount_list()

def check_mount_exists(mount_name):
    """检查挂载点是否存在"""
    return get_connection_manager().check_mount_exists(mount_name)
