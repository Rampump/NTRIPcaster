#!/usr/bin/env python3
"""
NTRIP并发连接测试脚本
功能：使用500个用户并发连接NTRIP服务器，测试系统稳定性
"""

import socket
import threading
import time
import json
import random
import base64
import hashlib
import sys
import psutil
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# NTRIP服务器配置
NTRIP_SERVER = "192.168.1.4"
NTRIP_PORT = 2101
MOUNT_POINTS = ["RTKGL", "RTKHL"]
TEST_DURATION = 99999  # 测试时长（秒），保持长连接
MAX_CONCURRENT_CONNECTIONS = 1500  # 最大并发连接数
TARGET_CONNECTIONS = [1000, 1200, 1500]  # 目标连接数列表
CONNECTION_STEP = 100  # 每次增加的连接数

# 统计信息
stats = {
    "total_connections": 0,
    "successful_connections": 0,
    "failed_connections": 0,
    "data_received": 0,
    "total_bytes": 0,
    "ntrip_bytes_sent": 0,      # NTRIP应用层发送字节数
    "ntrip_bytes_received": 0,  # NTRIP应用层接收字节数
    "connection_errors": [],
    "start_time": None,
    "end_time": None,
    "performance_data": [],
    "server_stats": [],
    "network_stats": []
}
stats_lock = threading.Lock()

def load_test_users():
    """加载测试用户列表"""
    try:
        with open("test_users.json", "r", encoding="utf-8") as f:
            users = json.load(f)
        print(f"成功加载 {len(users)} 个测试用户")
        return users
    except FileNotFoundError:
        print("错误: 找不到 test_users.json 文件，请先运行 test_add_users.py")
        sys.exit(1)
    except Exception as e:
        print(f"加载用户文件失败: {e}")
        sys.exit(1)

def get_system_performance():
    """获取系统性能数据"""
    try:
        # CPU使用率
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # 内存使用情况
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used_mb = memory.used / 1024 / 1024
        memory_total_mb = memory.total / 1024 / 1024
        
        # 网络IO统计
        net_io = psutil.net_io_counters()
        
        return {
            "timestamp": time.time(),
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "memory_used_mb": memory_used_mb,
            "memory_total_mb": memory_total_mb,
            "network_bytes_sent": net_io.bytes_sent,
            "network_bytes_recv": net_io.bytes_recv,
            "network_packets_sent": net_io.packets_sent,
            "network_packets_recv": net_io.packets_recv
        }
    except Exception as e:
        print(f"获取系统性能数据失败: {e}")
        return None

def get_server_stats():
    """获取NTRIP服务器统计信息"""
    try:
        # 尝试从服务器API获取统计信息
        response = requests.get(f"http://{NTRIP_SERVER}:5757/api/stats", timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"获取服务器统计失败: {e}")
    return None

def calculate_bandwidth(start_stats, end_stats, duration):
    """计算网络带宽使用量"""
    if not start_stats or not end_stats or duration <= 0:
        return None
    
    bytes_sent_diff = end_stats["network_bytes_sent"] - start_stats["network_bytes_sent"]
    bytes_recv_diff = end_stats["network_bytes_recv"] - start_stats["network_bytes_recv"]
    
    upload_mbps = (bytes_sent_diff * 8) / (duration * 1024 * 1024)  # Mbps
    download_mbps = (bytes_recv_diff * 8) / (duration * 1024 * 1024)  # Mbps
    
    return {
        "upload_mbps": upload_mbps,
        "download_mbps": download_mbps,
        "total_mbps": upload_mbps + download_mbps,
        "bytes_sent": bytes_sent_diff,
        "bytes_recv": bytes_recv_diff
    }

