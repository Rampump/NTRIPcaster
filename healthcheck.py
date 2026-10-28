#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NTRIP Caster 健康检查脚本
用于Docker容器健康检查和监控
"""

import sys
import os
import time
import socket
import urllib.request
import urllib.error
import json
import logging
from typing import Dict, List, Tuple, Optional
from pathlib import Path

# 端口与数据目录均允许环境变量覆盖，避免改了 config.ini / compose 端口后探活误报
WEB_PORT = int(os.environ.get('WEB_PORT', '5757'))
NTRIP_PORT = int(os.environ.get('NTRIP_PORT', '2101'))
DATA_DIR = os.environ.get('DATA_DIR', '/app')

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class HealthChecker:
    """健康检查器"""
    
    def __init__(self):
        self.checks = [
            self.check_web_service,
            self.check_ntrip_service,
            self.check_memory_usage,
            self.check_disk_space,
        ]
    
    def check_web_service(self) -> Tuple[bool, str]:
        """检查Web服务"""
        try:
            url = f'http://127.0.0.1:{WEB_PORT}/health'
            # 必须绕过环境里的 HTTP(S)_PROXY：容器继承代理变量时，
            # 连 127.0.0.1 也会被转发出去，探活直接 502。
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=5) as response:
                if response.status == 200:
                    return True, "Web服务正常"
                else:
                    return False, f"Web服务返回状态码: {response.status}"
        except urllib.error.URLError as e:
            return False, f"Web服务连接失败: {e}"
        except Exception as e:
            return False, f"Web服务检查异常: {e}"
    
    def check_ntrip_service(self) -> Tuple[bool, str]:
        """检查NTRIP服务端口"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex(('127.0.0.1', NTRIP_PORT))
            sock.close()
            
            if result == 0:
                return True, "NTRIP服务端口正常"
            else:
                return False, "NTRIP服务端口无法连接"
        except Exception as e:
            return False, f"NTRIP服务检查异常: {e}"
    
    def _read_cgroup_memory(self) -> Tuple[int, int]:
        """读取容器自身的内存上限/已用（cgroup v2 优先，回落 v1）

        只看 /proc/meminfo 是宿主机口径：宿主机内存紧张时容器明明很闲，
        也会被判成 unhealthy，进而拖住 depends_on: service_healthy 的服务。
        """
        # cgroup v2
        for limit_file, usage_file in (
            ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory.current'),
        ):
            try:
                limit_raw = open(limit_file).read().strip()
                if limit_raw == 'max':
                    continue
                usage = int(open(usage_file).read().strip())
                limit = int(limit_raw)
                if limit > 0:
                    return limit, usage
            except (OSError, ValueError):
                continue
        # cgroup v1
        try:
            limit = int(open('/sys/fs/cgroup/memory/memory.limit_in_bytes').read().strip())
            usage = int(open('/sys/fs/cgroup/memory/memory.usage_in_bytes').read().strip())
            # 未设上限时 v1 会给出一个天文数字
            if 0 < limit < (1 << 60):
                return limit, usage
        except (OSError, ValueError):
            pass
        return 0, 0

    def check_memory_usage(self) -> Tuple[bool, str]:
        """检查内存使用情况

        优先看容器 cgroup 限额（这才是"本容器是否健康"）；
        cgroup 不可用时退回 /proc/meminfo（宿主机口径），再退回 psutil。
        """
        try:
            limit, usage = self._read_cgroup_memory()
            if limit > 0:
                percent = usage / limit * 100
                ok = percent < 90
                scope = '容器'
            else:
                total = available = 0
                try:
                    with open('/proc/meminfo', 'r') as f:
                        meminfo = f.read()
                    for line in meminfo.split('\n'):
                        if line.startswith('MemTotal:'):
                            total = int(line.split()[1]) * 1024
                        elif line.startswith('MemAvailable:'):
                            available = int(line.split()[1]) * 1024
                except OSError:
                    total = available = 0

                if total <= 0:
                    try:
                        import psutil
                        vm = psutil.virtual_memory()
                        total, available = vm.total, vm.available
                    except Exception:
                        return True, "无法获取内存信息，跳过内存检查"

                if total <= 0:
                    return True, "无法获取内存信息，跳过内存检查"
                percent = (total - available) / total * 100
                ok = percent < 90
                scope = '主机'

            if ok:
                return True, f"{scope}内存使用率: {percent:.1f}%"
            return False, f"{scope}内存使用率过高: {percent:.1f}%"
        except Exception as e:
            return False, f"内存检查异常: {e}"
    
    def check_disk_space(self) -> Tuple[bool, str]:
        """检查磁盘空间"""
        try:
            import shutil
            target = DATA_DIR if os.path.isdir(DATA_DIR) else os.getcwd()
            total, used, free = shutil.disk_usage(target)
            usage_percent = used / total * 100
            
            if usage_percent < 90:
                return True, f"磁盘使用率: {usage_percent:.1f}%"
            else:
                return False, f"磁盘空间不足: {usage_percent:.1f}%"
        except Exception as e:
            return False, f"磁盘检查异常: {e}"
    
    def run_checks(self) -> Dict[str, any]:
        """运行所有健康检查"""
        results = {
            'healthy': True,
            'timestamp': time.time(),
            'checks': {},
            'summary': ''
        }
        
        failed_checks = []
        
        for check in self.checks:
            check_name = check.__name__.replace('check_', '')
            try:
                success, message = check()
                results['checks'][check_name] = {
                    'success': success,
                    'message': message
                }
                
                if success:
                    logger.info(f"[OK] {check_name}: {message}")
                else:
                    logger.error(f"[FAIL] {check_name}: {message}")
                    failed_checks.append(check_name)
                    results['healthy'] = False
            except Exception as e:
                logger.error(f"[ERROR] {check_name}: 检查失败 - {e}")
                results['checks'][check_name] = {
                    'success': False,
                    'message': f"检查失败: {e}"
                }
                failed_checks.append(check_name)
                results['healthy'] = False
        
        if results['healthy']:
            results['summary'] = "所有健康检查通过"
            logger.info("[OK] 所有健康检查通过")
        else:
            results['summary'] = f"健康检查失败: {', '.join(failed_checks)}"
            logger.error(f"[FAIL] 健康检查失败: {', '.join(failed_checks)}")
        
        return results


def main():
    """主函数"""
    checker = HealthChecker()
    results = checker.run_checks()
    
    # 输出JSON格式的结果（用于监控系统）
    if '--json' in sys.argv:
        print(json.dumps(results, indent=2))
    
    # 根据健康状态设置退出码
    if results['healthy']:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()