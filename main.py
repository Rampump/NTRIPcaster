#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import signal
import logging
import psutil
import threading
import argparse
import os
from pathlib import Path
from threading import Thread

# 解析命令行参数
parser = argparse.ArgumentParser(description='2RTK NTRIP Caster')
parser.add_argument('--config', type=str, help='配置文件路径')
args = parser.parse_args()

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 如果指定了配置文件，设置环境变量
if args.config:
    os.environ['NTRIP_CONFIG_FILE'] = args.config

# 导入配置和核心模块
from src import config
from src import logger
from src import forwarder
from src.database import DatabaseManager
from src.web import create_web_manager
from src.ntrip import NTRIPCaster
from src.connection import get_connection_manager

def setup_logging():
    """设置日志系统"""
    # 初始化日志模块
    logger.init_logging()
    
    # 设置特定模块的日志级别
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('socketio').setLevel(logging.WARNING)
    logging.getLogger('engineio').setLevel(logging.WARNING)
    
    # 记录系统启动日志
    logger.log_system_event('日志系统初始化完成')

def print_banner():
    """打印启动横幅"""
    banner = f"""

    ██████╗ ██████╗ ████████╗██╗  ██╗
    ╚════██╗██╔══██╗╚══██╔══╝██║ ██╔╝
     █████╔╝██████╗╔   ██║   █████╔╝ 
    ██╔═══╝ ██╔══██╗   ██║   ██║  ██╗
    ███████╗██║  ██║   ██║   ██║  ██╗
    ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝
    2RTK Ntrip Caster {config.VERSION}

NTRIP端口: {config.NTRIP_PORT:<8} Web管理端口: {config.WEB_PORT:<8} 
调试模式: {str(config.DEBUG):<9} 最大连接: {config.MAX_CONNECTIONS:<8} 

    """
    print(banner)