def create_ntrip_request(mount_point, username, password, protocol="basic"):
    """创建NTRIP请求"""
    if protocol == "basic":
        # Basic认证
        auth_string = f"{username}:{password}"
        auth_bytes = auth_string.encode('ascii')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
        
        request = (
            f"GET /{mount_point} HTTP/1.1\r\n"
            f"Host: {NTRIP_SERVER}:{NTRIP_PORT}\r\n"
            f"User-Agent: NTRIP-Test-Client/1.0\r\n"
            f"Authorization: Basic {auth_b64}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
    elif protocol == "digest":
        # Digest认证（简化版，实际应用中需要先获取challenge）
        request = (
            f"GET /{mount_point} HTTP/1.1\r\n"
            f"Host: {NTRIP_SERVER}:{NTRIP_PORT}\r\n"
            f"User-Agent: NTRIP-Test-Client/1.0\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
    else:
        # NTRIP 1.0格式
        request = (
            f"GET /{mount_point} HTTP/1.0\r\n"
            f"User-Agent: NTRIP-Test-Client/1.0\r\n"
            f"Authorization: Basic {base64.b64encode(f'{username}:{password}'.encode()).decode()}\r\n"
            f"\r\n"
        )
    
    return request

def ntrip_client_test(user_info, test_duration):
    """单个NTRIP客户端测试"""
    username = user_info["username"]
    password = user_info["password"]
    mount_point = random.choice(MOUNT_POINTS)
    protocol = random.choice(["basic", "ntrip1.0"])  # 随机选择协议
    
    client_stats = {
        "username": username,
        "mount_point": mount_point,
        "protocol": protocol,
        "connected": False,
        "bytes_received": 0,
        "connection_time": 0,
        "error_message": None
    }
    
    sock = None
    start_time = time.time()
    
    try:
        # 创建socket连接
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)  # 30秒超时，给服务器更多处理时间
        
        # 连接到NTRIP服务器
        sock.connect((NTRIP_SERVER, NTRIP_PORT))
        
        # 发送NTRIP请求
        request = create_ntrip_request(mount_point, username, password, protocol)
        request_bytes = request.encode('utf-8')
        sock.send(request_bytes)
        
        # 统计NTRIP发送字节数
        with stats_lock:
            stats["ntrip_bytes_sent"] += len(request_bytes)
        
        # 接收响应
        response = sock.recv(1024).decode('utf-8', errors='ignore')
        
        if "200 OK" in response:
            client_stats["connected"] = True
            client_stats["connection_time"] = time.time() - start_time
            
            # 持续接收数据
            end_time = start_time + test_duration
            sock.settimeout(1)  # 设置较短的接收超时
            
            while time.time() < end_time:
                try:
                    data = sock.recv(4096)
                    if data:
                        client_stats["bytes_received"] += len(data)
                        # 统计NTRIP接收字节数
                        with stats_lock:
                            stats["ntrip_bytes_received"] += len(data)
                    else:
                        break
                except socket.timeout:
                    continue
                except Exception:
                    break
        else:
            client_stats["error_message"] = f"认证失败: {response[:100]}"
    
    except socket.timeout:
        client_stats["error_message"] = "连接超时"
    except ConnectionRefusedError:
        client_stats["error_message"] = "连接被拒绝"
    except Exception as e:
        client_stats["error_message"] = str(e)
    
    finally:
        if sock:
            try:
                sock.close()
            except:
                pass
    
    # 更新全局统计
    with stats_lock:
        stats["total_connections"] += 1
        if client_stats["connected"]:
            stats["successful_connections"] += 1
            stats["total_bytes"] += client_stats["bytes_received"]
            if client_stats["bytes_received"] > 0:
                stats["data_received"] += 1
        else:
            stats["failed_connections"] += 1
            if client_stats["error_message"]:
                stats["connection_errors"].append({
                    "username": username,
                    "mount_point": mount_point,
                    "error": client_stats["error_message"]
                })
    
    return client_stats

def print_progress():
    """打印测试进度和性能监控"""
    last_perf_data = None
    
    while stats["start_time"] and not stats["end_time"]:
        time.sleep(5)  # 每5秒打印一次
        
        # 获取性能数据
        current_perf = get_system_performance()
        server_stats = get_server_stats()
        
        with stats_lock:
            elapsed = time.time() - stats["start_time"]
            
            # 保存性能数据
            if current_perf:
                stats["performance_data"].append(current_perf)
            if server_stats:
                stats["server_stats"].append(server_stats)
            
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 测试进度和性能监控:")
            print(f"  运行时间: {elapsed:.1f}s")
            print(f"  总连接数: {stats['total_connections']}")
            print(f"  成功连接: {stats['successful_connections']}")
            print(f"  失败连接: {stats['failed_connections']}")
            print(f"  接收数据连接: {stats['data_received']}")
            print(f"  总接收字节: {stats['total_bytes']:,} ({stats['total_bytes']/1024/1024:.2f} MB)")
            print(f"  NTRIP发送: {stats['ntrip_bytes_sent']:,} 字节 ({stats['ntrip_bytes_sent']/1024:.2f} KB)")
            print(f"  NTRIP接收: {stats['ntrip_bytes_received']:,} 字节 ({stats['ntrip_bytes_received']/1024/1024:.2f} MB)")
            
            if stats['successful_connections'] > 0:
                success_rate = (stats['successful_connections'] / stats['total_connections']) * 100
                print(f"  连接成功率: {success_rate:.1f}%")
            
            # 显示系统性能
            if current_perf:
                print(f"\n  系统性能:")
                print(f"    CPU使用率: {current_perf['cpu_percent']:.1f}%")
                print(f"    内存使用率: {current_perf['memory_percent']:.1f}% ({current_perf['memory_used_mb']:.0f}/{current_perf['memory_total_mb']:.0f} MB)")
                
                # 计算网络带宽
                if last_perf_data:
                    bandwidth = calculate_bandwidth(last_perf_data, current_perf, 5)
                    if bandwidth:
                        print(f"    网络带宽: ↑{bandwidth['upload_mbps']:.2f} Mbps ↓{bandwidth['download_mbps']:.2f} Mbps (总计: {bandwidth['total_mbps']:.2f} Mbps)")
                        print(f"    数据传输: ↑{bandwidth['bytes_sent']/1024/1024:.2f} MB ↓{bandwidth['bytes_recv']/1024/1024:.2f} MB")
                
                last_perf_data = current_perf
            
            # 显示服务器统计
            if server_stats:
                print(f"\n  服务器状态:")
                if 'active_connections' in server_stats:
                    print(f"    活跃连接: {server_stats['active_connections']}")
                if 'total_connections' in server_stats:
                    print(f"    总连接数: {server_stats['total_connections']}")
                if 'rejected_connections' in server_stats:
                    print(f"    拒绝连接: {server_stats['rejected_connections']}")

def run_connection_test(users, target_connections, test_name):
    """运行指定数量的连接测试"""
    print(f"\n{'='*60}")
    print(f"开始 {test_name} - 目标连接数: {target_connections}")
    print(f"{'='*60}")
    
    # 重置统计数据
    with stats_lock:
        stats["total_connections"] = 0
        stats["successful_connections"] = 0
        stats["failed_connections"] = 0
        stats["data_received"] = 0
        stats["total_bytes"] = 0
        stats["ntrip_bytes_sent"] = 0
        stats["ntrip_bytes_received"] = 0
        stats["connection_errors"] = []
        stats["performance_data"] = []
        stats["server_stats"] = []
        stats["start_time"] = time.time()
        stats["end_time"] = None
    
    # 获取初始性能数据
    initial_perf = get_system_performance()
    
    # 启动进度显示线程
    progress_thread = threading.Thread(target=print_progress, daemon=True)
    progress_thread.start()
    
    # 选择用户子集，确保不超过单用户连接限制
    MAX_CONNECTIONS_PER_USER = 50  # 与配置文件保持一致
    test_users = []
    user_connection_count = {}
    
    # 循环分配用户，确保不超过单用户连接限制
    user_index = 0
    for i in range(target_connections):
        while True:
            user = users[user_index % len(users)]
            username = user['username']
            
            # 检查该用户的连接数
            if user_connection_count.get(username, 0) < MAX_CONNECTIONS_PER_USER:
                test_users.append(user)
                user_connection_count[username] = user_connection_count.get(username, 0) + 1
                break
            
            user_index += 1
            # 如果所有用户都达到连接限制，停止分配
            if user_index >= len(users) * MAX_CONNECTIONS_PER_USER:
                print(f"警告：无法分配 {target_connections} 个连接，最多只能分配 {len(test_users)} 个连接")
                break
        
        if len(test_users) >= target_connections or user_index >= len(users) * MAX_CONNECTIONS_PER_USER:
            break
        
        user_index += 1
    
    print(f"使用 {len(test_users)} 个用户进行测试...")
    
    # 使用线程池进行并发测试
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CONNECTIONS) as executor:
        futures = []
        for i, user in enumerate(test_users):
            # 长时间连接测试
            future = executor.submit(ntrip_client_test, user, TEST_DURATION)
            futures.append(future)
            
            # 控制连接建立速度
            time.sleep(0.02)  # 每20ms建立一个连接
            
            if (i + 1) % 100 == 0:
                print(f"已启动 {i + 1}/{len(test_users)} 个连接...")
        
        print(f"\n所有 {len(test_users)} 个连接已启动，等待稳定运行...")
        
        # 等待连接稳定（60秒）
        time.sleep(60)
        
        print(f"\n连接已稳定，开始性能监控阶段...")
        print("按 Ctrl+C 停止当前测试并进入下一阶段")
        
        try:
            # 持续监控直到用户中断
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            print(f"\n用户中断，正在停止 {test_name}...")
    
    # 记录结束时间
    with stats_lock:
        stats["end_time"] = time.time()
    
    # 获取最终性能数据
    final_perf = get_system_performance()
    
    # 生成测试报告
    generate_test_report(test_name, target_connections, initial_perf, final_perf)

