#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NTRIP Caster 健康检查脚本
用于Docker容器健康检查和监控
"""

import sys
import time
import socket
import urllib.request
import urllib.error
import json
import logging
from typing import Dict, List, Tuple, Optional
from pathlib import Path

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
            with urllib.request.urlopen('http://localhost:5757/health', timeout=5) as response:
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
            result = sock.connect_ex(('localhost', 2101))
            sock.close()
            
            if result == 0:
                return True, "NTRIP服务端口正常"
            else:
                return False, "NTRIP服务端口无法连接"
        except Exception as e:
            return False, f"NTRIP服务检查异常: {e}"
    
    def check_memory_usage(self) -> Tuple[bool, str]:
        """检查内存使用情况"""
        try:
            with open('/proc/meminfo', 'r') as f:
                meminfo = f.read()
            
            mem_total = 0
            mem_available = 0
            
            for line in meminfo.split('\n'):
                if line.startswith('MemTotal:'):
                    mem_total = int(line.split()[1]) * 1024  # 转换为字节
                elif line.startswith('MemAvailable:'):
                    mem_available = int(line.split()[1]) * 1024  # 转换为字节
            
            if mem_total > 0:
                usage_percent = (mem_total - mem_available) / mem_total * 100
                if usage_percent < 90:
                    return True, f"内存使用率: {usage_percent:.1f}%"
                else:
                    return False, f"内存使用率过高: {usage_percent:.1f}%"
            else:
                return False, "无法获取内存信息"
        except Exception as e:
            return False, f"内存检查异常: {e}"
    
    def check_disk_space(self) -> Tuple[bool, str]:
        """检查磁盘空间"""
        try:
            import shutil
            total, used, free = shutil.disk_usage('/app')
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
                    logger.info(f"✓ {check_name}: {message}")
                else:
                    logger.error(f"✗ {check_name}: {message}")
                    failed_checks.append(check_name)
                    results['healthy'] = False
            except Exception as e:
                logger.error(f"✗ {check_name}: 检查失败 - {e}")
                results['checks'][check_name] = {
                    'success': False,
                    'message': f"检查失败: {e}"
                }
                failed_checks.append(check_name)
                results['healthy'] = False
        
        if results['healthy']:
            results['summary'] = "所有健康检查通过"
            logger.info("✓ 所有健康检查通过")
        else:
            results['summary'] = f"健康检查失败: {', '.join(failed_checks)}"
            logger.error(f"✗ 健康检查失败: {', '.join(failed_checks)}")
        
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