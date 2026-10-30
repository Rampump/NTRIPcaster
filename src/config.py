#!/usr/bin/env python3
"""
config.py - 配置文件
从config.ini文件读取NTRIP Caster的所有配置参数
"""

import os
import socket
import secrets
import configparser
from pathlib import Path
from typing import List, Tuple

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FILE = os.environ.get('NTRIP_CONFIG_FILE', os.path.join(_PROJECT_ROOT, 'config.ini'))
EXAMPLE_CONFIG_FILE = os.path.join(_PROJECT_ROOT, 'config.ini.example')

config = configparser.ConfigParser()

if os.path.exists(CONFIG_FILE):
    print(f"加载配置文件: {CONFIG_FILE}")
    config.read(CONFIG_FILE, encoding='utf-8')
elif os.path.exists(EXAMPLE_CONFIG_FILE):
    # config.ini 属于本地部署配置（含密钥/数据库路径），不再纳入版本库；
    # 缺失时回退到 config.ini.example，保证首次运行可用。
    print(f"未找到 {CONFIG_FILE}，回退使用示例配置 {EXAMPLE_CONFIG_FILE}（请复制为 config.ini 后按需修改）")
    CONFIG_FILE = EXAMPLE_CONFIG_FILE
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

# ==================== 版本号：全项目唯一事实源 ====================
# 版本号只在根目录的 VERSION 文件里维护（单行，如 "2.3.1"）。
# 代码、示例配置、compose、Dockerfile、部署脚本、文档里的版本号都是派生副本，
# 由 scripts/sync_version.py 自动刷新：改完 VERSION 后执行
#     python3 scripts/sync_version.py
# 全项目即自动对齐，不需要再去各处手改。
#
# 运行期取值优先级：VERSION 文件 > 环境变量 APP_VERSION > config.ini [app] version
#   - 以 VERSION 为准：config.ini 里的 version 只是老部署的兼容兜底，
#     不允许反向覆盖真实版本，否则升级后会出现「配置说旧版、代码是新版」的错位。
VERSION_FILE = os.path.join(_PROJECT_ROOT, 'VERSION')


def _read_version() -> str:
    """读取版本号：VERSION 文件 → 环境变量 → config.ini → 兜底。"""
    try:
        with open(VERSION_FILE, encoding='utf-8') as f:
            for line in f:
                v = line.strip()
                if v and not v.startswith('#'):
                    return v
    except OSError:
        pass

    env_version = os.environ.get('APP_VERSION', '').strip()
    if env_version:
        return env_version

    return get_config_value('app', 'version', '0.0.0')


# ==================== 基本配置 ====================

# 基本应用信息
APP_NAME = get_config_value('app', 'name', '2RTK Ntrip Caster')
APP_VERSION = _read_version()
APP_DESCRIPTION = get_config_value('app', 'description', 'Ntrip Caster')
APP_AUTHOR = get_config_value('app', 'author', '2rtk')
APP_CONTACT = get_config_value('app', 'contact', 'i@jia.im')
APP_WEBSITE = get_config_value('app', 'website', 'https://2rtk.com')


VERSION = APP_VERSION


DEBUG = get_config_value('development', 'debug_mode', False, bool)

# ==================== CASTER配置 ====================

# NTRIP Caster地理位置信息
CASTER_COUNTRY = get_config_value('caster', 'country', 'CHN')
CASTER_LATITUDE = get_config_value('caster', 'latitude', 25.20341154, float)
CASTER_LONGITUDE = get_config_value('caster', 'longitude', 110.277492, float)

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
MAX_CONNECTIONS = get_config_value('network', 'max_connections', 5000, int)

# 缓冲区大小
BUFFER_SIZE = get_config_value('network', 'buffer_size', 81920, int)      # 80KB
MAX_BUFFER_SIZE = get_config_value('network', 'max_buffer_size', 655360, int) # 640KB

# ==================== 数据库配置 ====================

# 环境变量优先于配置文件：容器部署时数据/日志必须落在持久卷内
DATABASE_PATH = os.environ.get('DATABASE_PATH') or get_config_value('database', 'path', '2rtk.db')
DB_POOL_SIZE = get_config_value('database', 'pool_size', 10, int)
DB_TIMEOUT = get_config_value('database', 'timeout', 30, int)