def generate_test_report(test_name, target_connections, initial_perf, final_perf):
    """生成详细的测试报告"""
    print(f"\n{'='*60}")
    print(f"{test_name} 测试报告")
    print(f"{'='*60}")
    
    with stats_lock:
        total_time = stats["end_time"] - stats["start_time"] if stats["end_time"] else 0
        
        print(f"\n基本统计:")
        print(f"  目标连接数: {target_connections}")
        print(f"  实际连接尝试: {stats['total_connections']}")
        print(f"  成功连接: {stats['successful_connections']}")
        print(f"  失败连接: {stats['failed_connections']}")
        print(f"  接收数据连接: {stats['data_received']}")
        print(f"  总测试时间: {total_time:.2f} 秒")
        
        if stats['total_connections'] > 0:
            success_rate = (stats['successful_connections'] / stats['total_connections']) * 100
            print(f"  连接成功率: {success_rate:.2f}%")
        
        print(f"\n数据传输统计:")
        print(f"  总接收数据: {stats['total_bytes']:,} 字节 ({stats['total_bytes']/1024/1024:.2f} MB)")
        print(f"  NTRIP应用层统计:")
        print(f"    发送数据: {stats['ntrip_bytes_sent']:,} 字节 ({stats['ntrip_bytes_sent']/1024/1024:.2f} MB)")
        print(f"    接收数据: {stats['ntrip_bytes_received']:,} 字节 ({stats['ntrip_bytes_received']/1024/1024:.2f} MB)")
        if stats['successful_connections'] > 0 and total_time > 0:
            avg_throughput = (stats['total_bytes'] / total_time) / 1024 / 1024  # MB/s
            ntrip_throughput = (stats['ntrip_bytes_received'] / total_time) / 1024 / 1024  # MB/s
            print(f"  平均吞吐量: {avg_throughput:.2f} MB/s")
            print(f"  NTRIP平均吞吐量: {ntrip_throughput:.2f} MB/s")
            avg_per_conn = stats['total_bytes'] / stats['successful_connections'] / 1024  # KB per connection
            ntrip_avg_per_conn = stats['ntrip_bytes_received'] / stats['successful_connections'] / 1024  # KB per connection
            print(f"  平均每连接数据: {avg_per_conn:.2f} KB")
            print(f"  NTRIP平均每连接数据: {ntrip_avg_per_conn:.2f} KB")
        
        # 性能统计
        if initial_perf and final_perf and len(stats['performance_data']) > 0:
            print(f"\n系统性能统计:")
            
            # CPU统计
            cpu_values = [p['cpu_percent'] for p in stats['performance_data']]
            print(f"  CPU使用率: 平均 {sum(cpu_values)/len(cpu_values):.1f}%, 最高 {max(cpu_values):.1f}%")
            
            # 内存统计
            memory_values = [p['memory_percent'] for p in stats['performance_data']]
            print(f"  内存使用率: 平均 {sum(memory_values)/len(memory_values):.1f}%, 最高 {max(memory_values):.1f}%")
            
            # 网络带宽统计
            if total_time > 0:
                bandwidth = calculate_bandwidth(initial_perf, final_perf, total_time)
                if bandwidth:
                    print(f"  网络带宽使用:")
                    print(f"    上传: {bandwidth['upload_mbps']:.2f} Mbps ({bandwidth['bytes_sent']/1024/1024:.2f} MB)")
                    print(f"    下载: {bandwidth['download_mbps']:.2f} Mbps ({bandwidth['bytes_recv']/1024/1024:.2f} MB)")
                    print(f"    总计: {bandwidth['total_mbps']:.2f} Mbps")
        
        # 错误统计
        if stats['connection_errors']:
            print(f"\n错误统计 (前10个):")
            error_counts = {}
            for error in stats['connection_errors'][:20]:
                error_type = error['error'][:100]
                error_counts[error_type] = error_counts.get(error_type, 0) + 1
            
            for error_type, count in list(error_counts.items())[:10]:
                print(f"  {error_type}: {count} 次")
            
            print(f"\n错误详情示例 (前5个):")
            for i, error in enumerate(stats['connection_errors'][:5]):
                print(f"  {i+1}. 用户: {error['username']}, 挂载点: {error['mount_point']}, 错误: {error['error']}")
        
        # 保存详细报告到文件
        report_filename = f"test_report_{test_name}_{target_connections}conn_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        report_data = {
            "test_name": test_name,
            "target_connections": target_connections,
            "stats": dict(stats),
            "initial_performance": initial_perf,
            "final_performance": final_perf,
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            with open(report_filename, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2)
            print(f"\n详细报告已保存到: {report_filename}")
        except Exception as e:
            print(f"\n保存报告失败: {e}")
    
    print(f"\n{test_name} 完成！\n")



def main():
    """主函数 - 运行分阶段并发测试"""
    print("=" * 80)
    print("NTRIP 高并发连接测试 - 分阶段测试")
    print("=" * 80)
    
    # 加载测试用户
    users = load_test_users()
    print(f"加载了 {len(users)} 个测试用户")
    
    # 测试阶段配置
    test_stages = [500, 1000, 1200, 1500, 2000]
    
    for stage_connections in test_stages:
        print(f"\n{'='*60}")
        print(f"开始 {stage_connections} 连接并发测试")
        print(f"{'='*60}")
        
        try:
            # 运行测试
            test_name = f"{stage_connections}连接并发测试"
            run_connection_test(users, stage_connections, test_name)
            
            # 测试间隔
            if stage_connections != test_stages[-1]:
                print(f"\n等待 30 秒后进行下一阶段测试...")
                time.sleep(30)
                
        except KeyboardInterrupt:
            print("\n用户中断测试")
            break
        except Exception as e:
            print(f"\n测试阶段 {stage_connections} 连接时发生错误: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    print("\n" + "=" * 80)
    print("所有测试阶段完成")
    print("=" * 80)

if __name__ == "__main__":
    main()