def check_environment():
    """检查运行环境"""
    logger = logging.getLogger('main')
    
    # 检查Python版本
    if sys.version_info < (3, 7):
        logger.error("需要Python 3.7或更高版本")
        sys.exit(1)
    
    # 检查必要的目录（数据库目录、日志目录），不存在则创建
    required_dirs = [
        Path(config.DATABASE_PATH).resolve().parent,
        Path(config.LOG_DIR).resolve()
    ]
    
    for dir_path in required_dirs:
        if not dir_path.exists():
            logger.info(f"创建目录: {dir_path}")
            dir_path.mkdir(parents=True, exist_ok=True)
        if not os.access(str(dir_path), os.W_OK):
            logger.error(f"目录不可写: {dir_path}")
            sys.exit(1)
    
    # 检查端口是否可用
    import socket
    
    def check_port(port, name):
        """检查端口能否用于监听。

        必须先设置 SO_REUSEADDR 再 bind：服务停止后，(本地IP, 端口) 上往往还
        残留着上一轮连接的 TIME_WAIT 状态。Web 端口尤其明显——浏览器 socket.io
        轮询会持续建立/关闭短连接，一次运行下来能累积几百个 TIME_WAIT。
        没有 SO_REUSEADDR 的 bind 会直接 EADDRINUSE，于是重启时误报「端口已被占用」。

        真正的监听端也是这么做的（src/ntrip.py 的 NTRIP 监听、Werkzeug 的
        BaseWSGIServer 都设置了该选项），这里保持一致，否则检查通过与否
        和实际能不能 bind 就不是一回事了。

        Windows 不设：那边 SO_REUSEADDR 语义不同，会允许抢占已被其他进程
        绑定的端口，反而让检查失效。
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name != 'nt':
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('', port))
            return True
        except OSError as e:
            # bind 失败时再探一次：能连上才说明确实有进程在监听，提示更准确
            listening = False
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=1):
                    listening = True
            except OSError:
                pass
            if listening:
                logger.error(f"{name}端口 {port} 已被占用（有进程正在监听，请先停止该进程）")
            else:
                logger.error(f"{name}端口 {port} 不可用: {e}")
            return False
        finally:
            s.close()
    
    ports_ok = True
    ports_ok &= check_port(config.NTRIP_PORT, "NTRIP")
    ports_ok &= check_port(config.WEB_PORT, "Web")
    
    if not ports_ok:
        logger.error("端口检查失败，请检查端口占用情况")
        sys.exit(1)
    
    logger.info("环境检查通过")

class ServiceManager:
    """服务管理器 - 统一管理所有服务组件"""
    
    def __init__(self):
        self.db_manager = None
        self.web_manager = None
        self.ntrip_caster = None
        self.web_thread = None
        self.running = False
        self.stopping = False  # 添加停止标志位，防止重复调用
        self.start_time = None
        self.stats_thread = None
        self.stats_interval = 10  # 统计打印间隔（秒）
        self.last_network_stats = None
        self.print_stats = False  # 控制是否在控制台打印统计信息
        self.system_stats_cache = {}  # 缓存系统统计数据供Web API使用

        # psutil.cpu_percent(interval=None) 首次调用恒为 0，这里先打一次底，
        # 让第一次采集就能拿到有意义的值
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
        
    def start_all_services(self):
        """启动所有服务"""
        try:
            self.start_time = time.time()
            logger.log_system_event(f'启动2RTK NTRIP Caster v{config.VERSION}')
            
            # 1. 初始化数据库
            self.db_manager = DatabaseManager()
            self.db_manager.init_database()
            logger.log_system_event('数据库初始化完成')
            
            # 2. 初始化并启动数据转发器
            forwarder.initialize()
            forwarder.start_forwarder()
            logger.log_system_event('数据转发器初始化完成')
            
            # 3. RTCM解析现在集成在connection_manager中，无需单独启动
            logger.log_system_event('RTCM解析器集成完成')
            
            # 4. 启动Web管理界面
            self._start_web_interface()
            
            # 5. 启动NTRIP服务器（在单独线程中）
            self.ntrip_caster = NTRIPCaster(self.db_manager)
            self.ntrip_thread = threading.Thread(target=self.ntrip_caster.start, daemon=True)
            self.ntrip_thread.start()
            time.sleep(1)  # 等待NTRIP服务器启动
            
            # 6. 注册信号处理
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            
            self.running = True
            logger.log_system_event(f'所有服务已启动 - NTRIP端口: {config.NTRIP_PORT}, Web端口: {config.WEB_PORT}')
            
            # 启动统计监控线程
            self._start_stats_monitor()
            
            # 主循环 - 保持服务运行
            self._main_loop()
            
        except Exception as e:
            logger.log_error(f"启动服务失败: {e}", exc_info=True)
            self.stop_all_services()
            raise
    
    def _start_web_interface(self):
        """启动Web管理界面"""
        from src.web import set_server_instance
        self.web_manager = create_web_manager(
            self.db_manager, 
            forwarder.get_forwarder(), 
            self.start_time
        )
        # 设置服务器实例供Web API使用
        set_server_instance(self)
        self.web_manager.start_rtcm_parsing()
        
        def run_web():
            self.web_manager.run(host=config.HOST, port=config.WEB_PORT, debug=False)
        
        self.web_thread = Thread(target=run_web, daemon=True)
        self.web_thread.start()
        
        # 显示所有可访问的Web管理界面地址
        web_urls = config.get_display_urls(config.WEB_PORT, "Web管理界面")
        if len(web_urls) == 1:
            logger.log_info(f'Web管理界面已启动，管理地址: {web_urls[0]}')
        else:
            logger.log_system_event('Web管理界面已启动，可通过以下地址访问:')
            for url in web_urls:
                logger.log_system_event(f'  - {url}')
    
    def _start_stats_monitor(self):
        """启动统计监控线程"""
        self.stats_thread = Thread(target=self._stats_monitor_worker, daemon=True)
        self.stats_thread.start()
 
    def _stats_monitor_worker(self):
        """统计监控工作线程"""
        while self.running:
            try:
                # 先采集再睡：原来先 sleep(10)，启动后前 10 秒任何请求都会
                # 落进 get_system_stats 的"缓存为空 → 现场采集"分支
                self._update_system_stats()
                time.sleep(self.stats_interval)
            except Exception as e:
                logger.log_error(f"统计监控异常: {e}", exc_info=True)
                time.sleep(self.stats_interval)

    def _update_system_stats(self):
        """更新系统统计信息到缓存"""
        try:
            # interval=None：返回"距上次调用"的均值，立刻返回。
            # 原来 interval=1 会在请求线程里阻塞 1 秒，管理页首屏因此卡顿。
            cpu_percent = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            
            # 获取网络统计
            network_stats = psutil.net_io_counters()
            network_bandwidth = self._calculate_network_bandwidth(network_stats)
            
            # 获取NTRIP服务器统计
            ntrip_stats = self.ntrip_caster.get_performance_stats() if self.ntrip_caster else {}
            
            # 获取连接管理器统计
            conn_manager = get_connection_manager()
            conn_stats = conn_manager.get_statistics()
            
            # 计算运行时间
            uptime = time.time() - self.start_time if self.start_time else 0
            uptime_str = self._format_uptime(uptime)
            
            # 计算数据传输统计
            # mounts 既可能是 dict（get_system_stats 里按 dict 用）也可能是 list，统一处理
            mounts_stats = conn_stats.get('mounts') or {}
            if isinstance(mounts_stats, dict):
                total_data_bytes = sum(
                    v.get('total_bytes', 0) for v in mounts_stats.values() if isinstance(v, dict)
                )
            else:
                total_data_bytes = sum(
                    m.get('total_bytes', 0) for m in mounts_stats if isinstance(m, dict)
                )
            total_data_mb = total_data_bytes / (1024 * 1024)
            
            # 更新缓存
            self.system_stats_cache = {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'uptime': uptime,  # 保存数字格式的运行时间
                'uptime_str': uptime_str,  # 保存格式化的运行时间字符串
                'cpu_percent': cpu_percent,
                'memory': memory,
                'network_bandwidth': network_bandwidth,
                'ntrip_stats': ntrip_stats,
                'conn_stats': conn_stats,
                'total_data_mb': total_data_mb
            }
            
        except Exception as e:
            logger.log_error(f"更新统计信息失败: {e}", exc_info=True)
    

    
    def get_system_stats(self):
        """获取系统统计数据供Web API使用

        缓存里只放"贵"的东西（psutil 的 CPU/内存/网卡速率，10 秒一次）；
        运行时长、在线挂载点/用户在这些**每次调用时实时取**——
        原来整份数据都来自 10 秒缓存，于是前端看到的连接数最多滞后 10 秒，
        运行时长也是每 10 秒跳一格。
        """
        try:
            stats = self.system_stats_cache.copy()
            if not stats:
                # 如果缓存为空，立即更新一次（psutil 已改非阻塞，不会卡住请求线程）
                self._update_system_stats()
                stats = self.system_stats_cache.copy()

            if not stats:
                return {}

            memory_info = stats.get('memory')
            network_info = stats.get('network_bandwidth', {})
            # NTRIP 计数只是一次加锁读整数，不放进 10 秒缓存
            try:
                ntrip_stats = self.ntrip_caster.get_performance_stats() if self.ntrip_caster else {}
            except Exception:
                ntrip_stats = stats.get('ntrip_stats', {})

            # 实时部分
            uptime = time.time() - self.start_time if self.start_time else 0
            try:
                conn_stats = get_connection_manager().get_statistics()
            except Exception as e:
                logger.log_error(f"获取连接统计失败: {e}")
                conn_stats = {}

            counts = {
                'mounts': conn_stats.get('total_mounts', 0),
                'users': conn_stats.get('total_users', 0),
                'connections': conn_stats.get('total_connections', 0),
            }

            return {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'uptime': uptime,  # 数字格式的运行时间（秒）
                'uptime_str': self._format_uptime(uptime),
                'cpu_percent': round(stats.get('cpu_percent', 0), 1),
                'memory': {
                    'percent': round(getattr(memory_info, 'percent', 0), 1),
                    'used': getattr(memory_info, 'used', 0),
                    'total': getattr(memory_info, 'total', 0)
                },
                'network_bandwidth': {
                    'sent_rate': network_info.get('sent_rate', 0) if isinstance(network_info, dict) else 0,
                    'recv_rate': network_info.get('recv_rate', 0) if isinstance(network_info, dict) else 0,
                    'display': network_info.get('display', '') if isinstance(network_info, dict) else ''
                },
                'connections': {
                    'active': ntrip_stats.get('active_connections', 0),
                    'total': ntrip_stats.get('total_connections', 0),
                    'rejected': ntrip_stats.get('rejected_connections', 0),
                    'max_concurrent': ntrip_stats.get('max_connections', 0)
                },
                # 明确的计数口径，前端直接用，不再自己数密钥猜含义
                'counts': counts,
                'mounts': conn_stats.get('mounts', []),
                'users': conn_stats.get('users', []),
                'data_transfer': {
                    'total_bytes': sum(
                        m.get('total_bytes', 0) for m in conn_stats.get('mounts', [])
                        if isinstance(m, dict)
                    )
                },
                # 转发器实时计数：dropped_bytes 持续增长说明有客户端跟不上
                'forwarder': self._get_forwarder_stats()
            }
        except Exception as e:
            logger.log_error(f"获取系统统计数据失败: {e}", exc_info=True)
            return {}

    @staticmethod
    def _get_forwarder_stats():
        """转发器计数（失败时返回空字典，不影响主接口）"""
        try:
            fw_stats = forwarder.get_stats() or {}
            fwd = fw_stats.get('forwarder', {})
            queue_info = fw_stats.get('send_queue', {})
            return {
                'active_clients': fwd.get('active_clients', 0),
                'total_bytes_sent': fwd.get('total_bytes_sent', 0),
                'dropped_bytes': fwd.get('dropped_bytes', 0),
                'failed_sends': fwd.get('failed_sends', 0),
                'disconnected_clients': fwd.get('disconnected_clients', 0),
                'send_queue_pending': queue_info.get('pending_clients', 0),
                'sender_threads': queue_info.get('sender_threads', 0),
            }
        except Exception:
            return {}
    
    def set_print_stats(self, enabled):
        """设置是否在控制台打印统计信息"""
        self.print_stats = enabled
        if enabled:
            logger.log_system_event('已启用控制台统计信息打印')
        else:
            logger.log_system_event('已禁用控制台统计信息打印')
    
    def _calculate_network_bandwidth(self, current_stats):
        """计算网络带宽

        返回结构化数据（bytes/s），前端按 formatBytes(sent_rate) 展示；
        原来返回的是 "↑x Mbps ↓y Mbps" 字符串，而后端又按 dict 取值，
        结果页面上收发速率永远是 0。
        """
        now = time.time()
        if self.last_network_stats is None:
            self.last_network_stats = (current_stats, now)
            return {'sent_rate': 0, 'recv_rate': 0, 'sent_mbps': 0.0, 'recv_mbps': 0.0, 'display': '计算中...'}
        
        last_stats, last_time = self.last_network_stats
        time_diff = now - last_time
        if time_diff <= 0:
            return {'sent_rate': 0, 'recv_rate': 0, 'sent_mbps': 0.0, 'recv_mbps': 0.0, 'display': '计算中...'}
        
        bytes_sent_diff = max(0, current_stats.bytes_sent - last_stats.bytes_sent)
        bytes_recv_diff = max(0, current_stats.bytes_recv - last_stats.bytes_recv)
        
        sent_rate = bytes_sent_diff / time_diff          # bytes/s
        recv_rate = bytes_recv_diff / time_diff
        sent_mbps = (bytes_sent_diff * 8) / (time_diff * 1024 * 1024)
        recv_mbps = (bytes_recv_diff * 8) / (time_diff * 1024 * 1024)
        total_mbps = sent_mbps + recv_mbps
        
        self.last_network_stats = (current_stats, now)
        
        return {
            'sent_rate': sent_rate,
            'recv_rate': recv_rate,
            'sent_mbps': round(sent_mbps, 3),
            'recv_mbps': round(recv_mbps, 3),
            'display': f"↑{sent_mbps:.2f} Mbps ↓{recv_mbps:.2f} Mbps (总计: {total_mbps:.2f} Mbps)"
        }
    
    def _format_uptime(self, seconds):
        """格式化运行时间"""
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if days > 0:
            return f"{days}天 {hours}小时 {minutes}分钟"
        elif hours > 0:
            return f"{hours}小时 {minutes}分钟"
        else:
            return f"{minutes}分钟 {secs}秒"

    def _main_loop(self):
        """主循环 - 监控服务状态"""
        while self.running:
            try:
                # 检查各服务状态
                if self.ntrip_caster and not self.ntrip_caster.running:
                    logger.log_error('NTRIP服务器意外停止')
                    break
                    
                if self.web_thread and not self.web_thread.is_alive():
                    logger.log_error('Web服务意外停止')
                    break
                
                # 短暂休眠避免CPU占用过高
                time.sleep(1)
                
            except Exception as e:
                logger.log_error(f"主循环异常: {e}", exc_info=True)
                break
    
    def _signal_handler(self, signum, frame):
        """信号处理器

        第一次信号走优雅关闭；再收到一次说明关闭卡住了，直接强制退出，
        避免运维按两次 Ctrl+C 也退不出来。
        """
        if self.stopping:
            logger.log_warning(f'收到信号 {signum}，关闭已在进行中，强制退出')
            os._exit(1)
        logger.log_system_event(f'收到信号 {signum}，开始关闭所有服务')
        self.stop_all_services()
    
    def stop_all_services(self):
        """停止所有服务"""
        if self.stopping:
            logger.log_system_event('服务正在关闭中，避免重复调用')
            return
            
        self.stopping = True
        logger.log_system_event('正在关闭所有服务')
        
        try:
            self.running = False
            
            # 等待统计监控线程结束
            if self.stats_thread and self.stats_thread.is_alive():
                logger.log_system_event('正在停止统计监控线程')
                self.stats_thread.join(timeout=2)
            
            # 关闭顺序很重要：先停转发器（关掉所有客户端/源站 socket），
            # 再停 NTRIP（此时 worker 的 recv/send 才会立即返回），最后停 Web。
            try:
                forwarder.stop_forwarder()
            except Exception as e:
                logger.log_error(f'停止数据转发器时出错: {e}')
            
            if self.ntrip_caster:
                try:
                    self.ntrip_caster.stop()
                except Exception as e:
                    logger.log_error(f'停止NTRIP服务器时出错: {e}')
            
            # 停止Web管理器
            if self.web_manager:
                try:
                    self.web_manager.stop_rtcm_parsing()
                except Exception as e:
                    logger.log_error(f'停止Web管理器时出错: {e}')
            
            logger.log_system_event('所有服务已关闭')
            
        except Exception as e:
            logger.log_error(f'关闭服务时发生异常: {e}')
        finally:
            # 关闭完成后保持 stopping=True，后续信号直接走强制退出分支
            pass

# 全局服务器实例
server = None

def get_server_instance():
    """获取服务器实例"""
    return server

def main():
    """主函数"""
    global server
    try:
        # 设置日志
        setup_logging()
        main_logger = logger.get_logger('main')
        
        # 打印启动信息
        print_banner()
        
        # 初始化配置（必须先于环境检查，否则环境变量 NTRIP_PORT/WEB_PORT 的覆盖不会生效）
        config.init_config()
        logger.log_system_event('配置初始化完成')

        # 检查环境
        check_environment()
        
        # 创建服务器实例并启动所有服务
        server = ServiceManager()
        globals()['server'] = server  # 设置全局变量
        server.start_all_services()
        
    except KeyboardInterrupt:
        logger.log_system_event('收到中断信号，正在关闭服务')
    except Exception as e:
        logger.log_error(f"启动失败: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if server:
            server.stop_all_services()
        logger.log_system_event('程序已退出')
        logger.shutdown_logging()

if __name__ == '__main__':
    main()