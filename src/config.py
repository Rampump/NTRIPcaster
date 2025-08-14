#!/usr/bin/env python3
"""
config.py - 配置文件
从config.ini文件读取NTRIP Caster的所有配置参数
"""

import os
import socket
import configparser
from pathlib import Path
from typing import List, Tuple

CONFIG_FILE = os.environ.get('NTRIP_CONFIG_FILE', 
                            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.ini'))

config = configparser.ConfigParser()

if os.path.exists(CONFIG_FILE):
    print(f"加载配置文件: {CONFIG_FILE}")
    config.read(CONFIG_FILE, encoding='utf-8')
else:
    raise FileNotFoundError(f"配置文件 {CONFIG_FILE} 不存在")

def get_config_value(section, key, fallback=None, value_type=str):
    """获取配置值并转换类型"""
    try:
        if value_type == bool:
            return config.getboolean(section, key, fallback=fallback)
        elif value_type == int:
            return config.getint(section, key, fallback=fallback)
        elif value_type == float:
            return config.getfloat(section, key, fallback=fallback)
        elif value_type == list:
            value = config.get(section, key, fallback='')
            return [item.strip() for item in value.split(',') if item.strip()] if value else fallback or []
        else:
            return config.get(section, key, fallback=fallback)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return fallback

# ==================== 基本配置 ====================

# 基本应用信息
APP_NAME = get_config_value('app', 'name', '2RTK Ntrip Caster')
APP_VERSION = get_config_value('app', 'version', '2.0.0')
APP_DESCRIPTION = get_config_value('app', 'description', '高性能NTRIP数据转发服务器')
APP_AUTHOR = get_config_value('app', 'author', '2RTK Team')
APP_CONTACT = get_config_value('app', 'contact', 'support@2rtk.com')
APP_WEBSITE = get_config_value('app', 'website', 'https://2rtk.com')


VERSION = APP_VERSION


DEBUG = get_config_value('development', 'debug_mode', True, bool)

# ==================== CASTER配置 ====================

# NTRIP Caster地理位置信息
CASTER_COUNTRY = get_config_value('caster', 'country', 'CHN')
CASTER_LATITUDE = get_config_value('caster', 'latitude', 25.2034, float)
CASTER_LONGITUDE = get_config_value('caster', 'longitude', 110.2777, float)

# ==================== 网络配置 ====================

def get_all_network_interfaces() -> List[Tuple[str, str]]:
    """获取所有网络接口的IP地址"""
    interfaces = []
    
    try:
        
        hostname = socket.gethostname()
        
        
        for info in socket.getaddrinfo(hostname, None):
            family, socktype, proto, canonname, sockaddr = info
            if family == socket.AF_INET:  # 只获取IPv4地址
                ip = sockaddr[0]
                if ip not in [addr[1] for addr in interfaces]:  # 避免重复
                    interfaces.append((f"Interface-{len(interfaces)+1}", ip))
    except Exception:
        pass
    
    
    if not any(addr[1] == '127.0.0.1' for addr in interfaces):
        interfaces.append(("Loopback", "127.0.0.1"))
    
    return interfaces

def get_private_ips() -> List[Tuple[str, str]]:
    """获取所有内网IP地址（仅检测实际可用的IP，不强制添加回环地址）"""
    private_ips = []
    
    try:
       
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            primary_ip = s.getsockname()[0]
            private_ips.append(("Primary", primary_ip))
    except Exception:
        pass
    
    
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            family, socktype, proto, canonname, sockaddr = info
            if family == socket.AF_INET:
                ip = sockaddr[0]
                if (ip.startswith('192.168.') or 
                    ip.startswith('10.') or 
                    ip.startswith('172.') or 
                    ip == '127.0.0.1'):
                    if ip not in [addr[1] for addr in private_ips]:
                        interface_name = f"Interface-{len(private_ips)+1}"
                        if ip == '127.0.0.1':
                            interface_name = "Loopback"
                        elif ip.startswith('192.168.'):
                            interface_name = "LAN"
                        private_ips.append((interface_name, ip))
    except Exception:
        pass
    
    return private_ips

def get_display_urls(port: int, service_name: str = "服务") -> List[str]:
    """获取用于显示的所有可访问URL"""
    urls = []
    
    listen_host = get_config_value('network', 'host', '0.0.0.0')
    
    if listen_host == '0.0.0.0':
        
        for interface_name, ip in get_private_ips():
            urls.append(f"http://{ip}:{port}")
    else:
        
        urls.append(f"http://{listen_host}:{port}")
    
    return urls

# 网络配置
HOST = get_config_value('network', 'host', '0.0.0.0') 


NTRIP_HOST = HOST  
NTRIP_PORT = get_config_value('ntrip', 'port', 2101, int)  # NTRIP服务端口

WEB_HOST = HOST  # Web服务监听地址
WEB_PORT = get_config_value('web', 'port', 5757, int)      # Web服务端口

# 最大连接数
MAX_CONNECTIONS = get_config_value('network', 'max_connections', 100, int)

# 缓冲区大小
BUFFER_SIZE = get_config_value('network', 'buffer_size', 8192, int)      # 8KB
MAX_BUFFER_SIZE = get_config_value('network', 'max_buffer_size', 65536, int) # 64KB

# ==================== 数据库配置 ====================

DATABASE_PATH = get_config_value('database', 'path', 'ntrip_caster.db')
DB_POOL_SIZE = get_config_value('database', 'pool_size', 10, int)
DB_TIMEOUT = get_config_value('database', 'timeout', 30, int)

# ==================== 日志配置 ====================

LOG_DIR = get_config_value('logging', 'log_dir', 'logs')
LOG_FILES = {
    'main': get_config_value('logging', 'main_log_file', 'main.log'),
    'ntrip': get_config_value('logging', 'ntrip_log_file', 'ntrip.log'), 
    'errors': get_config_value('logging', 'error_log_file', 'errors.log')
}

LOG_LEVEL = get_config_value('logging', 'log_level', "WARNING")

LOG_FORMAT = get_config_value('logging', 'log_format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')

LOG_MAX_SIZE = get_config_value('logging', 'max_log_size', 10 * 1024 * 1024, int)  # 10MB

LOG_BACKUP_COUNT = get_config_value('logging', 'backup_count', 5, int)  # 保留5个备份文件

LOG_FREQUENT_STATUS = get_config_value('logging', 'log_frequent_status', False, bool)

# Flask密钥 (生产环境中请修改)
SECRET_KEY = get_config_value('security', 'secret_key', 'your-secret-key-change-this-in-production')
FLASK_SECRET_KEY = SECRET_KEY  # Flask应用密钥

# 密码哈希配置
PASSWORD_HASH_ROUNDS = get_config_value('security', 'password_hash_rounds', 12, int)
SESSION_TIMEOUT = get_config_value('security', 'session_timeout', 3600, int)  # 1小时

# 默认管理员账户
DEFAULT_ADMIN = {
    'username': get_config_value('admin', 'username', 'admin'),
    'password': get_config_value('admin', 'password', 'admin123')
}

# ==================== NTRIP协议配置 ====================


SUPPORTED_NTRIP_VERSIONS = get_config_value('ntrip', 'supported_versions', ['1.0', '2.0'], list)

DEFAULT_NTRIP_VERSION = get_config_value('ntrip', 'default_version', '1.0')
MAX_USER_CONNECTIONS_PER_MOUNT = get_config_value('ntrip', 'max_user_connections_per_mount', 3, int)
MAX_USERS_PER_MOUNT = get_config_value('ntrip', 'max_users_per_mount', 3, int)  # 每个挂载点每个用户的最大连接数
MAX_CONNECTIONS_PER_USER = get_config_value('ntrip', 'max_connections_per_user', 3, int)  # 每个用户的最大连接数
MOUNT_TIMEOUT = get_config_value('ntrip', 'mount_timeout', 300, int)  # 5分钟
CLIENT_TIMEOUT = get_config_value('ntrip', 'client_timeout', 60, int)  # 1分钟
CONNECTION_TIMEOUT = get_config_value('ntrip', 'connection_timeout', 300, int)  # 连接超时时间 (秒)

# ==================== TCP配置 ====================

# TCP Keep-Alive配置
TCP_KEEPALIVE = {
    'enabled': get_config_value('tcp', 'keepalive_enabled', True, bool),
    'idle': get_config_value('tcp', 'keepalive_idle', 60, int),      # 开始发送keep-alive探测前的空闲时间
    'interval': get_config_value('tcp', 'keepalive_interval', 10, int),  # keep-alive探测间隔
    'count': get_config_value('tcp', 'keepalive_count', 3, int)       # 最大keep-alive探测次数
}
SOCKET_TIMEOUT = get_config_value('tcp', 'socket_timeout', 30, int)

# ==================== 数据转发配置 ====================

# 环形缓冲区配置
RING_BUFFER_SIZE = get_config_value('data_forwarding', 'ring_buffer_size', 2000, int)  # 缓冲区大小

BROADCAST_INTERVAL = get_config_value('data_forwarding', 'broadcast_interval', 0.01, float)  # 广播间隔 (秒)

DATA_SEND_TIMEOUT = get_config_value('data_forwarding', 'data_send_timeout', 5, int)  # 数据发送超时时间（秒）

CLIENT_HEALTH_CHECK_INTERVAL = get_config_value('data_forwarding', 'client_health_check_interval', 30, int)  # 客户端健康检查间隔（秒）

# ==================== RTCM解析 ====================

# RTCM解析间隔（秒）
RTCM_PARSE_INTERVAL = get_config_value('rtcm', 'parse_interval', 5, int)

# RTCM缓冲区大小
RTCM_BUFFER_SIZE = get_config_value('rtcm', 'buffer_size', 1000, int)

# RTCM数据解析时长（秒）- 用于修正STR表
RTCM_PARSE_DURATION = get_config_value('rtcm', 'parse_duration', 30, int)

# RTCM消息类型描述字典
RTCM_MESSAGE_DESCRIPTIONS = {
    1001: "L1-Only GPS RTK Observables",
    1002: "Extended L1-Only GPS RTK Observables", 
    1003: "L1&L2 GPS RTK Observables",
    1004: "Extended L1&L2 GPS RTK Observables",
    1005: "Stationary RTK Reference Station ARP",
    1006: "Stationary RTK Reference Station ARP with Antenna Height",
    1007: "Antenna Descriptor",
    1008: "Antenna Descriptor & Serial Number",
    1009: "L1-Only GLONASS RTK Observables",
    1010: "Extended L1-Only GLONASS RTK Observables",
    1011: "L1&L2 GLONASS RTK Observables",
    1012: "Extended L1&L2 GLONASS RTK Observables",
    1013: "System Parameters",
    1019: "GPS Ephemerides",
    1020: "GLONASS Ephemerides",
    1033: "Receiver and Antenna Descriptors",
    1074: "GPS MSM4",
    1075: "GPS MSM5",
    1077: "GPS MSM7",
    1084: "GLONASS MSM4",
    1085: "GLONASS MSM5",
    1087: "GLONASS MSM7",
    1094: "Galileo MSM4",
    1095: "Galileo MSM5",
    1097: "Galileo MSM7",
    1124: "BeiDou MSM4",
    1125: "BeiDou MSM5",
    1127: "BeiDou MSM7"
}

# ==================== Web界面配置 ====================

# WebSocket配置
WEBSOCKET_CONFIG = {
    # 'cors_allowed_origins': get_config_value('websocket', 'cors_allowed_origins', '*'),  # 已移除CORS功能
    'ping_timeout': get_config_value('websocket', 'ping_timeout', 60, int),
    'ping_interval': get_config_value('websocket', 'ping_interval', 25, int)
}
WEBSOCKET_ENABLED = get_config_value('websocket', 'enabled', True, bool)

# 实时数据推送间隔 (秒)
REALTIME_PUSH_INTERVAL = get_config_value('web', 'realtime_push_interval', 5, int)


PAGE_REFRESH_INTERVAL = get_config_value('web', 'page_refresh_interval', 30, int)

# ==================== 预留 ====================
# 支付二维码URL
PAYMENT_QR_CODES = {
    'alipay': get_config_value('payment', 'alipay_qr_code', ''),
    'wechat': get_config_value('payment', 'wechat_qr_code', '')
}

ALIPAY_QR_URL = PAYMENT_QR_CODES['alipay']
WECHAT_QR_URL = PAYMENT_QR_CODES['wechat']

# 线程池配置
THREAD_POOL_SIZE = get_config_value('performance', 'thread_pool_size', 100, int)
MAX_WORKERS = get_config_value('performance', 'max_workers', 100, int)
CONNECTION_QUEUE_SIZE = get_config_value('performance', 'connection_queue_size', 500, int)

MAX_MEMORY_USAGE = get_config_value('performance', 'max_memory_usage', 512, int)

CPU_WARNING_THRESHOLD = get_config_value('performance', 'cpu_warning_threshold', 80, int)

MEMORY_WARNING_THRESHOLD = get_config_value('performance', 'memory_warning_threshold', 80, int)

def load_from_env():
    """从环境变量加载配置"""
    global NTRIP_PORT, WEB_PORT, DEBUG, DATABASE_PATH
    
    # NTRIP端口
    if 'NTRIP_PORT' in os.environ:
        try:
            NTRIP_PORT = int(os.environ['NTRIP_PORT'])
        except ValueError:
            pass
    
    # Web端口
    if 'WEB_PORT' in os.environ:
        try:
            WEB_PORT = int(os.environ['WEB_PORT'])
        except ValueError:
            pass
    
    # 调试模式
    if 'DEBUG' in os.environ:
        DEBUG = os.environ['DEBUG'].lower() in ('true', '1', 'yes', 'on')
    
    # 数据库路径
    if 'DATABASE_PATH' in os.environ:
        DATABASE_PATH = os.environ['DATABASE_PATH']
    
    # 密钥
    if 'SECRET_KEY' in os.environ:
        global SECRET_KEY
        SECRET_KEY = os.environ['SECRET_KEY']

# ==================== 配置验证 ====================

def validate_config():
    """验证配置参数的有效性"""
    errors = []
    
    # 验证端口范围
    if not (1024 <= NTRIP_PORT <= 65535):
        errors.append(f"NTRIP端口 {NTRIP_PORT} 不在有效范围内 (1024-65535)")
    
    if not (1024 <= WEB_PORT <= 65535):
        errors.append(f"Web端口 {WEB_PORT} 不在有效范围内 (1024-65535)")
    
    # 验证缓冲区大小
    if BUFFER_SIZE <= 0 or BUFFER_SIZE > MAX_BUFFER_SIZE:
        errors.append(f"缓冲区大小 {BUFFER_SIZE} 无效")
    
    # 验证日志目录
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except Exception as e:
            errors.append(f"无法创建日志目录 {LOG_DIR}: {e}")
    
    return errors


def init_config():
    """初始化配置"""
    
    load_from_env()
    
    errors = validate_config()
    if errors:
        # print("配置验证失败:")
    # for error in errors:
    #     print(f"  - {error}")
        return False
    
    return True

def get_config_dict():
    """获取配置字典，用于调试"""
    return {
        'version': VERSION,
        'app_name': APP_NAME,
        'debug': DEBUG,
        'ntrip_host': NTRIP_HOST,
        'ntrip_port': NTRIP_PORT,
        'web_host': WEB_HOST,
        'web_port': WEB_PORT,
        'max_connections': MAX_CONNECTIONS,
        'buffer_size': BUFFER_SIZE,
        'database_path': DATABASE_PATH,
        'log_level': LOG_LEVEL,
        'tcp_keepalive': TCP_KEEPALIVE,
        'ring_buffer_size': RING_BUFFER_SIZE,
        'rtcm_parse_interval': RTCM_PARSE_INTERVAL
    }
    #雪碧+咖啡=不好喝~！