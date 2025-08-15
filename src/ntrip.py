#!/usr/bin/env python3
"""
ntrip.py - NTRIP Caster主程序模块
功能：监听NTRIP请求端口，接收上传和下载请求，验证用户和挂载点的有效性
"""

import sys
import time
import socket
import logging
import threading
import base64
from datetime import datetime, timezone
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Full, Empty
from collections import defaultdict

from . import forwarder
from . import config
from . import logger
from .logger import log_debug, log_info, log_warning, log_error, log_critical, log_system_event
from . import connection


DEBUG = config.DEBUG
VERSION = config.VERSION
NTRIP_PORT = config.NTRIP_PORT
WEB_PORT = config.WEB_PORT
BUFFER_SIZE = config.BUFFER_SIZE


class AntiSpamLogger:
    def __init__(self, time_window=60, max_count=5):
        self.time_window = time_window  
        self.max_count = max_count      
        self.message_counts = defaultdict(list)  
        self.suppressed_counts = defaultdict(int)  
        self.lock = threading.Lock()
    
    def should_log(self, message_key):
        """判断是否应该记录日志"""
        with self.lock:
            now = time.time()
           
            self.message_counts[message_key] = [
                timestamp for timestamp in self.message_counts[message_key]
                if now - timestamp < self.time_window
            ]
            
            if len(self.message_counts[message_key]) < self.max_count:
                self.message_counts[message_key].append(now)
                return True
            else:
                self.suppressed_counts[message_key] += 1
                return False
    
    def get_suppressed_count(self, message_key):
        """获取被抑制的消息数量"""
        with self.lock:
            count = self.suppressed_counts[message_key]
            self.suppressed_counts[message_key] = 0  
            return count

anti_spam_logger = AntiSpamLogger(time_window=60, max_count=3)
MAX_CONNECTIONS = config.MAX_CONNECTIONS
MAX_CONNECTIONS_PER_USER = config.MAX_CONNECTIONS_PER_USER
MAX_WORKERS = config.MAX_WORKERS
CONNECTION_QUEUE_SIZE = config.CONNECTION_QUEUE_SIZE

# 获取日志记录器

