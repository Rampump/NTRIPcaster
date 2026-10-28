#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import socket
import queue
import threading
from collections import deque
from itertools import islice
from threading import Lock, RLock
from . import config
from . import logger
from . import connection

class RingBuffer:
    """环形缓冲区

    同时受两个上限约束：
      - max_age 秒的时间窗口（配置里 ring_buffer_size 的本意，避免只留 1~2 秒历史）
      - max_chunks 硬上限（防止上游突发小包把内存打满）
    """
    def __init__(self, maxlen=None, max_age=None):
        self.maxlen = maxlen or getattr(config, 'RING_BUFFER_MAX_CHUNKS', 3000)
        self.max_age = max_age if max_age is not None else config.RING_BUFFER_SIZE
        self.buffer = deque(maxlen=self.maxlen)
        self.lock = Lock()
        self.total_bytes = 0
        self.total_messages = 0
        self.last_timestamp = time.time()
        self._write_index = 0
        self._read_index = 0
        
    def append(self, data, timestamp=None):
        """添加数据到环形缓冲区"""
        if timestamp is None:
            timestamp = time.time()
            
        with self.lock:
            item = {
                'data': data,
                'timestamp': timestamp,
                'size': len(data),
                'index': self._write_index
            }
            self.buffer.append(item)
            # 按时间窗口淘汰过期数据
            if self.max_age and self.max_age > 0:
                while self.buffer and timestamp - self.buffer[0]['timestamp'] > self.max_age:
                    self.buffer.popleft()
            self.total_bytes += len(data)
            self.total_messages += 1
            self.last_timestamp = timestamp
            self._write_index += 1
                
    def current_index(self):
        """当前写位置（下一个待分配序号）"""
        with self.lock:
            return self._write_index
    
    def get_since_index(self, cursor):
        """按写入序号返回增量数据

        返回 ([(timestamp, data), ...], new_cursor)。
        复杂度只与新增块数相关：deque 不支持切片，原来的 `list(buffer)[offset:]`
        每轮都要把整个 3000 块的缓冲拷一遍（每 10ms 一次），是纯浪费。
        新增块一定在尾部，所以从尾部反向取 N 个再翻回来即可。
        """
        with self.lock:
            if not self.buffer:
                return [], self._write_index
            
            latest = self._write_index
            if cursor is None:
                cursor = latest
            if cursor >= latest:
                return [], latest
            
            oldest = self.buffer[0]['index']
            start = cursor if cursor > oldest else oldest
            # 需要返回的块数（可能因 max_age/maxlen 淘汰而少于逻辑增量）
            take = latest - start
            if take <= 0:
                return [], latest
            if take > len(self.buffer):
                take = len(self.buffer)

            items = list(islice(reversed(self.buffer), take))
            items.reverse()
            return [(it['timestamp'], it['data']) for it in items], latest
    def get_since(self, timestamp):
        """获取指定时间戳之后的数据"""
        with self.lock:
            if not self.buffer:
                return []
            
            result = []
            for item in self.buffer:
                if item['timestamp'] > timestamp:
                    result.append((item['timestamp'], item['data']))
            
            return result
    
    def get_latest(self, count=None):
        """获取最新的数据"""
        with self.lock:
            if not self.buffer:
                return []
            
            if count is None:
                return [(item['timestamp'], item['data']) for item in self.buffer]
            else:
                items = list(self.buffer)[-count:] if count > 0 else []
                return [(item['timestamp'], item['data']) for item in items]
    
    def get_range(self, start_index, end_index=None):
        """根据索引范围获取数据"""
        with self.lock:
            if not self.buffer:
                return []
            
            result = []
            for item in self.buffer:
                if item['index'] >= start_index:
                    if end_index is None or item['index'] <= end_index:
                        result.append((item['timestamp'], item['data']))
            
            return result
    
    def get_stats(self):
        """获取缓冲区统计信息"""
        with self.lock:
            return {
                'size': len(self.buffer),
                'max_size': self.maxlen,
                'total_bytes': self.total_bytes,
                'total_messages': self.total_messages,
                'last_update': self.last_timestamp,
                'usage_percent': (len(self.buffer) / self.maxlen) * 100 if self.maxlen > 0 else 0,
                'write_index': self._write_index,
                'read_index': self._read_index
            }
    
    def clear(self):
        """清空缓冲区"""
        with self.lock:
            self.buffer.clear()
            self.total_bytes = 0
            self.total_messages = 0
            self._write_index = 0
            self._read_index = 0
    
    def is_full(self):
        """检查缓冲区是否已满"""
        with self.lock:
            return len(self.buffer) >= self.maxlen
    
    def is_empty(self):
        """检查缓冲区是否为空"""
        with self.lock:
            return len(self.buffer) == 0