# ==================== 日志配置 ====================

LOG_DIR = os.environ.get('LOG_DIR') or get_config_value('logging', 'log_dir', 'logs')
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

# Flask密钥（生产环境请通过 config.ini / SECRET_KEY 环境变量提供；
# 若仍是仓库里的示例值，则自动生成随机密钥并存盘，避免所有人都用同一把会话密钥）
_INSECURE_SECRET_KEYS = {
    '',
    'your-secret-key-change-this',
    'your-secret-key-change-this-in-production',
    '8f4a9c2e7d1b6f3a5e8d7c9b2a4f6e3d5c8b7a9f2e4d6c8b3a5f7e9d1c2b4a6',
}


def _load_or_create_secret_key(configured_key):
    """载入持久化的随机会话密钥；不存在则生成"""
    if configured_key and configured_key not in _INSECURE_SECRET_KEYS:
        return configured_key

    key_dir = os.environ.get('DATA_DIR') or os.path.join(_PROJECT_ROOT, 'data')
    key_file = Path(key_dir) / '.secret_key'
    try:
        if key_file.exists():
            existing = key_file.read_text(encoding='utf-8').strip()
            if existing and existing not in _INSECURE_SECRET_KEYS:
                return existing
        new_key = secrets.token_hex(32)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(new_key, encoding='utf-8')
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        print(f"已自动生成会话密钥并保存到 {key_file}（建议在 config.ini 中显式配置 secret_key）")
        return new_key
    except Exception as exc:
        # 落盘失败时退化为进程内随机密钥：重启后会话失效，但不会使用弱密钥
        print(f"警告: 无法保存会话密钥({exc})，将使用进程内随机密钥，重启后需重新登录")
        return secrets.token_hex(32)


SECRET_KEY = _load_or_create_secret_key(
    os.environ.get('SECRET_KEY') or get_config_value('security', 'secret_key', '')
)
FLASK_SECRET_KEY = SECRET_KEY  # Flask应用密钥

# 密码哈希配置
PASSWORD_HASH_ROUNDS = get_config_value('security', 'password_hash_rounds', 3, int)
SESSION_TIMEOUT = get_config_value('security', 'session_timeout', 3600, int)  # 1小时

# 登录防爆破与会话安全
LOGIN_MAX_ATTEMPTS = get_config_value('security', 'login_max_attempts', 5, int)
LOGIN_LOCKOUT_SECONDS = get_config_value('security', 'login_lockout_seconds', 300, int)
# HTTPS 部署时应置 True，浏览器只在 HTTPS 下回传会话 Cookie
SESSION_COOKIE_SECURE = get_config_value('security', 'session_cookie_secure', False, bool)
# 仅在确实位于可信反向代理（Nginx）之后时启用，否则客户端可伪造 X-Forwarded-For
TRUST_PROXY_HEADERS = get_config_value('security', 'trust_proxy_headers', False, bool)

# 默认管理员账户
DEFAULT_ADMIN = {
    'username': get_config_value('admin', 'username', 'admin'),
    'password': get_config_value('admin', 'password', 'admin123')
}

# ==================== NTRIP协议配置 ====================


SUPPORTED_NTRIP_VERSIONS = get_config_value('ntrip', 'supported_versions', ['1.0', '2.0'], list)

DEFAULT_NTRIP_VERSION = get_config_value('ntrip', 'default_version', '1.0')
MAX_USER_CONNECTIONS_PER_MOUNT = get_config_value('ntrip', 'max_user_connections_per_mount', 3000, int)
MAX_USERS_PER_MOUNT = get_config_value('ntrip', 'max_users_per_mount', 3000, int)  # 每个挂载点每个用户的最大连接数
MAX_CONNECTIONS_PER_USER = get_config_value('ntrip', 'max_connections_per_user', 3, int)  # 每个用户的最大连接数
# 取流是否校验"用户-挂载点绑定"：
#   False（默认，兼容旧行为）= 任一有效用户可拉取任意挂载点
#   True                      = 仅当挂载点未绑定或绑定到本人时才允许取流
DOWNLOAD_ENFORCE_USER_BINDING = get_config_value('ntrip', 'download_enforce_user_binding', False, bool)
MOUNT_TIMEOUT = get_config_value('ntrip', 'mount_timeout', 1800, int)  # 30分钟
CLIENT_TIMEOUT = get_config_value('ntrip', 'client_timeout', 300, int)  # 5分钟
CONNECTION_TIMEOUT = get_config_value('ntrip', 'connection_timeout', 1800, int)  # 连接超时时间 (秒)