class NTRIPHandler:
    """NTRIP请求处理器"""
    
    def __init__(self, client_socket, client_address, db_manager):
        self.client_socket = client_socket
        self.client_address = client_address
        self.db_manager = db_manager
        self.ntrip_version = "1.0"
        self.protocol_type = "ntrip1_0"
        self.user_agent = ""
        self.mount = ""
        self.username = ""
        self.ntrip1_password = ""  
        self.current_method = "GET"  
        
        self.client_socket.settimeout(config.SOCKET_TIMEOUT)
        
        self._configure_keepalive()
    
    def _configure_keepalive(self):
        """配置TCP Keep-Alive（跨平台统一实现）"""
        try:
            if not config.TCP_KEEPALIVE['enabled']:
                return
                
            # 启用TCP Keep-Alive 
            self.client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            
            try:
                if hasattr(socket, 'TCP_KEEPIDLE'):
                    self.client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, config.TCP_KEEPALIVE['idle'])
                if hasattr(socket, 'TCP_KEEPINTVL'):
                    self.client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, config.TCP_KEEPALIVE['interval'])
                if hasattr(socket, 'TCP_KEEPCNT'):
                    self.client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, config.TCP_KEEPALIVE['count'])
                
                message_key = "tcp_keepalive_configured"
                if anti_spam_logger.should_log(message_key):
                    suppressed = anti_spam_logger.get_suppressed_count(message_key)
                    if suppressed > 0:
                        logger.log_debug(f"TCP Keep-Alive已配置: idle={config.TCP_KEEPALIVE['idle']}s (已抑制{suppressed}条相似消息)", 'ntrip')
                    else:
                        logger.log_debug(f"TCP Keep-Alive已配置: idle={config.TCP_KEEPALIVE['idle']}s", 'ntrip')
            except OSError:
               
                logger.log_debug("TCP Keep-Alive已启用（使用系统默认参数）", 'ntrip')
        except Exception as e:
            logger.log_debug(f"配置Keep-Alive失败: {e}", 'ntrip')
    
    def handle_request(self):
        """处理NTRIP请求，增强验证和错误处理"""
        try:
            # 改为debug级别，避免频繁日志
            log_debug(f"=== 开始处理请求 {self.client_address} ===")
           
            request_data = self.client_socket.recv(BUFFER_SIZE).decode('utf-8', errors='ignore')
            if not request_data:
                log_debug(f"客户端 {self.client_address} 发送空请求")
                return
            
            raw_request = request_data[:200]
            sanitized_request = self._sanitize_request_for_logging(raw_request)

            # 改为debug级别，避免频繁日志
            log_debug(f"检测到连接请求来自 {self.client_address}: {sanitized_request}")
            
            lines = request_data.strip().split('\r\n')
            if not lines or not lines[0].strip():
                self.send_error_response(400, "Bad Request: Empty request line")
                return
            
            request_line = lines[0]
            try:
                method, path, protocol = self._parse_request_line(request_line)
               
                self.current_method = method.upper()
            except ValueError as e:
                log_debug(f"请求行解析失败 {self.client_address}: {e}")
                self.send_error_response(400, f"Bad Request: {str(e)}")
                return
            
            headers = self._parse_headers(lines[1:])
            
            if self._is_empty_request(method, path, headers):
                log_debug(f"检测到空请求 {self.client_address}")
                self.send_error_response(400, "Bad Request: Empty request")
                return
            
            self._determine_ntrip_version(headers, request_line)
            
            is_valid, error_msg = self._is_valid_request(method, path, headers)
            if not is_valid:
                # 验证失败保持info级别，这是重要信息
                log_info(f"请求验证失败 {self.client_address}: {error_msg}")
                self.send_error_response(400, f"Bad Request: {error_msg}")
                return
            
            self.user_agent = headers.get('user-agent', 'Unknown')
            
            # 改为debug级别，避免频繁日志
            log_debug(f"请求验证通过 {self.client_address}: {method} {path} (协议: {self.protocol_type})")

            if method.upper() in ['SOURCE', 'POST']:
                
                self.handle_upload(path, headers)
            elif method.upper() == 'GET':
               
                if self.protocol_type in ['ntrip1_0_http', 'ntrip2_0', 'ntrip1_0', 'ntrip0_8']:
                    self.handle_download(path, headers)
                else:
                   
                    self.handle_http_get(path, headers)

            elif method.upper() == 'OPTIONS':
                
                self.handle_options(headers)
            elif method.upper() in ['DESCRIBE', 'SETUP', 'PLAY', 'PAUSE', 'TEARDOWN', 'RECORD']:
               
                self.handle_rtsp_command(method, path, headers)
            else:
                self.send_error_response(405, f"Method Not Allowed: {method}")
        
        except socket.timeout:
            log_debug(f"客户端 {self.client_address} 连接超时")
            
            self._cleanup()
        except UnicodeDecodeError as e:
            log_debug(f"请求解码失败 {self.client_address}: {e}")
            self.send_error_response(400, "Bad Request: Invalid encoding")
           
            self._cleanup()
        except Exception as e:
            log_error(f"处理请求异常 {self.client_address}: {e}", exc_info=True)
            self.send_error_response(500, "Internal Server Error")
           
            self._cleanup()
    
    def _parse_request_line(self, request_line):
        """解析请求行，支持多种NTRIP协议版本的SOURCE格式"""
        parts = request_line.split()
        
        if not parts:
            raise ValueError("Empty request line")
        
        method = parts[0].upper()
        
        if method == 'SOURCE':
            if len(parts) >= 2:
                if len(parts) == 2:
                    # NTRIP 0.8格式: "SOURCE <url>" 或 "SOURCE <path>"
                    url_or_path = parts[1]
                    if url_or_path.startswith('/') and not url_or_path.startswith(('http://', 'https://', 'rtsp://')):
                        # SOURCE /mountpoint 无密码格式，需要后续401认证
                        return 'SOURCE', url_or_path, 'NTRIP/1.0'
                    else:
                        return self._parse_source_url_format(url_or_path)
                elif len(parts) >= 3:
                    password = parts[1]
                    mountpoint_or_url = parts[2]
                    
                    # 检查是否为URL格式
                    if mountpoint_or_url.startswith(('http://', 'https://', 'rtsp://')):
                        # NTRIP 0.8 URL格式: "SOURCE <password> <url>"
                        return self._parse_source_url_format(mountpoint_or_url, password)
                    else:
                        # NTRIP 0.9/1.0格式: "SOURCE <password> /<mountpoint>" 或 "SOURCE <password> <mountpoint>"
                        # 统一处理挂载点格式，确保以/开头
                        if not mountpoint_or_url.startswith('/'):
                            mountpoint = '/' + mountpoint_or_url
                        else:
                            mountpoint = mountpoint_or_url
                        
                        self.ntrip1_password = password
                        return 'SOURCE', mountpoint, 'NTRIP/1.0'
            else:
                raise ValueError(f"Invalid SOURCE request format: {request_line}")
        
        # NTRIP 1.0 ADMIN格式: "ADMIN <password> <path>"
        elif method == 'ADMIN' and len(parts) >= 3:
            password = parts[1]
            path = parts[2]
            if not path.startswith('/'):
                path = '/' + path
            self.ntrip1_password = password
            return 'ADMIN', path, 'NTRIP/1.0'
        
        # 标准HTTP格式: "METHOD PATH PROTOCOL"
        elif len(parts) == 3:
            method, path, protocol = parts
            
            # 对于RTSP协议，保持原始URL格式
            if protocol.startswith('RTSP/'):
                # RTSP URL应该保持完整格式，不需要添加前缀
                return method, path, protocol
            else:
                # 对于HTTP协议，确保路径以/开头
                if not path.startswith('/'):
                    path = '/' + path
                return method, path, protocol
        
        else:
            raise ValueError(f"Invalid request line format: {request_line}")
    
    def _parse_source_url_format(self, url, password=None):
        """解析SOURCE请求中的URL格式"""
        from urllib.parse import urlparse
        
        if url.startswith(('http://', 'https://')):
            parsed = urlparse(url)
            
            mountpoint = parsed.path
            if not mountpoint or mountpoint == '/':
                raise ValueError(f"Invalid mountpoint in URL: {url}")

            if not mountpoint.startswith('/'):
                mountpoint = '/' + mountpoint

            if parsed.username and parsed.password:

                self.ntrip1_password = parsed.password
            elif password:
                self.ntrip1_password = password
            
            if parsed.hostname:
                self.parsed_host = parsed.hostname
            if parsed.port:
                self.parsed_port = parsed.port
            
            return 'SOURCE', mountpoint, 'NTRIP/0.8'
        
        elif url.startswith('rtsp://'):
            
            parsed = urlparse(url)
            
            
            mountpoint = parsed.path
            if not mountpoint or mountpoint == '/':
                raise ValueError(f"Invalid mountpoint in RTSP URL: {url}")
            
            
            if not mountpoint.startswith('/'):
                mountpoint = '/' + mountpoint
            
            
            if parsed.username and parsed.password:
                self.ntrip1_password = parsed.password
            elif password:
                self.ntrip1_password = password
            
            if parsed.hostname:
                self.parsed_host = parsed.hostname
            if parsed.port:
                self.parsed_port = parsed.port
            
            return 'SOURCE', mountpoint, 'NTRIP/0.8'
        
        elif url.startswith('/'):
            
            if password:
                self.ntrip1_password = password
            return 'SOURCE', url, 'NTRIP/0.8'
        
        else:
            
            mountpoint = '/' + url if not url.startswith('/') else url
            if password:
                self.ntrip1_password = password
            return 'SOURCE', mountpoint, 'NTRIP/0.8'
    
    def _parse_headers(self, header_lines):
        """解析请求头"""
        headers = {}
        for line in header_lines:
            if ':' in line:
                key, value = line.split(':', 1)
                headers[key.strip().lower()] = value.strip()
        return headers
    
    def _determine_ntrip_version(self, headers, request_line):
        """确定NTRIP协议类型判断"""
        
        if request_line.startswith(('SOURCE ', 'ADMIN ')):
            
            parts = request_line.split()
            if len(parts) >= 2:
               
                second_param = parts[1] if len(parts) == 2 else parts[2] if len(parts) >= 3 else ""
                if (second_param.startswith(('http://', 'https://', 'rtsp://')) or 
                    (len(parts) == 2 and (second_param.startswith('/') or not second_param.startswith('http')))):
                    self.ntrip_version = "0.8"
                    self.protocol_type = "ntrip0_8"
                    
                    message_key = f"ntrip_08_request_{self.client_address[0]}"
                    if anti_spam_logger.should_log(message_key):
                        suppressed = anti_spam_logger.get_suppressed_count(message_key)
                        if suppressed > 0:
                            logger.log_debug(f"检测到NTRIP 0.8请求: {request_line.split()[0]} - {self.client_address} (已抑制{suppressed}条相似消息)", 'ntrip')
                        else:
                            logger.log_debug(f"检测到NTRIP 0.8请求: {request_line.split()[0]} - {self.client_address}", 'ntrip')
                    return
            
            
            self.ntrip_version = "1.0"
            self.protocol_type = "ntrip1_0"
            
            message_key = f"ntrip_10_request_{self.client_address[0]}"
            if anti_spam_logger.should_log(message_key):
                suppressed = anti_spam_logger.get_suppressed_count(message_key)
                if suppressed > 0:
                    logger.log_debug(f"检测到NTRIP 1.0请求: {request_line.split()[0]} - {self.client_address} (已抑制{suppressed}条相似消息)", 'ntrip')
                else:
                    logger.log_debug(f"检测到NTRIP 1.0请求: {request_line.split()[0]} - {self.client_address}", 'ntrip')
            return
        
        
        if 'HTTP/' in request_line:
            protocol_type = "http"
        elif 'RTSP/' in request_line:
            protocol_type = "rtsp"
            self.ntrip_version = "1.0"
            self.protocol_type = "rtsp"
            logger.log_debug(f"检测到RTSP协议: {self.client_address}", 'ntrip')
            return
        else:
            protocol_type = "unknown"
        
        
        if request_line.startswith(('POST ', 'GET ')) and 'HTTP/' in request_line:
            user_agent = headers.get('user-agent', '').lower()
            
           
            if any(ntrip_ua in user_agent for ntrip_ua in ['ntrip', 'rtk', 'gnss', 'gps']):
               
                if '2.0' in user_agent or 'HTTP/1.1' in request_line:
                    self.ntrip_version = "2.0"
                    self.protocol_type = "ntrip2_0"
                    logger.log_debug(f"检测到NTRIP 2.0 HTTP格式: {self.client_address}", 'ntrip')
                else:
                    self.ntrip_version = "1.0"
                    self.protocol_type = "ntrip1_0_http"
                    log_debug(f"检测到NTRIP 1.0 HTTP格式: {self.client_address}")
                return
            
            # 检查是否有Authorization头部（可能是NTRIP客户端）
            if 'authorization' in headers:
                
                if 'HTTP/1.1' in request_line:
                    self.ntrip_version = "2.0"
                    self.protocol_type = "ntrip2_0"
                    log_debug(f"检测到NTRIP 2.0 HTTP认证格式: {self.client_address}")
                else:
                    self.ntrip_version = "1.0"
                    self.protocol_type = "ntrip1_0_http"
                    log_debug(f"检测到NTRIP 1.0 HTTP认证格式: {self.client_address}")
                return
            
            if protocol_type == "http" and "ntrip" in user_agent and path not in ["/", ""]:
                self.ntrip_version = "2.0"
                self.protocol_type = "ntrip2_0"
                log_debug(f"基于路径检测NTRIP 2.0: {self.client_address}")
                return
        
        # 检查Ntrip-Version头部字段（NTRIP 2.0特有）
        ntrip_version = headers.get('ntrip-version', '')
        if 'NTRIP/2.0' in ntrip_version:
            self.ntrip_version = "2.0"
            self.protocol_type = "ntrip2_0"
            log_debug(f"检测到NTRIP 2.0协议: {self.client_address}")
        elif protocol_type == "http":
            # HTTP请求但没有Ntrip-Version头，判断是否需要协议降级
            if self._should_downgrade_protocol(headers):
                self.ntrip_version = "1.0"
                self.protocol_type = "ntrip1_0"
                log_debug(f"协议降级到NTRIP 1.0: {self.client_address}")
            else:
                
                user_agent = headers.get('user-agent', '').lower()
                if any(keyword in user_agent for keyword in ['ntrip', 'rtk', 'gnss']):
                    self.ntrip_version = "2.0"
                    self.protocol_type = "ntrip2_0"
                    log_debug(f"基于User-Agent检测NTRIP 2.0: {self.client_address}")
                else:
                    self.ntrip_version = "2.0"
                    self.protocol_type = "http"
                    log_debug(f"使用HTTP协议: {self.client_address}")
        else:
            # 其他情况默认为NTRIP 1.0
            self.ntrip_version = "1.0"
            self.protocol_type = "ntrip1_0"
            log_debug(f"默认使用NTRIP 1.0: {self.client_address}")
    
    def _should_downgrade_protocol(self, headers):
        """判断是否应该降级协议到NTRIP 1.0"""
        
        user_agent = headers.get('user-agent', '').lower()
        old_clients = ['ntrip', 'rtk', 'gnss', 'leica', 'trimble']
        
        for client in old_clients:
            if client in user_agent and '2.0' not in user_agent:
                return True
        
        required_headers = ['connection', 'host']
        missing_headers = [h for h in required_headers if h not in headers]
        
        return len(missing_headers) > 0
    
    def _is_valid_request(self, method, path, headers):
        """验证请求的有效性，"""
        
        if not method:
            return False, "Missing request method"
        if not path:
            return False, "Invalid path format"
        
        if hasattr(self, 'protocol_type') and self.protocol_type == 'rtsp':
           
            if not (path.startswith('/') or path.startswith('rtsp://')):
                return False, "Invalid RTSP path format"
        else:
            
            if not path.startswith('/'):
                return False, "Invalid path format"
        

        if self.protocol_type in ['http', 'ntrip2_0']:
            if 'host' not in headers:
                return False, "Missing Host header"
        
        supported_methods = ['GET', 'POST', 'SOURCE', 'ADMIN', 'OPTIONS']
        
        if hasattr(self, 'protocol_type') and self.protocol_type == 'rtsp':
            rtsp_methods = ['DESCRIBE', 'SETUP', 'PLAY', 'PAUSE', 'TEARDOWN', 'RECORD']
            supported_methods.extend(rtsp_methods)
        
        if method.upper() not in supported_methods:
            return False, f"Unsupported method: {method}"
        
        return True, "Valid request"
    
    def _is_empty_request(self, method, path, headers):
        """检查是否为空请求"""
        return not method and not path and not headers
    
    def _sanitize_request_for_logging(self, request_data):
        """过滤请求数据中的敏感信息"""
        try:
            
            lines = request_data.replace('\r\n', '\n').replace('\r', '\n').split('\n')
            sanitized_lines = []

            if lines:
                first_line = lines[0].strip()
                # 检查是否是NTRIP 1.0格式（SOURCE password mount 或 GET mount password）
                if (first_line.startswith('SOURCE ') and len(first_line.split()) >= 3) or \
                   (first_line.startswith('GET ') and len(first_line.split()) >= 3):
                    parts = first_line.split()
                    if len(parts) >= 3:
                        method = parts[0]
                        if method == 'SOURCE':
                            
                            mount = parts[2]
                            
                            if len(parts) > 3:
                                additional_info = ' '.join(parts[3:])
                                sanitized_lines.append(f'{method} [PASSWORD_REDACTED] {mount} {additional_info}')
                            else:
                                sanitized_lines.append(f'{method} [PASSWORD_REDACTED] {mount}')
                        elif method == 'GET':
                            
                            mount = parts[1]   
                            sanitized_lines.append(f'{method} {mount} [PASSWORD_REDACTED]')
                    else:
                        sanitized_lines.append(first_line)
                else:
                    
                    sanitized_lines.append(first_line)
            
            for line in lines[1:]:
                line_lower = line.lower()
                
                if 'authorization:' in line_lower:
                    
                    if 'basic' in line_lower:
                        sanitized_lines.append('Authorization: Basic [REDACTED]')
                    elif 'digest' in line_lower:
                        sanitized_lines.append('Authorization: Digest [REDACTED]')
                    else:
                        sanitized_lines.append('Authorization: [REDACTED]')
                else:
                    sanitized_lines.append(line)
            
            return '\n'.join(sanitized_lines).replace('\r', '').strip()
        except Exception:
            
            return '[REQUEST DATA - SANITIZATION FAILED]'
    
    def verify_user(self, mount, auth_header, request_type="upload"):
        """验证NTRIP请求的用户和挂载点是否合法
        """
        try:
            # 统一处理挂载点名称，确保去除前导/
            mount_name = mount.lstrip('/')
            self.mount = mount_name
            
            if self.protocol_type == "ntrip1_0":
                
                if auth_header.startswith('Basic '):
                    return self._verify_basic_auth(mount, auth_header, request_type)
                elif auth_header.startswith('Digest '):
                    return self._verify_digest_auth(mount, auth_header, request_type)
                elif hasattr(self, 'ntrip1_password') and self.ntrip1_password:
                   
                    mount_password = self.ntrip1_password
                    

                    is_valid, error_msg = self.db_manager.verify_mount_and_user(mount_name, username=None, password=None, mount_password=mount_password)
                    
                    if not is_valid:
                        return False, error_msg
                    
                    self.username = f"source_{mount_name}"
                    
                    return True, "Authentication successful"
                else:
                    
                    return False, "Authentication required"
            
            elif self.protocol_type == "ntrip1_0_http":
                 if auth_header.startswith('Basic '):
                     return self._verify_basic_auth(mount, auth_header, request_type)
                 elif auth_header.startswith('Digest '):
                     return self._verify_digest_auth(mount, auth_header, request_type)
                 else:
                     
                     if hasattr(self, 'ntrip1_password') and self.ntrip1_password:
                         password = self.ntrip1_password
                         
                         is_valid, error_msg = self.db_manager.verify_mount_and_user(mount_name, username=None, password=None, mount_password=password, protocol_version="1.0")
                         if not is_valid:
                             return False, error_msg
                         
                         self.username = f"http_{mount_name}"
                         
                         return True, "Authentication successful"
                     return False, "Missing authorization"
            
            # NTRIP 0.8 URL格式认证（必须有密码）
            elif self.protocol_type == "ntrip0_8":
                 
                 if hasattr(self, 'ntrip1_password') and self.ntrip1_password:
                     password = self.ntrip1_password
                     
                     # 对于NTRIP 0.8格式，只验证挂载点和挂载点密码，不验证用户
                     is_valid, error_msg = self.db_manager.verify_mount_and_user(mount_name, username=None, password=None, mount_password=password, protocol_version="1.0")
                     
                     if not is_valid:
                         return False, error_msg
                     
                     self.username = f"ntrip08_{mount_name}"
                     
                     return True, "Authentication successful"
                 else:
                     
                     return False, "Authentication required"
            
            elif self.protocol_type == "ntrip2_0":
                 if auth_header.startswith('Basic '):
                     return self._verify_basic_auth(mount, auth_header, request_type)
                 elif auth_header.startswith('Digest '):
                     return self._verify_digest_auth(mount, auth_header, request_type)
                 elif not auth_header and hasattr(self, 'ntrip1_password') and self.ntrip1_password:
                   
                     password = self.ntrip1_password
                     
                     
                     is_valid, error_msg = self.db_manager.verify_mount_and_user(mount_name, username=None, password=None, mount_password=password, protocol_version="1.0")
                     if not is_valid:
                         return False, error_msg
                     
                     self.username = f"ntrip20_{mount_name}"
                     
                     return True, "Authentication successful"
                 else:
                     return False, "Invalid authorization format"
            
            elif self.protocol_type == "rtsp":
                 if auth_header.startswith('Basic '):
                     return self._verify_basic_auth(mount, auth_header, request_type)
                 elif auth_header.startswith('Digest '):
                     return self._verify_digest_auth(mount, auth_header, request_type)
                 elif not auth_header and hasattr(self, 'ntrip1_password') and self.ntrip1_password:

                     password = self.ntrip1_password
                     
                     is_valid, error_msg = self.db_manager.verify_mount_and_user(mount_name, username=None, password=None, mount_password=password, protocol_version="1.0")
                     if not is_valid:
                         return False, error_msg
                     
                     self.username = f"rtsp_{mount_name}"
                     
                     return True, "Authentication successful"
                 else:
                     return False, "Invalid authorization format"
            
            else:
                 if not auth_header:
                     return False, "Missing authorization"

                 if not auth_header.startswith('Basic '):
                     return False, "Invalid authorization format"
                 
                 encoded_credentials = auth_header[6:]
                 decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
                 
                 if ':' not in decoded_credentials:
                     return False, "Invalid credentials format"
                 
                 username, password = decoded_credentials.split(':', 1)
                 self.username = username
                 
                 # 验证挂载点和用户（默认使用1.0协议）
                 is_valid, error_msg = self.db_manager.verify_mount_and_user(mount_name, username, password, mount_password=password, protocol_version="1.0")
                 
                 if not is_valid:
                     return False, error_msg
                 
                 # 检查用户连接数限制
                 current_connections = connection.get_user_connection_count(username)
                 if current_connections >= MAX_CONNECTIONS_PER_USER:
                     return False, f"User connection limit exceeded (max: {MAX_CONNECTIONS_PER_USER})"
                 
                 return True, "Authentication successful"
        
        except Exception as e:
            logger.log_error(f"用户验证异常: {e}", exc_info=True)
            return False, "Authentication error"
    
    def _verify_basic_auth(self, mount, auth_header, request_type="upload"):
        """验证Basic认证"""
        try:
            # 统一处理挂载点名称
            mount_name = mount.lstrip('/')
            
            encoded_credentials = auth_header[6:]
            

            try:
                decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
            except (ValueError, UnicodeDecodeError) as e:
                logger.log_debug(f"Basic认证解码失败 {self.client_address}: {e}", 'ntrip')
                return False, "Invalid credentials format"
            
            if ':' not in decoded_credentials:
                return False, "Invalid credentials format"
            
            username, password = decoded_credentials.split(':', 1)
            self.username = username

            if request_type == "download":
                
                is_valid, error_msg = self.db_manager.verify_download_user(mount_name, username, password)
            else:
                
                if self.protocol_type == "ntrip2_0":
                    
                    is_valid, error_msg = self.db_manager.verify_mount_and_user(mount_name, username, password, mount_password=None, protocol_version="2.0")
                else:
                    
                    is_valid, error_msg = self.db_manager.verify_mount_and_user(mount_name, username, password, mount_password=password, protocol_version="1.0")
            
            if not is_valid:
                return False, error_msg
            

            current_connections = connection.get_user_connection_count(username)
            if current_connections >= MAX_CONNECTIONS_PER_USER:
                return False, f"User connection limit exceeded (max: {MAX_CONNECTIONS_PER_USER})"
            
            return True, "Authentication successful"
        except Exception as e:
            logger.log_error(f"Basic认证异常: {e}", exc_info=True)
            return False, "Authentication error"
    
    def _verify_digest_auth(self, mount, auth_header, request_type="upload"):
        """验证Digest认证"""
        try:
            
            mount_name = mount.lstrip('/')
            
            digest_params = self._parse_digest_auth(auth_header)
            
            if not digest_params:
                return False, "Invalid digest format"
            
            username = digest_params.get('username')
            if not username:
                return False, "Missing username in digest"
            
            self.username = username
            
            stored_password = self.db_manager.get_user_password(username)
            if not stored_password:
                return False, "Invalid credentials"

            if not self._validate_digest_response(digest_params, stored_password, mount_name):
                return False, "Invalid digest response"

            if request_type == "download":
               
                is_valid, error_msg = self.db_manager.verify_download_user(mount_name, username, stored_password)
            else:
                
                if self.protocol_type == "ntrip2_0":
                    
                    is_valid, error_msg = self.db_manager.verify_mount_and_user(mount_name, username, stored_password, mount_password=None, protocol_version="2.0")
                else:
                    
                    is_valid, error_msg = self.db_manager.verify_mount_and_user(mount_name, username, stored_password, mount_password=stored_password, protocol_version="1.0")
            
            if not is_valid:
                return False, error_msg
            
            current_connections = connection.get_user_connection_count(username)
            if current_connections >= MAX_CONNECTIONS_PER_USER:
                return False, f"User connection limit exceeded (max: {MAX_CONNECTIONS_PER_USER})"
            
            return True, "Authentication successful"
        except Exception as e:
            logger.log_error(f"Digest认证异常: {e}", exc_info=True)
            return False, "Authentication error"
    
    def _parse_digest_auth(self, auth_header):
        """解析Digest认证头部"""
        import re
        
        digest_pattern = r'(\w+)=(?:"([^"]*)"|([^,\s]*))'  
        matches = re.findall(digest_pattern, auth_header[7:])
        
        params = {}
        for match in matches:
            key = match[0]
            value = match[1] if match[1] else match[2]
            params[key] = value
        
        return params
    
    def _validate_digest_response(self, params, password, uri):
        """验证Digest响应"""
        import hashlib
        
        try:
            username = params.get('username')
            realm = params.get('realm')
            nonce = params.get('nonce')
            response = params.get('response')
            method = getattr(self, 'current_method', 'GET')
            
            if not all([username, realm, nonce, response]):
                return False
            
            # HA1
            ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
            
            # HA2
            ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
            
            
            expected_response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
            
            return response.lower() == expected_response.lower()
        except Exception:
            return False
    

    
    def handle_options(self, headers):
        """处理OPTIONS请求（CORS预检等）"""
        try:
            logger.log_debug(f"OPTIONS请求 {self.client_address}")
            
            # CORS响应头 - 已移除，NTRIP协议不需要CORS
            # NTRIP客户端不是浏览器，不受CORS限制
            
            # 使用标准响应方法，确保包含Content-Length
            self._send_response(
                "HTTP/1.1 200 OK",
                content_type="text/plain",
                content=""
            )
            
            logger.log_debug(f"OPTIONS请求处理完成 {self.client_address}")
            
        except Exception as e:
            logger.log_error(f"OPTIONS请求处理异常 {self.client_address}: {e}", exc_info=True)
            self.send_error_response(500, "Internal Server Error")
    
    def handle_rtsp_command(self, method, path, headers):
        """处理RTSP协议命令"""
        try:
            # 提取挂载点名称
            if path.startswith('rtsp://'):
                # 从RTSP URL中提取挂载点
                from urllib.parse import urlparse
                parsed = urlparse(path)
                mount = parsed.path.lstrip('/')
            else:
                mount = path.lstrip('/')
            
            if not mount:
                self.send_error_response(400, "Missing mount point")
                return
            
            self.mount = mount
            
            # 验证用户（RTSP可能使用不同的认证机制）
            auth_header = headers.get('authorization', '')
            is_valid, message = self.verify_user(mount, auth_header)
            
            if not is_valid:
                self.send_auth_challenge(message)
                return
            
            # 根据RTSP命令类型处理
            if method.upper() == 'DESCRIBE':
                self._handle_rtsp_describe(mount, headers)
            elif method.upper() == 'SETUP':
                self._handle_rtsp_setup(mount, headers)
            elif method.upper() == 'PLAY':
                self._handle_rtsp_play(mount, headers)
            elif method.upper() == 'PAUSE':
                self._handle_rtsp_pause(mount, headers)
            elif method.upper() == 'TEARDOWN':
                self._handle_rtsp_teardown(mount, headers)
            elif method.upper() == 'RECORD':
                self._handle_rtsp_record(mount, headers)
            else:
                self.send_error_response(501, f"RTSP method not implemented: {method}")
                
        except Exception as e:
            logger.log_error(f"处理RTSP命令异常: {e}", exc_info=True)
            self.send_error_response(500, "Internal Server Error")
    
    def _handle_rtsp_describe(self, mount, headers):
        """处理RTSP DESCRIBE命令"""
        # 检查挂载点是否存在
        if not connection.check_mount_exists(mount):
            self.send_error_response(404, "Mount point not found")
            return
        
        # 生成SDP描述
        sdp_content = self._generate_sdp_description(mount)
        
        rtsp_headers = {
            'Content-Type': 'application/sdp',
            'Content-Length': str(len(sdp_content))
        }
        
        self._send_response('RTSP/1.0 200 OK', content_type='application/sdp', 
                          content=sdp_content, additional_headers=rtsp_headers)
    
    def _handle_rtsp_setup(self, mount, headers):
        """处理RTSP SETUP命令"""
        # 检查挂载点是否存在
        if not connection.check_mount_exists(mount):
            cseq = headers.get('cseq', '1')
            response_headers = {'CSeq': cseq}
            self._send_response("RTSP/1.0 404 Not Found", additional_headers=response_headers)
            return
        
        # 解析Transport头
        transport = headers.get('transport', 'RTP/AVP;unicast')
        client_port = '8000-8001'  # 默认端口
        
        # 提取客户端端口信息
        if 'client_port=' in transport:
            try:
                client_port = transport.split('client_port=')[1].split(';')[0]
            except:
                pass
        
        session_id = f"{mount}-{int(time.time())}"
        
        cseq = headers.get('cseq', '1')
        rtsp_headers = {
            'CSeq': cseq,
            'Transport': f'RTP/AVP;unicast;client_port={client_port};server_port=8002-8003',
            'Session': session_id,
            'Cache-Control': 'no-cache'
        }
        
        self._send_response('RTSP/1.0 200 OK', additional_headers=rtsp_headers)
    
    def _handle_rtsp_play(self, mount, headers):
        """处理RTSP PLAY命令"""
        cseq = headers.get('cseq', '1')
        session = headers.get('session', '')
        
        rtsp_headers = {
            'CSeq': cseq,
            'Session': session,
            'Range': 'npt=0.000-',
            'RTP-Info': f'url=rtsp://{config.HOST if config.HOST != "0.0.0.0" else "localhost"}:{config.NTRIP_PORT}/{mount};seq=1;rtptime=0'
        }
        
        self._send_response('RTSP/1.0 200 OK', additional_headers=rtsp_headers)
        # 开始数据流传输
        self.handle_download('/' + mount, headers)
    
    def _handle_rtsp_pause(self, mount, headers):
        """处理RTSP PAUSE命令"""
        cseq = headers.get('cseq', '1')
        session = headers.get('session', '')
        
        rtsp_headers = {
            'CSeq': cseq,
            'Session': session
        }
        
        self._send_response('RTSP/1.0 200 OK', additional_headers=rtsp_headers)
    
    def _handle_rtsp_teardown(self, mount, headers):
        """处理RTSP TEARDOWN命令"""
        cseq = headers.get('cseq', '1')
        session = headers.get('session', '')
        
        rtsp_headers = {
            'CSeq': cseq,
            'Session': session
        }
        
        self._send_response('RTSP/1.0 200 OK', additional_headers=rtsp_headers)
        # 清理连接
        self._cleanup()
    
    def _handle_rtsp_record(self, mount, headers):
        """处理RTSP RECORD命令"""
        cseq = headers.get('cseq', '1')
        session = headers.get('session', '')
        
        rtsp_headers = {
            'CSeq': cseq,
            'Session': session
        }
        
        self._send_response('RTSP/1.0 200 OK', additional_headers=rtsp_headers)
        
        self.handle_upload('/' + mount, headers)
    
    def _generate_sdp_description(self, mount):
        """生成SDP描述"""
        # 获取实际的IP地址用于SDP描述
        origin_ip = config.HOST if config.HOST != "0.0.0.0" else "127.0.0.1"
        sdp = f"""v=0
o=- 0 0 IN IP4 {origin_ip}
s=NTRIP Stream {mount}
c=IN IP4 0.0.0.0
t=0 0
m=application 0 RTP/AVP 96
a=rtpmap:96 rtcm/1000
a=control:*
"""
        return sdp
    
    def handle_upload(self, path, headers):
        """处理上传请求"""
        try:
            # 使用防刷屏机制记录HANDLE_UPLOAD日志
            message_key = f"handle_upload_{self.client_address[0]}_{path}"
            if anti_spam_logger.should_log(message_key):
                suppressed = anti_spam_logger.get_suppressed_count(message_key)
                if suppressed > 0:
                    logger.log_info(f"HANDLE_UPLOAD 被调用 {self.client_address}: path={path} (已抑制{suppressed}条相似消息)")
                else:
                    logger.log_info(f"HANDLE_UPLOAD 被调用 {self.client_address}: path={path}")
            logger.log_debug(f"handle_upload开始处理 {self.client_address}: path={path}")
            
            # 打印当前连接状态
            # print(f"\n>>> 新的上传请求 - IP: {self.client_address[0]}, 挂载点: {path.lstrip('/')}, 时间: {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
            # print(f">>> 请求详情 - 方法: POST, 路径: {path}, 用户代理: {headers.get('User-Agent', 'Unknown')}")
            
            connection.get_connection_manager().cleanup_zombie_connections()
            connection.get_connection_manager().force_refresh_connections()
            
            # 提取挂载点名称
            mount = path.lstrip('/')
            if not mount:
                self.send_error_response(400, "Missing mount point")
                return
            
            self.mount = mount
             
            if connection.get_connection_manager().is_mount_online(mount):
                existing_mount = connection.get_connection_manager().get_mount_info(mount)
                if existing_mount and existing_mount['ip_address'] != self.client_address[0]:
                    
                    message_key = f"mount_occupied_{mount}_{existing_mount['ip_address']}"
                    if anti_spam_logger.should_log(message_key):
                        suppressed = anti_spam_logger.get_suppressed_count(message_key)
                        if suppressed > 0:
                            logger.log_warning(f"挂载点 {mount} 已被 {existing_mount['ip_address']} 占用，拒绝来自 {self.client_address[0]} 的连接 (已抑制{suppressed}条相似消息)")
                        else:
                            logger.log_warning(f"挂载点 {mount} 已被 {existing_mount['ip_address']} 占用，拒绝来自 {self.client_address[0]} 的连接")
                    self.send_error_response(409, f"Mount point {mount} is already online from {existing_mount['ip_address']}")
                    
                    try:
                        self.client_socket.close()
                    except:
                        pass
                    return
                elif existing_mount and existing_mount['ip_address'] == self.client_address[0]:
                    logger.log_warning(f"检测到相同IP({self.client_address[0]})的重复连接，可能是连接异常，允许重新连接")
                    
                    connection.get_connection_manager().remove_mount_connection(mount, "相同IP重复连接")
            
            # 所有请求都必须通过完整的数据库验证，确保挂载点存在且密码正确
            auth_header = headers.get('authorization', '')
            logger.log_info(f"handle_upload开始验证 {self.client_address}: mount={mount}, auth_header={auth_header[:50] if auth_header else 'None'}")
            is_valid, message = self.verify_user(mount, auth_header)
            
            logger.log_info(f"handle_upload验证结果 {self.client_address}: is_valid={is_valid}, message={message}")
            
            if not is_valid:
                logger.log_warning(f"handle_upload认证失败 {self.client_address}: {message}")
                self.send_auth_challenge(message)
                # 认证失败时直接关闭socket
                try:
                    self.client_socket.close()
                except:
                    pass
                return
             
            try:
                success, message = connection.get_connection_manager().add_mount_connection(mount, self.client_address[0], getattr(self, 'user_agent', 'Unknown'), getattr(self, 'ntrip_version', '1.0'), self.client_socket)
                if not success:
                    logger.log_warning(f"挂载点 {mount} 连接被拒绝: {message}")
                    logger.log_info(f"连接拒绝详情 - 挂载点: {mount}, IP: {self.client_address[0]}, 原因: {message}")
                    self.send_error_response(409, message)
                    
                    try:
                        self.client_socket.close()
                    except:
                        pass
                    return

                self.mount_connection_established = True
                
                if success:
                    logger.log_info(f"挂载点 {mount} 已成功添加到连接管理器: {message}")
                else:
                    logger.log_warning(f"挂载点 {mount} 添加到连接管理器失败: {message}")
            except Exception as e:
                logger.log_error(f"添加挂载点 {mount} 到连接管理器时发生异常: {e}", exc_info=True)

            self.send_upload_success_response()
            
            username_for_log = getattr(self, 'username', mount) if hasattr(self, 'username') else mount
            logger.log_mount_operation('upload_connected', mount, username_for_log)
            
            logger.log_info(f"=== 开始接收RTCM数据 ===: mount={mount}")
            self._receive_rtcm_data(mount)
        
        except Exception as e:
            logger.log_error(f"处理上传请求异常: {e}", exc_info=True)
            self.send_error_response(500, "Internal Server Error")
    
    def handle_download(self, path, headers):
        """处理下载请求"""
        try:
            
            if path.strip().lower() in ['/', '', '/sourcetable']:
                self._send_mount_list()
                return
            
            mount = path.lstrip('/')
            self.mount = mount

            auth_header = next((v for k, v in headers.items() if k.lower() == 'authorization'), '')
            is_valid, message = self.verify_user(mount, auth_header, "download")
            
            if not is_valid:
                self.send_auth_challenge(message)
                return
            
            if not self.db_manager.check_mount_exists_in_db(mount):
                self.send_error_response(404, "Mount point not found")
                return
            
            # 添加到连接管理器
            connection_id = connection.add_user_connection(self.username, mount, self.client_address[0])
            
            # 添加客户端到转发器
            try:
                self.client_info = forwarder.add_client(self.client_socket, self.username, mount,
                                                       self.user_agent, self.client_address, 
                                                       self.ntrip_version, connection_id)
                if not self.client_info:
                    self.send_error_response(500, "Failed to add client")
                    return
            except Exception as e:
                logger.log_error(f"添加客户端失败: {e}", exc_info=True)
                self.send_error_response(500, "Failed to add client")
                return
            
            self.send_download_success_response()
            
            logger.log_client_connect(self.username, mount, self.client_address[0], self.user_agent)
            
            self._keep_connection_alive()
        
        except Exception as e:
            logger.log_error(f"处理下载请求异常: {e}", exc_info=True)
            self.send_error_response(500, "Internal Server Error")
    
    def handle_http_get(self, path, headers):
        """处理普通HTTP GET请求"""
        try:
            if path == '/' or path == '':
                content = "<!DOCTYPE html><html><head><title>NTRIP Caster</title></head><body><h1>NTRIP Caster Server</h1><p>This is an NTRIP Caster server.</p></body></html>"
                self._send_response(
                    "HTTP/1.1 200 OK",
                    content_type="text/html",
                    content=content
                )
            else:
                self.send_error_response(404, "Not Found")
        except Exception as e:
            logger.log_error(f"处理HTTP GET请求异常: {e}", exc_info=True)
            self.send_error_response(500, "Internal Server Error")
    
    def _receive_rtcm_data(self, mount):
        """接收RTCM数据循环"""
        try:
            while True:
                try:
                    data = self.client_socket.recv(BUFFER_SIZE)
                    if not data:
                        # 连接已关闭
                        logger.log_debug(f"挂载点 {mount} 连接已关闭", 'ntrip')
                        break
                    
                    forwarder.upload_data(mount, data)

                    connection.get_connection_manager().update_mount_data_stats(mount, len(data))
                    
                except OSError as e:
                    
                    if e.winerror == 10038:  #10038 
                        logger.log_debug(f"挂载点 {mount} socket已被关闭，停止接收数据", 'ntrip')
                    else:
                        logger.log_error(f"挂载点 {mount} socket错误: {e}", 'ntrip')
                    break
                except socket.timeout:
                    logger.log_debug(f"挂载点 {mount} 数据接收超时", 'ntrip')
                    continue
        
        except Exception as e:
            logger.log_error(f"接收RTCM数据异常: {e}", exc_info=True)
        finally:
            
            def delayed_cleanup():
                """延迟清理函数"""
                try:
                    forwarder.remove_mount_buffer(mount)
                except Exception as e:
                    logger.log_warning(f"清理转发器缓冲区失败: {e}", 'ntrip')
                
                try:
                    connection.get_connection_manager().remove_mount_connection(mount)
                except Exception as e:
                    log_warning(f"清理挂载点连接失败: {e}")
                

                logger.log_mount_operation('disconnected', mount)
                # 改为debug级别，避免频繁日志
                log_debug(f"挂载点 {mount} 延迟清理完成")
            
            # 记录断开事件，改为warning级别以确保重要信息被记录
            log_warning(f"挂载点 {mount} 连接断开，将在1.5秒后清理数据")
            

            cleanup_timer = threading.Timer(1.5, delayed_cleanup)
            cleanup_timer.daemon = True  # 设置为守护线程
            cleanup_timer.start()
            

            self._cleanup()
    
    def _keep_connection_alive(self):
        """保持下载连接活跃）"""
        try:
            
            while True:
                time.sleep(5)  
                
                if hasattr(self, 'client_info') and self.client_info:
                    
                    try:
                        
                        self.client_socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    except (OSError, AttributeError):
                        break
                else:
                    break
        except:
            
            pass
        finally:
            
            if hasattr(self, 'client_info') and self.client_info:
                forwarder.remove_client(self.client_info)
                logger.log_client_disconnect(self.username, self.mount, self.client_address[0])
    
    def _send_mount_list(self):
        """发送挂载点列表"""
        try:
            from . import config
            from datetime import datetime
            
            mount_list = connection.generate_mount_list()
            logger.log_debug(f"生成的挂载点列表: {mount_list}", 'ntrip')
            
            
            content_lines = []
            
            # 添加CAS信息（Caster信息）
            # 复用现有配置: server_name=author, server_port=NTRIP_PORT, operator=APP_NAME, network_name=author, website_url=APP_WEBSITE, fallback_ip=HOST
            cas_line = f"CAS;{config.APP_AUTHOR};{config.NTRIP_PORT};{config.APP_NAME};{config.APP_AUTHOR};0;{config.CASTER_COUNTRY};{config.CASTER_LATITUDE};{config.CASTER_LONGITUDE};{config.HOST};0;{config.APP_WEBSITE}"
            content_lines.append(cas_line)
            
            # 添加NET信息（Network信息）
            net_line = f"NET;{config.APP_AUTHOR};{config.APP_AUTHOR};B;{config.CASTER_COUNTRY};{config.APP_WEBSITE};{config.APP_WEBSITE};{config.APP_CONTACT};none"
            content_lines.append(net_line)
            
            # 添加STR表数据
            content_lines.extend(mount_list)
            
            # 将内容转换为字符串
            content_str = '\r\n'.join(content_lines) + '\r\n' if content_lines else '\r\n'
            log_debug(f"挂载点列表内容长度: {len(content_str)}")
            
            if self.ntrip_version == "2.0":
                # NTRIP 2.0格式 - 使用标准HTTP响应
                current_time = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
                
                # 构建HTTP响应头
                response_lines = [
                    "HTTP/1.1 200 OK",
                    f"Server: NTRIP 2RTK caster {config.APP_VERSION}",
                    f"Date: {current_time}",
                    "Ntrip-Version: Ntrip/2.0",
                    f"Content-Length: {len(content_str.encode('utf-8'))}",
                    "Content-Type: text/plain",
                    "Connection: close",
                    "",  # 空行分隔头部和内容
                    content_str
                ]
                
                response = '\r\n'.join(response_lines)
                try:
                    self.client_socket.send(response.encode('utf-8'))
                    log_debug(f"发送NTRIP 2.0格式挂载点列表到 {self.client_address}")
                except Exception as e:
                    logger.log_error(f"发送NTRIP 2.0挂载点列表失败: {e}", exc_info=True)
            else:
                # NTRIP 1.0格式 - 使用SOURCETABLE格式
                current_time = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
                
                response_lines = [
                    "SOURCETABLE 200 OK",
                    f"Server: NTRIP 2RTK caster {config.APP_VERSION}",
                    f"Date: {current_time}",
                    "Ntrip-Version: Ntrip/1.0",
                    f"Content-Length: {len(content_str.encode('utf-8'))}",
                    "Content-Type: text/plain",
                    "Connection: close",
                    "",  # 空行分隔头部和内容
                    content_str,
                    "ENDSOURCETABLE"
                ]
                
                response = '\r\n'.join(response_lines)
                log_debug(f"NTRIP 1.0响应内容: {repr(response[:200])}...")
                try:
                    self.client_socket.send(response.encode('utf-8'))
                    log_debug(f"发送NTRIP 1.0格式挂载点列表到 {self.client_address}")
                except Exception as e:
                    logger.log_error(f"发送NTRIP 1.0挂载点列表失败: {e}", exc_info=True)
            
            log_debug(f"发送挂载点列表到 {self.client_address}")
        
        except Exception as e:
            log_error(f"发送挂载点列表异常: {e}", exc_info=True)
    
    def send_upload_success_response(self):
        """发送上传成功响应"""
        if self.ntrip_version == "2.0":
            self._send_response(
                "HTTP/1.1 200 OK",
                additional_headers=["Connection: keep-alive"]
            )
        else:
            # NTRIP 1.0格式
            try:
                response = "ICY 200 OK\r\n\r\n"
                self.client_socket.send(response.encode('utf-8'))
            except Exception as e:
                logger.log_error(f"发送上传成功响应失败: {e}", exc_info=True)
    
    def send_download_success_response(self):
        """发送下载成功响应"""
        if self.ntrip_version == "2.0":
            self._send_response(
                "HTTP/1.1 200 OK",
                content_type="application/octet-stream",
                additional_headers=["Connection: keep-alive"]
            )
        else:
            # NTRIP 1.0格式 - 强制保持连接，忽略客户端的Connection: close
            try:
                response = "ICY 200 OK\r\nConnection: keep-alive\r\n\r\n"
                self.client_socket.send(response.encode('utf-8'))
                logger.log_debug(f"NTRIP 1.0下载响应已发送，保持长连接: {self.client_address}", 'ntrip')
            except Exception as e:
                logger.log_error(f"发送下载成功响应失败: {e}", exc_info=True)
    
    def send_auth_challenge(self, message="Authentication required", auth_type="both"):
        """发送认证挑战"""
        import secrets
        import time
        
        # 生成nonce用于Digest认证
        nonce = secrets.token_hex(16)
        realm = "NTRIP"
        
        # 构建认证头
        auth_headers = []
        if auth_type in ["basic", "both"]:
            auth_headers.append(f'WWW-Authenticate: Basic realm="{realm}"')
        
        if auth_type in ["digest", "both"]:
            digest_header = f'WWW-Authenticate: Digest realm="{realm}", nonce="{nonce}", algorithm=MD5, qop="auth"'
            auth_headers.append(digest_header)
        
        if self.ntrip_version == "2.0":
            self._send_response(
                "HTTP/1.1 401 Unauthorized",
                content_type="text/plain",
                content=message,
                additional_headers=auth_headers
            )
        else:
            # NTRIP 1.0格式
            try:
                response = "SOURCETABLE 401 Unauthorized\r\n"
                for header in auth_headers:
                    response += f"{header}\r\n"
                response += "\r\n"
                self.client_socket.send(response.encode('utf-8'))
            except Exception as e:
                logger.log_error(f"发送认证挑战失败: {e}", exc_info=True)
    
    def send_error_response(self, code, message):
        """发送HTTP错误响应"""
        if self.ntrip_version == "2.0":
            # 获取标准HTTP状态消息
            status_messages = {
                400: "Bad Request",
                401: "Unauthorized", 
                404: "Not Found",
                405: "Method Not Allowed",
                409: "Conflict",
                500: "Internal Server Error"
            }
            status_text = status_messages.get(code, "Error")
            
            self._send_response(
                f"HTTP/1.1 {code} {status_text}",
                content_type="text/plain",
                content=message
            )
        else:
            # NTRIP 1.0格式
            try:
                response = f"ERROR {code} {message}\r\n\r\n"
                self.client_socket.send(response.encode('utf-8'))
            except Exception as e:
                logger.log_error(f"发送错误响应失败: {e}", exc_info=True)
    
    def _generate_standard_headers(self, additional_headers=None):
        """生成标准HTTP响应头"""
        current_time = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        headers = []
        
        # 根据协议版本添加相应头部
        if self.protocol_type == "ntrip2_0":
            # NTRIP 2.0必需头部字段（参考ntrip_header_element）
            headers.append("Ntrip-Version: NTRIP/2.0")
            headers.append("Cache-Control: no-cache, no-store, must-revalidate")
            headers.append("Pragma: no-cache")
            headers.append("Expires: 0")
        elif self.protocol_type == "rtsp":
            headers.append("CSeq: 1")
            headers.append(f"Session: {id(self)}")
        elif self.ntrip_version == "2.0":
            headers.append("Ntrip-Version: NTRIP/2.0")
        
        # 通用头部字段
        headers.append(f"Date: {current_time}")
        headers.append(f"Server: {config.APP_NAME}/{config.VERSION}")
        
        # 安全相关头部
        headers.append("X-Content-Type-Options: nosniff")
        headers.append("X-Frame-Options: DENY")
        
        if additional_headers:
            headers.extend(additional_headers)
        
        return "\r\n".join(headers) + "\r\n"
    
    def _send_response(self, status_line, content_type=None, content=None, additional_headers=None):
        """发送标准化HTTP响应"""
        try:
            response = status_line + "\r\n"
            
            headers = []
            if content_type:
                headers.append(f"Content-Type: {content_type}")
            if content:
                headers.append(f"Content-Length: {len(content)}")
            
            response += self._generate_standard_headers(headers + (additional_headers or []))
            response += "\r\n"
            
            if content:
                response += content
            
            self.client_socket.send(response.encode('utf-8'))
            
        except Exception as e:
            logger.log_error(f"发送响应失败: {e}", exc_info=True)
    
    def _cleanup(self):
        """清理资源"""
        try:
            # print(f"\n>>> 连接清理开始 - IP: {self.client_address[0]}, 时间: {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")

            if hasattr(self, 'username') and hasattr(self, 'mount'):
                if hasattr(self, 'client_info'):  # 下载连接
                    # print(f">>> 移除用户连接 - 用户: {self.username}, 挂载点: {self.mount}")
                    connection.remove_user_connection(self.username, self.client_address[0], self.mount)
                else:  # 上传连接
                    # 只有真正成功建立的挂载点连接才在断开时移除
                    if hasattr(self, 'mount_connection_established') and self.mount_connection_established:
                        # print(f">>> 移除挂载点连接 - 挂载点: {self.mount}")
                        connection.remove_mount_connection(self.mount)
                    else:
                        # print(f">>> 跳过移除挂载点连接 - 挂载点: {self.mount} (连接未成功建立)")
                        pass
            else:
                # print(f">>> 跳过连接移除 - username存在: {hasattr(self, 'username')}, mount存在: {hasattr(self, 'mount')}") 
                pass
            
            self.client_socket.close()
            # print(f">>> 连接清理完成 - IP: {self.client_address[0]}")
        except Exception as e:
            logger.log_error(f"清理资源时出错: {e}", exc_info=True)