class SimpleDataForwarder:
    """简化的数据广播"""
    
    def __init__(self, buffer_maxlen=None, broadcast_interval=None):
        self.buffer_maxlen = buffer_maxlen or getattr(config, 'RING_BUFFER_MAX_CHUNKS', 3000)
        self.buffer_max_age = config.RING_BUFFER_SIZE
        self.broadcast_interval = broadcast_interval or config.BROADCAST_INTERVAL
        
        self.mount_buffers = {}  # {mount_name: RingBuffer}
        self.buffer_lock = RLock()
        self.mount_cursors = {}  # {mount_name: 已广播到的写序号}，每轮只切片一次供所有客户端共享
        
        self.clients = {}  # {mount_name: [client_info]}
        self.client_lock = RLock()
        
        self.subscribers = {}  # {mount_name: [socket_write_end]}
        self.subscriber_lock = RLock()
        
        self.broadcast_thread = None
        self.sender_threads = []
        self.send_queue = queue.Queue()  # 待发送客户端通知队列（无界，客户端条目会被去重）
        self.sender_count = max(1, int(getattr(config, 'SENDER_THREADS', 8) or 8))
        self.client_queue_size = max(1, int(getattr(config, 'CLIENT_SEND_QUEUE_SIZE', 200) or 200))
        # 单客户端积压字节上限：超过就跳到最新，防止慢客户端吃光内存
        self.pending_max_bytes = max(64 * 1024, int(getattr(config, 'CLIENT_PENDING_MAX_BYTES', 1024 * 1024) or 1024 * 1024))
        # 积压停滞多久后放弃（秒）
        self.stall_timeout = max(5, int(getattr(config, 'DATA_SEND_TIMEOUT', 5) or 5) * 4)
        # 内核发送缓冲满后的重试退避（秒）
        self.retry_interval = 0.02
        self.running = False
        
        self.stats = {
            'total_clients': 0,
            'active_clients': 0,
            'total_bytes_sent': 0,
            'total_messages_sent': 0,
            'failed_sends': 0,
            'dropped_bytes': 0,
            'disconnected_clients': 0
        }
    
    def start(self):
        """启动广播线程与发送线程池"""
        if self.running:
            return
            
        self.running = True
        self.broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True,
                                                 name='forwarder-broadcast')
        self.broadcast_thread.start()

        self.sender_threads = []
        for i in range(self.sender_count):
            t = threading.Thread(target=self._sender_loop, daemon=True, name=f'forwarder-sender-{i}')
            t.start()
            self.sender_threads.append(t)

        logger.log_system_event(f'数据转发器已启动（发送线程 {len(self.sender_threads)} 个）')
    
    def stop(self):
        """停止广播线程与发送线程，并关闭所有客户端连接"""
        self.running = False
        
        if self.broadcast_thread and self.broadcast_thread.is_alive():
            self.broadcast_thread.join(timeout=5)

        # 先关 socket：发送线程可能正阻塞在 sendall 上（慢客户端，最长 DATA_SEND_TIMEOUT），
        # 关掉句柄能让它立即抛错返回，不用干等超时
        with self.client_lock:
            for mount_clients in self.clients.values():
                for client_info in mount_clients[:]:
                    client_info['closed'] = True
                    self._close_client(client_info)

        # 再发停止哨兵并回收发送线程
        for _ in range(len(self.sender_threads)):
            self.send_queue.put(None)
        for t in self.sender_threads:
            if t.is_alive():
                t.join(timeout=2)
        self.sender_threads = []
                    
        logger.log_system_event('数据转发器已停止')
    
    def add_client(self, client_socket, user, mount, agent, addr, protocol_version, connection_id=None):
        """添加客户端连接（同步方式）"""
        try:
            # 启用TCP Keep-Alive
            self._enable_keepalive(client_socket)
            
            client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # 非阻塞发送：send() 只写"内核还能吃多少"，写不完的留在 pending 里下一轮继续，
            # 发送线程永远不会卡在某个慢客户端上（原来 settimeout 最长会阻塞 5 秒）。
            # 注意：add_client 之后 NTRIP 处理线程不再对这个 socket 做 send/recv。
            try:
                client_socket.setblocking(False)
            except OSError:
                pass

            current_time = time.time()
            # 记录加入时刻的缓冲写位置：首轮只补这一刻之后的数据
            try:
                with self.buffer_lock:
                    _buf = self.mount_buffers.get(mount)
                    join_index = _buf.current_index() if _buf is not None else 0
            except Exception:
                join_index = 0

            client_info = {
                'socket': client_socket,
                'user': user,
                'mount': mount,
                'agent': agent,
                'addr': addr,
                'protocol_version': protocol_version,
                'connection_id': connection_id,
                'connected_at': current_time,
                'last_seen': current_time,
                'last_sent_timestamp': current_time,  
                'cursor': None,
                'initialized': False,
                'join_index': join_index,
                # 发送队列：广播线程只入队，发送线程池负责写 socket，
                # 慢客户端不会传导到广播线程和其他客户端
                'outbox': deque(),
                'outbox_lock': Lock(),
                'queued': False,
                'pending': bytearray(),      # 已取出但尚未写进内核的数据
                'pending_messages': 0,
                'pending_last_ts': None,
                'pending_since': None,
                'retry_at': 0.0,
                'dropped_bytes': 0,
                'closed': False,
                'bytes_sent': 0,
                'messages_sent': 0,
                'send_errors': 0
            }
            
            with self.client_lock:
                # 限制同用户同挂载点的连接数
                if mount not in self.clients:
                    self.clients[mount] = []
                
                user_connections = [c for c in self.clients[mount] if c['user'] == user]
                if len(user_connections) >= config.MAX_USERS_PER_MOUNT:
                    
                    oldest = min(user_connections, key=lambda x: x['connected_at'])
                    self.remove_client(oldest)
                
                self.clients[mount].append(client_info)
                
                self.stats['total_clients'] += 1
                self.stats['active_clients'] = sum(len(clients) for clients in self.clients.values())
            
            logger.log_client_connect(user, mount, addr[0], protocol_version)
            return client_info
            
        except Exception as e:
            logger.log_error(f"添加客户端失败: {e}", exc_info=True)
            try:
                client_socket.close()
            except:
                pass
            raise
    
    def _enable_keepalive(self, client_socket):
        """TCP Keep-Alive"""
        try:
            if not config.TCP_KEEPALIVE['enabled']:
                return
                
            client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            
            
            try:
                if hasattr(socket, 'TCP_KEEPIDLE'):
                    client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, config.TCP_KEEPALIVE['idle'])
                if hasattr(socket, 'TCP_KEEPINTVL'):
                    client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, config.TCP_KEEPALIVE['interval'])
                if hasattr(socket, 'TCP_KEEPCNT'):
                    client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, config.TCP_KEEPALIVE['count'])
                # 移除频繁的debug日志
                # logger.log_debug(f"TCP Keep-Alive已启用: idle={config.TCP_KEEPALIVE['idle']}s", 'ntrip')
            except OSError:
                # 移除频繁的debug日志
                # logger.log_debug("TCP Keep-Alive已启用（使用系统默认参数）", 'ntrip')
                pass
            
            
            client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, config.BUFFER_SIZE)
            client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, config.BUFFER_SIZE)
            
        except Exception as e:
            logger.log_warning(f"设置TCP Keep-Alive失败: {e}", 'ntrip')
    
    def remove_client(self, client_info):
        """移除客户端连接（幂等：多处清理路径可能重复调用）"""
        try:
            if client_info.get('closed'):
                return
            client_info['closed'] = True

            # 丢弃待发数据，让发送线程立即放弃这个客户端
            outbox_lock = client_info.get('outbox_lock')
            if outbox_lock is not None:
                with outbox_lock:
                    client_info['outbox'].clear()
            pending = client_info.get('pending')
            if pending:
                pending.clear()
            
            self._close_client(client_info)
            
            with self.client_lock:
                
                mount = client_info['mount']
                if mount in self.clients and client_info in self.clients[mount]:
                    self.clients[mount].remove(client_info)
                    
                    if not self.clients[mount]:
                        del self.clients[mount]
                
                self.stats['active_clients'] = sum(len(clients) for clients in self.clients.values())
                self.stats['disconnected_clients'] += 1
            
            # 用 connection_id 精确移除单条连接；传 IP 会导致按"用户+挂载点"整批删除
            conn_id = client_info.get('connection_id')
            connection.remove_user_connection(
                client_info['user'], 
                conn_id,
                None if conn_id else client_info['mount']
            )
            
            logger.log_client_disconnect(
                client_info['user'], 
                client_info['mount'], 
                client_info['addr'][0]
            )
            
        except Exception as e:
            logger.log_error(f"移除客户端失败: {e}", exc_info=True)
    
    def _close_client(self, client_info):
        """关闭客户端连接"""
        try:
            socket_obj = client_info['socket']
            socket_obj.close()
        except Exception as e:
            logger.log_debug(f"关闭客户端连接失败: {e}", 'ntrip')
    
    def upload_data(self, mount, data_chunk):
        """上传数据到指定挂载点"""
        timestamp = time.time()

        # 不能"先判断再取"：判断与 append 之间缓冲可能被 remove_mount_buffer 删掉，
        # 那样 self.mount_buffers[mount] 会抛 KeyError，直接把源站接收循环打断。
        with self.buffer_lock:
            buf = self.mount_buffers.get(mount)
            if buf is None:
                buf = RingBuffer(maxlen=self.buffer_maxlen, max_age=self.buffer_max_age)
                self.mount_buffers[mount] = buf
                logger.log_mount_operation('buffer_created', mount)
            buf.append(data_chunk, timestamp)

        self._send_to_subscribers(mount, data_chunk)

        try:
            connection.update_mount_data_stats(mount, len(data_chunk))
        except Exception as e:
            logger.log_error(f"更新挂载点 {mount} 数据统计时发生错误: {e}")
    
    def create_mount_buffer(self, mount):
        with self.buffer_lock:
            if mount not in self.mount_buffers:
                self.mount_buffers[mount] = RingBuffer(maxlen=self.buffer_maxlen,
                                                       max_age=self.buffer_max_age)
                logger.log_mount_operation('buffer_created', mount)
                return True
            return False
    
    def remove_mount_buffer(self, mount):
        with self.buffer_lock:
            self.mount_cursors.pop(mount, None)
            if mount in self.mount_buffers:
                del self.mount_buffers[mount]
                logger.log_mount_operation('buffer_removed', mount)
                return True
            return False
    
    def _broadcast_loop(self):
        """广播循环"""
        logger.log_system_event('数据广播循环开始运行')
        
        while self.running:
            try:
                self._broadcast_data()
                time.sleep(self.broadcast_interval)
            except Exception as e:
                logger.log_error(f"广播循环异常: {e}", exc_info=True)
                time.sleep(1)
    
    def _broadcast_data(self):
        """把新增数据分发给各客户端

        这里是"生产者"：只做内存搬运（挂载点切片一次 + 逐客户端入队），不做任何 socket 写。
        原实现在广播线程里直接 sendall，任何一个慢客户端（TCP 窗口打满、阻塞到超时）
        都会把整个广播轮次卡住，进而拖死所有挂载点的所有用户。
        """
        with self.buffer_lock:
            mount_items = list(self.mount_buffers.items())
        
        for mount_name, buffer in mount_items:
            with self.client_lock:
                clients = list(self.clients.get(mount_name, ()))
            if not clients:
                continue

            # 每个挂载点只切片一次，所有客户端共享同一批数据；
            # 原来每个客户端各切一次（2000 客户端 = 2000 次切片）
            cursor = self.mount_cursors.get(mount_name)
            if cursor is None:
                # 首次有客户端时从当前写位置开始，不回放历史数据
                cursor = buffer.current_index()

            new_data, new_cursor = buffer.get_since_index(cursor)
            self.mount_cursors[mount_name] = new_cursor
            if not new_data:
                continue

            for client_info in clients:
                if not client_info['initialized']:
                    # 首个广播轮次：从客户端"加入那一刻"的写位置补一次，
                    # 避免客户端恰好赶上首包时丢掉前 10ms 的数据
                    start = client_info['join_index']
                    if start is None:
                        start = self.mount_cursors.get(mount_name, 0)
                    first_data, _ = buffer.get_since_index(start)
                    if first_data:
                        self._enqueue(client_info, first_data)
                    client_info['initialized'] = True
                    continue
                self._enqueue(client_info, new_data)

        # 处理"内核缓冲写满后残留"的客户端
        self._retry_stalled_sends()

    def _enqueue(self, client_info, chunks):
        """把一批数据放进客户端待发队列（非阻塞）"""
        if client_info.get('closed'):
            return

        outbox = client_info.get('outbox')
        outbox_lock = client_info.get('outbox_lock')
        if outbox is None or outbox_lock is None:
            return

        with outbox_lock:
            # 队列积压说明这个客户端追不上：直接清空跳到最新。
            # 差分数据过期就没用了，宁可丢历史也不要让延迟越滚越大、内存越吃越多。
            if len(outbox) + 1 > self.client_queue_size:
                dropped = sum(len(b) for b in outbox) + len(chunks)
                client_info['dropped_bytes'] = client_info.get('dropped_bytes', 0) + dropped
                self.stats['dropped_bytes'] = self.stats.get('dropped_bytes', 0) + dropped
                outbox.clear()
            outbox.append(chunks)

            if not client_info['queued']:
                client_info['queued'] = True
                need_notify = True
            else:
                need_notify = False

        if need_notify:
            self.send_queue.put(client_info)

    def _sender_loop(self):
        """发送线程主循环：从工作队列取客户端，把待发数据写出去"""
        while self.running:
            try:
                client_info = self.send_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if client_info is None:  # 停止哨兵
                return

            try:
                self._flush_client(client_info)
            except Exception as e:
                client_info['send_errors'] = client_info.get('send_errors', 0) + 1
                self.stats['failed_sends'] += 1
                # 网络类错误是常态（客户端掉线/重启），只记 debug，避免刷日志
                if isinstance(e, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                                  TimeoutError, BlockingIOError, ConnectionError, OSError)):
                    logger.log_debug(f"客户端 {client_info['addr']} 已断开: {e}", 'ntrip')
                else:
                    logger.log_warning(f"发送数据到客户端失败 ({client_info['addr']}): {e}", 'ntrip')
                try:
                    self.remove_client(client_info)
                except Exception as ex:
                    logger.log_error(f"移除发送失败的客户端异常: {ex}", exc_info=True)
            finally:
                requeue = False
                with client_info['outbox_lock']:
                    client_info['queued'] = False
                    if client_info['outbox'] and not client_info.get('closed'):
                        client_info['queued'] = True
                        requeue = True
                if requeue:
                    self.send_queue.put(client_info)

    def _flush_client(self, client_info):
        """把客户端的待发数据尽可能写出去（在发送线程中执行）

        socket 是非阻塞的：写入只花"内核还能吃多少"的时间，剩下的留在 pending 里，
        由广播轮次按 retry_at 退避重试。这样任何一个慢客户端都不会占住发送线程。
        """
        if client_info.get('closed'):
            return

        outbox = client_info['outbox']
        with client_info['outbox_lock']:
            batches = list(outbox)
            outbox.clear()

        pending = client_info['pending']
        for batch in batches:
            for ts, data in batch:
                pending += data
                client_info['pending_last_ts'] = ts
            client_info['pending_messages'] += len(batch)

        if not pending:
            return

        now = time.time()
        if not client_info.get('pending_since'):
            client_info['pending_since'] = now

        # 内存上限保护：积压超过上限说明客户端长期追不上。
        # 丢掉最老的部分、保留最新的一段（差分数据只有最新的有意义），
        # 而不是整批丢弃，避免一次突发就把客户端饿死。
        if len(pending) > self.pending_max_bytes:
            keep = max(64 * 1024, self.pending_max_bytes // 2)
            dropped = len(pending) - keep
            del pending[:dropped]
            client_info['dropped_bytes'] = client_info.get('dropped_bytes', 0) + dropped
            self.stats['dropped_bytes'] = self.stats.get('dropped_bytes', 0) + dropped
            logger.log_debug(f"客户端 {client_info['addr']} 积压超限，丢弃最老 {dropped} 字节", 'ntrip')

        # 停滞过久同样放弃积压，避免一个半死连接长期占着内存
        if now - client_info['pending_since'] > self.stall_timeout:
            self._drop_pending(client_info, '发送停滞超时')
            return

        sock = client_info['socket']
        # 用 memoryview + 偏移量写出：原来每写一次就 `del pending[:sent]`，
        # 大积压时会退化成 O(n²) 的内存搬移（1MB 积压、每个 send 只吃 64KB 时尤其明显）。
        sent_total = 0
        blocked = False
        try:
            view = memoryview(pending)
            try:
                while sent_total < len(pending):
                    sent = sock.send(view[sent_total:])
                    if sent <= 0:
                        raise ConnectionResetError('socket closed by peer')
                    sent_total += sent
            finally:
                # 必须先释放 view，否则后面改 bytearray 会抛 BufferError
                view.release()
        except (BlockingIOError, InterruptedError):
            # 内核发送缓冲已满：留到下一轮，退避后重试
            blocked = True

        if sent_total:
            del pending[:sent_total]
            client_info['bytes_sent'] += sent_total
            self.stats['total_bytes_sent'] += sent_total

        if blocked:
            client_info['retry_at'] = time.time() + self.retry_interval
            return

        if not pending:
            client_info['pending_since'] = None
            client_info['retry_at'] = 0.0
            client_info['last_seen'] = time.time()
            messages = client_info.get('pending_messages', 0)
            if messages:
                client_info['messages_sent'] += messages
                self.stats['total_messages_sent'] += messages
                client_info['pending_messages'] = 0
            last_ts = client_info.get('pending_last_ts')
            if last_ts:
                client_info['last_sent_timestamp'] = last_ts

            if client_info.get('connection_id'):
                # 静默更新用户活动，不产生日志。
                # 这里必须传"增量"：client_info['bytes_sent'] 是累计值，
                # 直接传会让 update_user_activity 里再累加一次，
                # 前端看到的"已发送"于是按轮次平方级膨胀。
                reported = client_info.get('bytes_reported', 0)
                delta = client_info['bytes_sent'] - reported
                if delta > 0:
                    client_info['bytes_reported'] = client_info['bytes_sent']
                    connection.update_user_activity(
                        client_info['user'],
                        client_info['connection_id'],
                        delta
                    )

    def _drop_pending(self, client_info, reason):
        """丢弃客户端积压数据，让它从最新数据继续"""
        pending = client_info['pending']
        dropped = len(pending)
        if dropped:
            client_info['dropped_bytes'] = client_info.get('dropped_bytes', 0) + dropped
            self.stats['dropped_bytes'] = self.stats.get('dropped_bytes', 0) + dropped
            logger.log_debug(f"客户端 {client_info['addr']} {reason}，丢弃 {dropped} 字节积压", 'ntrip')
        pending.clear()
        client_info['pending_messages'] = 0
        client_info['pending_since'] = None
        client_info['retry_at'] = 0.0

    def _retry_stalled_sends(self):
        """把"还有残留数据没发完"的客户端重新交给发送线程（退避重试）"""
        now = time.time()
        with self.client_lock:
            all_clients = [c for clients in self.clients.values() for c in clients]

        for client_info in all_clients:
            if client_info.get('closed') or not client_info['pending']:
                continue
            if now < client_info.get('retry_at', 0.0):
                continue
            if client_info['queued']:
                continue

            with client_info['outbox_lock']:
                if client_info['queued'] or client_info.get('closed'):
                    continue
                client_info['queued'] = True
            self.send_queue.put(client_info)

    def get_stats(self):
        """获取转发器统计信息"""
        with self.buffer_lock, self.client_lock:
            buffer_stats = {}
            for mount, buffer in self.mount_buffers.items():
                buffer_stats[mount] = buffer.get_stats()
            
            return {
                'forwarder': self.stats.copy(),
                'buffers': buffer_stats,
                'clients_by_mount': {mount: len(clients) for mount, clients in self.clients.items()},
                'send_queue': {
                    'pending_clients': self.send_queue.qsize(),
                    'sender_threads': len(self.sender_threads),
                }
            }
    
    def get_client_info(self, mount=None):
        """获取客户端信息"""
        with self.client_lock:
            if mount:
                return self.clients.get(mount, [])
            else:
                return dict(self.clients)
    

    
    def force_disconnect_user(self, username):
        """强制断开指定用户的所有连接"""
        disconnected_count = 0
        clients_to_remove = []
        
        with self.client_lock:
            for mount_name, clients in self.clients.items():
                for client_info in clients[:]:
                    if client_info['user'] == username:
                        clients_to_remove.append(client_info)
        
        for client_info in clients_to_remove:
            try:
                self.remove_client(client_info)
                disconnected_count += 1
                logger.log_info(f"强制断开用户 {username} 的连接: {client_info['mount']}")
            except Exception as e:
                logger.log_error(f"强制断开用户 {username} 连接失败: {e}")
        
        logger.log_info(f"强制断开用户 {username} 完成，共断开 {disconnected_count} 个连接")
        return disconnected_count > 0
    
    def force_disconnect_mount(self, mount_name):
        """强制断开指定挂载点的所有连接"""
        disconnected_count = 0
        
        with self.client_lock:
            if mount_name in self.clients:
                clients_to_remove = self.clients[mount_name][:]
                
                for client_info in clients_to_remove:
                    try:
                        self.remove_client(client_info)
                        disconnected_count += 1
                        logger.log_info(f"强制断开挂载点 {mount_name} 的用户连接: {client_info['user']}")
                    except Exception as e:
                        logger.log_error(f"强制断开挂载点 {mount_name} 用户连接失败: {e}")
        
        try:
            self.remove_mount_buffer(mount_name)
            logger.log_info(f"移除挂载点 {mount_name} 的数据缓冲区")
        except Exception as e:
            logger.log_error(f"移除挂载点 {mount_name} 缓冲区失败: {e}")
        logger.log_info(f"强制断开挂载点 {mount_name} 完成，共断开 {disconnected_count} 个用户连接")
        return True
    
    def register_subscriber(self, mount_name, socket_write_end):
        """注册数据订阅者（用于RTCM解析等）

        订阅端（解析线程）只做旁路分析，绝不能反压源站：
        管道写端设为非阻塞，写满时由 _send_to_subscribers 丢弃当前块。
        否则解析线程一慢，源站上传线程就会阻塞在 send 上，整个挂载点的取流一起卡住。
        """
        with self.subscriber_lock:
            try:
                if hasattr(socket_write_end, 'setblocking'):
                    socket_write_end.setblocking(False)
            except OSError:
                pass
            if mount_name not in self.subscribers:
                self.subscribers[mount_name] = []
            self.subscribers[mount_name].append(socket_write_end)
            logger.log_debug(f"添加解析线程订阅挂载点 {mount_name}", 'ntrip')
            logger.log_info(f"[DEBUG] 注册解析线程订阅 [挂载点: {mount_name}, 订阅者数: {len(self.subscribers[mount_name])}]")
    
    def unregister_subscriber(self, mount_name, socket_write_end):
        """注销数据订阅者"""
        with self.subscriber_lock:
            if mount_name in self.subscribers:
                try:
                    self.subscribers[mount_name].remove(socket_write_end)
                    if not self.subscribers[mount_name]:
                        del self.subscribers[mount_name]
                    logger.log_debug(f"关闭解析线程订阅从挂载点 {mount_name}", 'ntrip')
                except ValueError:
                    pass 
    
    def _send_to_subscribers(self, mount_name: str, data_chunk: bytes):
        """向订阅者发送数据"""
        # logger.log_debug(f"[FORWARDER] 正在向 {mount_name} 发送数据，订阅者数量: {len(self.subscribers.get(mount_name, []))}")
        with self.subscriber_lock:
            if mount_name in self.subscribers:
                subscribers_to_remove = []
                subscriber_count = len(self.subscribers[mount_name])
                # logger.log_debug(f"[DEBUG] 向挂载点 {mount_name} 的 {subscriber_count} 个订阅者发送数据 ({len(data_chunk)} 字节)", 'ntrip')
                
                if subscriber_count > 0:
                     # logger.log_info(f"[DEBUG] 发送RTCM数据 [挂载点: {mount_name}, 订阅者: {subscriber_count}, 数据长度: {len(data_chunk)}]", 'ntrip')
                     pass
                
                for i, subscriber in enumerate(self.subscribers[mount_name]):
                    try:
                        # 检查订阅者类型，socket对象使用send方法，文件对象使用write方法
                        if hasattr(subscriber, 'send'):
                            # socket对象（已在 register_subscriber 设为非阻塞）：
                            # 写满说明解析线程跟不上，丢这一块即可，
                            # 既不能阻塞源站上传线程，也不能把订阅者判死。
                            try:
                                subscriber.send(data_chunk)
                            except (BlockingIOError, InterruptedError):
                                self.stats['subscriber_dropped_bytes'] = \
                                    self.stats.get('subscriber_dropped_bytes', 0) + len(data_chunk)
                                continue
                        elif hasattr(subscriber, 'write'):
                            # 文件对象
                            subscriber.write(data_chunk)
                            subscriber.flush()
                        else:
                            raise AttributeError(f"订阅者对象不支持数据发送: {type(subscriber)}")
                        
                        # if i == 0:  # 只记录第一个订阅者的成功发送
                        #     logger.log_debug(f"[DEBUG] 成功发送到订阅者 #{i+1} [挂载点: {mount_name}]", 'ntrip')
                    except Exception as e:
                        logger.log_error(f"向解析线程订阅者 #{i+1} 发送数据失败 [挂载点: {mount_name}]: {e}", 'ntrip')
                        subscribers_to_remove.append(subscriber)
                
                # 移除失效的订阅者
                for subscriber in subscribers_to_remove:
                    try:
                        self.subscribers[mount_name].remove(subscriber)
                        logger.log_warning(f"[DEBUG] 移除解析线程失效订阅者 [挂载点: {mount_name}]", 'ntrip')
                    except ValueError:
                        pass
            
                if not self.subscribers[mount_name]:
                    del self.subscribers[mount_name]


forwarder = SimpleDataForwarder()

# 全局管理函数 扩容管理端
def initialize():
    """初始化数据转发器"""
    logger.log_system_event('数据转发器已初始化')
    return forwarder

def get_forwarder():
    """获取全局数据转发器实例"""
    return forwarder

def start_forwarder():
    """启动数据转发器"""
    forwarder.start()

def stop_forwarder():
    """停止数据转发器"""
    forwarder.stop()


def add_client(client_socket, user, mount, agent, addr, protocol_version, connection_id=None):
    """同步添加客户端（兼容原接口）"""
    try:
        return forwarder.add_client(client_socket, user, mount, agent, addr, protocol_version, connection_id)
    except Exception as e:
        logger.log_error(f"添加客户端超时: {e}", 'ntrip')
        raise

def remove_client(client_info):
    """移除客户端"""
    return forwarder.remove_client(client_info)

def upload_data(mount, data_chunk):
    """上传数据"""
    return forwarder.upload_data(mount, data_chunk)

def create_mount_buffer(mount):
    """创建挂载点缓冲区"""
    return forwarder.create_mount_buffer(mount)

def remove_mount_buffer(mount):
    """移除挂载点缓冲区"""
    return forwarder.remove_mount_buffer(mount)

def get_stats():
    """获取统计信息"""
    return forwarder.get_stats()

def get_client_info(mount=None):
    """获取客户端信息"""
    return forwarder.get_client_info(mount)

def force_disconnect_user(username):
    """强制断开指定用户的所有连接"""
    return forwarder.force_disconnect_user(username)

def force_disconnect_mount(mount_name):
    """强制断开挂载点"""
    return forwarder.force_disconnect_mount(mount_name)

def register_subscriber(mount_name, socket_write_end):
    """注册数据订阅者"""
    return forwarder.register_subscriber(mount_name, socket_write_end)

def unregister_subscriber(mount_name, socket_write_end):
    """注销数据订阅者"""
    return forwarder.unregister_subscriber(mount_name, socket_write_end)