#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import socket
import threading
from collections import deque
from threading import Lock, RLock
from . import config
from . import logger
from . import connection

class RingBuffer:
    """环形缓冲区"""
    def __init__(self, maxlen=None):
        self.maxlen = maxlen or config.RING_BUFFER_SIZE
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
            self.total_bytes += len(data)
            self.total_messages += 1
            self.last_timestamp = timestamp
            self._write_index += 1
                
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
        self.buffer_maxlen = buffer_maxlen or config.RING_BUFFER_SIZE
        self.broadcast_interval = broadcast_interval or config.BROADCAST_INTERVAL
        
        self.mount_buffers = {}  # {mount_name: RingBuffer}
        self.buffer_lock = RLock()
        
        self.clients = {}  # {mount_name: [client_info]}
        self.client_lock = RLock()
        
        self.subscribers = {}  # {mount_name: [socket_write_end]}
        self.subscriber_lock = RLock()
        
        self.broadcast_thread = None
        self.running = False
        
        self.stats = {
            'total_clients': 0,
            'active_clients': 0,
            'total_bytes_sent': 0,
            'total_messages_sent': 0,
            'failed_sends': 0,
            'disconnected_clients': 0
        }
    
    def start(self):
        """启动广播线程"""
        if self.running:
            return
            
        self.running = True
        self.broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self.broadcast_thread.start()
        logger.log_system_event('数据转发器已启动')
    
    def stop(self):
        """停止广播线程"""
        self.running = False
        
        if self.broadcast_thread and self.broadcast_thread.is_alive():
            self.broadcast_thread.join(timeout=5)
        
        # 关闭所有客户端连接
        with self.client_lock:
            for mount_clients in self.clients.values():
                for client_info in mount_clients[:]:
                    self._close_client(client_info)
                    
        logger.log_system_event('数据转发器已停止')
    
    def add_client(self, client_socket, user, mount, agent, addr, protocol_version, connection_id=None):
        """添加客户端连接（同步方式）"""
        try:
            # 启用TCP Keep-Alive
            self._enable_keepalive(client_socket)
            
            client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            current_time = time.time()
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
        """移除客户端连接"""
        try:
            self._close_client(client_info)
            
            with self.client_lock:
                
                mount = client_info['mount']
                if mount in self.clients and client_info in self.clients[mount]:
                    self.clients[mount].remove(client_info)
                    
                    if not self.clients[mount]:
                        del self.clients[mount]
                
                self.stats['active_clients'] = sum(len(clients) for clients in self.clients.values())
                self.stats['disconnected_clients'] += 1
            
            connection.remove_user_connection(
                client_info['user'], 
                client_info['addr'][0], 
                client_info['mount']
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
        
        if mount not in self.mount_buffers:
            self.create_mount_buffer(mount)
        
        with self.buffer_lock:
            self.mount_buffers[mount].append(data_chunk, timestamp)
        
        self._send_to_subscribers(mount, data_chunk)
        
        try:
            connection.update_mount_data_stats(mount, len(data_chunk))
        except Exception as e:
            logger.log_error(f"更新挂载点 {mount} 数据统计时发生错误: {e}")
    
    def create_mount_buffer(self, mount):
        with self.buffer_lock:
            if mount not in self.mount_buffers:
                self.mount_buffers[mount] = RingBuffer(self.buffer_maxlen)
                logger.log_mount_operation('buffer_created', mount)
                return True
            return False
    
    def remove_mount_buffer(self, mount):
        with self.buffer_lock:
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
        """广播数据到所有客户端"""
        with self.buffer_lock:
            mount_items = list(self.mount_buffers.items())
        
        for mount_name, buffer in mount_items:
            with self.client_lock:
                if mount_name in self.clients:
                    clients = self.clients[mount_name][:]
                    self._send_data_to_clients(clients, buffer, mount_name)
    
    def _send_data_to_clients(self, clients, buffer, mount_name):
        """发送数据到客户端列表"""
        disconnected_clients = []
        
        for client_info in clients:
            try:
                self._send_to_client(client_info, buffer)
            except Exception as e:
                logger.log_warning(f"发送数据到客户端失败 ({client_info['addr']}): {e}", 'ntrip')
                disconnected_clients.append(client_info)
        
        # 清理断开的连接
        for client_info in disconnected_clients:
            self.remove_client(client_info)
    
    def _send_to_client(self, client_info, buffer):
        """发送数据到单个客户端"""
        try:
            
            last_sent_timestamp = client_info['last_sent_timestamp']
            new_data = buffer.get_since(last_sent_timestamp)
            
            if new_data:
                
                bytes_sent = self._send_data_simple(client_info, new_data)
                
                if bytes_sent > 0:
                   
                    current_time = time.time()
                    client_info['last_seen'] = current_time
                    client_info['last_sent_timestamp'] = new_data[-1][0]
                    client_info['bytes_sent'] += bytes_sent
                    client_info['messages_sent'] += len(new_data)
                    
                    self.stats['total_bytes_sent'] += bytes_sent
                    self.stats['total_messages_sent'] += len(new_data)
                    
                    if client_info.get('connection_id'):
                        # 静默更新用户活动，不产生日志
                        connection.update_user_activity(
                            client_info['user'], 
                            client_info['connection_id'], 
                            bytes_sent
                        )
        
        except Exception as e:
            # 只在非网络错误时记录警告日志
            if "Connection" not in str(e) and "Broken pipe" not in str(e):
                logger.log_warning(f"发送数据到客户端失败 ({client_info['addr']}): {e}", 'ntrip')
            raise
    
    def _send_data_simple(self, client_info, data_list):
        """简单的数据发送方法"""
        try:
            socket_obj = client_info['socket']
            protocol_version = client_info['protocol_version']
            total_bytes_sent = 0
            
            for timestamp, data in data_list:
                if protocol_version == 'ntrip2_0':
                    # NTRIP 2.0 使用分块编码
                    chunk_size = hex(len(data))[2:].upper().encode('ascii')
                    chunk_data = chunk_size + b'\r\n' + data + b'\r\n'
                    socket_obj.sendall(chunk_data)
                    total_bytes_sent += len(chunk_data)
                else:
                    # NTRIP 1.0 直接发送
                    socket_obj.sendall(data)
                    total_bytes_sent += len(data)
            
            return total_bytes_sent
            
        except Exception as e:
            client_info['send_errors'] += 1
            self.stats['failed_sends'] += 1
            raise
    
    def get_stats(self):
        """获取转发器统计信息"""
        with self.buffer_lock, self.client_lock:
            buffer_stats = {}
            for mount, buffer in self.mount_buffers.items():
                buffer_stats[mount] = buffer.get_stats()
            
            return {
                'forwarder': self.stats.copy(),
                'buffers': buffer_stats,
                'clients_by_mount': {mount: len(clients) for mount, clients in self.clients.items()}
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
        """注册数据订阅者（用于RTCM解析等）"""
        with self.subscriber_lock:
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
                            # socket对象
                            subscriber.send(data_chunk)
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