class NTRIPCaster:
    """NTRIP Caster服务器 - 使用线程池处理高并发连接"""
    
    def __init__(self, db_manager):
        self.server_socket = None
        self.running = False
        self.db_manager = db_manager

        self.thread_pool = None
        self.connection_queue = Queue(maxsize=CONNECTION_QUEUE_SIZE)
        self.active_connections = 0
        self.connection_lock = threading.Lock()

        self.total_connections = 0
        self.rejected_connections = 0
    
    def start(self):
        """启动NTRIP服务器"""
        try:
            
            self._start_ntrip_server()
            
            log_system_event(f'NTRIP服务器已启动，监听端口: {NTRIP_PORT}')
            
            self._main_loop()
        
        except Exception as e:
            log_error(f"启动NTRIP服务器失败: {e}", exc_info=True)
            self.stop()
    
    def _start_ntrip_server(self):
        """启动NTRIP服务器"""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(('0.0.0.0', NTRIP_PORT))
        self.server_socket.listen(MAX_CONNECTIONS)
        self.running = True
        
        self.thread_pool = ThreadPoolExecutor(
            max_workers=MAX_WORKERS,
            thread_name_prefix="NTRIP-Worker"
        )
        
        self._start_connection_handler()

        ntrip_urls = config.get_display_urls(NTRIP_PORT, "NTRIP服务器")
        if len(ntrip_urls) == 1:
            log_system_event(f'NTRIP服务器已启动，监听地址: {ntrip_urls[0]}')
        else:
            log_system_event('NTRIP服务器已启动，可通过以下地址访问:')
            for url in ntrip_urls:
                log_system_event(f'  - {url}')
        
        log_system_event(f'线程池大小: {MAX_WORKERS}, 连接队列大小: {CONNECTION_QUEUE_SIZE}')
    

    def _main_loop(self):
        """主循环，接受客户端连接"""
        while self.running:
            try:
                client_socket, client_address = self.server_socket.accept()
                
                # 检查连接数限制
                with self.connection_lock:
                    if self.active_connections >= MAX_CONNECTIONS:
                        log_warning(f"连接数已达上限 {MAX_CONNECTIONS}，拒绝连接 {client_address}")
                        client_socket.close()
                        self.rejected_connections += 1
                        continue

                try:
                    self.connection_queue.put((client_socket, client_address), timeout=1.0)
                    with self.connection_lock:
                        self.total_connections += 1
                    log_info(f"接受连接来自 {client_address}, 队列大小: {self.connection_queue.qsize()}, 活跃连接: {self.active_connections}")
                except Full:
                    log_warning(f"连接队列已满，拒绝连接 {client_address}")
                    client_socket.close()
                    self.rejected_connections += 1
            
            except socket.error as e:
                if self.running:
                    log_error(f"接受连接异常: {e}", exc_info=True)
                break
            except Exception as e:
                log_error(f"主循环异常: {e}", exc_info=True)
                break
    
    def _start_connection_handler(self):
        """启动连接处理器线程"""
        handler_thread = Thread(target=self._connection_handler, daemon=True)
        handler_thread.start()
        log_debug("连接处理器已启动")
    
    def _connection_handler(self):
        """连接处理器，从队列中取出连接并提交给线程池"""
        while self.running:
            try:
                
                client_socket, client_address = self.connection_queue.get(timeout=1.0)
                
                future = self.thread_pool.submit(self._handle_client_connection, client_socket, client_address)

                with self.connection_lock:
                    self.active_connections += 1
                
                log_info(f"连接 {client_address} 已提交给线程池处理")
                
            except Empty:
                
                continue
            except Exception as e:
                log_error(f"连接处理器异常: {e}", exc_info=True)
    
    def _handle_client_connection(self, client_socket, client_address):
        """处理单个客户端连接"""
        try:
            
            handler = NTRIPHandler(client_socket, client_address, self.db_manager)
            handler.handle_request()
        except Exception as e:
            log_error(f"处理客户端连接 {client_address} 时发生异常: {e}", exc_info=True)
        finally:
           
            with self.connection_lock:
                self.active_connections -= 1
            
            try:
                client_socket.close()
            except:
                pass
            
            log_info(f"客户端连接 {client_address} 处理完成，活跃连接: {self.active_connections}")
    
    def get_performance_stats(self):
        """获取性能统计信息"""
        with self.connection_lock:
            return {
                'active_connections': self.active_connections,
                'total_connections': self.total_connections,
                'rejected_connections': self.rejected_connections,
                'queue_size': self.connection_queue.qsize(),
                'max_connections': MAX_CONNECTIONS,
                'max_workers': MAX_WORKERS,
                'connection_queue_size': CONNECTION_QUEUE_SIZE
            }
    
    def log_performance_stats(self):
        """记录性能统计信息"""
        stats = self.get_performance_stats()
        log_info(
            f"性能统计 - 活跃连接: {stats['active_connections']}/{stats['max_connections']}, "
            f"队列大小: {stats['queue_size']}/{stats['connection_queue_size']}, "
            f"总连接: {stats['total_connections']}, 拒绝: {stats['rejected_connections']}"
        )
    
    def stop(self):
        """停止NTRIP服务器"""
        log_system_event('正在关闭NTRIP服务器')
        
        self.running = False
        
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
        
        if self.thread_pool:
            logger.log_system_event("正在关闭线程池...")
            
            self.thread_pool.shutdown(wait=True)
            log_system_event("线程池已关闭")
        
        while not self.connection_queue.empty():
            try:
                client_socket, client_address = self.connection_queue.get_nowait()
                client_socket.close()
                log_debug(f"清理队列中的连接: {client_address}")
            except Empty:
                break
            except Exception as e:
                log_error(f"清理连接队列时发生异常: {e}", exc_info=True)
        
        
        log_system_event(f'NTRIP服务器已停止 - 总连接数: {self.total_connections}, 拒绝连接数: {self.rejected_connections}')
        log_system_event('NTRIP服务器已关闭')