# ==================== TCP配置 ====================

# TCP Keep-Alive配置
TCP_KEEPALIVE = {
    'enabled': get_config_value('tcp', 'keepalive_enabled', True, bool),
    'idle': get_config_value('tcp', 'keepalive_idle', 60, int),      # 开始发送keep-alive探测前的空闲时间
    'interval': get_config_value('tcp', 'keepalive_interval', 10, int),  # keep-alive探测间隔
    'count': get_config_value('tcp', 'keepalive_count', 3, int)       # 最大keep-alive探测次数
}
SOCKET_TIMEOUT = get_config_value('tcp', 'socket_timeout', 120, int)

# ==================== 数据转发配置 ====================

# 环形缓冲区配置
RING_BUFFER_SIZE = get_config_value('data_forwarding', 'ring_buffer_size', 60, int)  # 保留最近多少秒的数据
# 硬上限：单挂载点最多缓存的数据块数，防止上游高频小包把内存打满
RING_BUFFER_MAX_CHUNKS = get_config_value('data_forwarding', 'ring_buffer_max_chunks', 3000, int)

BROADCAST_INTERVAL = get_config_value('data_forwarding', 'broadcast_interval', 0.01, float)  # 广播间隔 (秒)

DATA_SEND_TIMEOUT = get_config_value('data_forwarding', 'data_send_timeout', 5, int)  # 数据发送超时时间（秒）

# 每个客户端的待发队列上限（数据块数）。
# 客户端 TCP 窗口被打满时队列会积压，超过上限就直接丢弃积压、跳到最新数据，
# 避免"内存涨爆 + 延迟越拖越长"（实时差分数据过期即无用）。
CLIENT_SEND_QUEUE_SIZE = get_config_value('data_forwarding', 'client_send_queue_size', 200, int)

# 发送线程数。广播线程只负责切片入队，实际 sendall 由这组线程完成，
# 一个慢客户端最多占住其中一个线程，不再拖死广播轮次。
SENDER_THREADS = get_config_value('data_forwarding', 'sender_threads', 8, int)

# 单客户端已取出但未写完的字节上限（含内核没吃下的部分）。
# 超过即判定客户端长期追不上，丢弃积压、跳到最新数据，防止内存被慢客户端吃光。
CLIENT_PENDING_MAX_BYTES = get_config_value('data_forwarding', 'client_pending_max_bytes', 1048576, int)

CLIENT_HEALTH_CHECK_INTERVAL = get_config_value('data_forwarding', 'client_health_check_interval', 120, int)  # 客户端健康检查间隔（秒）

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
    'ping_timeout': get_config_value('websocket', 'ping_timeout', 120, int),
    'ping_interval': get_config_value('websocket', 'ping_interval', 15, int)
}
WEBSOCKET_ENABLED = get_config_value('websocket', 'enabled', True, bool)

# 实时数据推送间隔 (秒)
REALTIME_PUSH_INTERVAL = get_config_value('web', 'realtime_push_interval', 3, int)


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
THREAD_POOL_SIZE = get_config_value('performance', 'thread_pool_size', 5000, int)
MAX_WORKERS = get_config_value('performance', 'max_workers', 5000, int)
CONNECTION_QUEUE_SIZE = get_config_value('performance', 'connection_queue_size', 5000, int)

MAX_MEMORY_USAGE = get_config_value('performance', 'max_memory_usage', 2048, int)

CPU_WARNING_THRESHOLD = get_config_value('performance', 'cpu_warning_threshold', 80, int)

MEMORY_WARNING_THRESHOLD = get_config_value('performance', 'memory_warning_threshold', 80, int)

# 优雅关闭时等待线程退出的最长秒数，超时后强制继续（避免 docker stop 被 SIGKILL）
SHUTDOWN_TIMEOUT = get_config_value('performance', 'shutdown_timeout', 10, int)

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
        global SECRET_KEY, FLASK_SECRET_KEY
        SECRET_KEY = os.environ['SECRET_KEY']
        FLASK_SECRET_KEY = SECRET_KEY

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