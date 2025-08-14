# -*- coding: utf-8 -*-
"""
RTCM2解析管理器
"""

import threading
import time
from typing import Dict, Optional, Callable, Any
from .logger import log_debug, log_info, log_warning, log_error


class RTCM2ParserManager:
    """RTCM2解析管理器 - 兼容原parser_manager接口"""
    
    def __init__(self):
        self.parsers: Dict[str, Any] = {}  # RTCMParserThread实例
        self.web_parsers: Dict[str, Any] = {}  # Web解析线程实例（单独管理）
        self.str_parsers: Dict[str, Any] = {}  # STR修正线程实例（单独管理）
        self.current_web_mount: Optional[str] = None  # 当前活跃的Web解析挂载点
        self.lock = threading.RLock()
        log_info("RTCM2数据解析管理器初始化完成")

    def start_parser(self, mount_name: str, mode: str = "str_fix", duration: int = 30, 
                     push_callback: Optional[Callable[[Dict], None]] = None) -> bool:
        """启动解析器（兼容原接口）"""
        with self.lock:
            # 如果已存在解析器，先停止
            if mount_name in self.parsers:
                self.stop_parser(mount_name)
            
            try:
                # 动态导入避免循环导入
                from .rtcm2 import start_str_fix_parser, start_web_parser
                
                if mode == "str_fix":
                    parser = start_str_fix_parser(mount_name, duration, push_callback)
                    # STR修正模式：添加到STR解析器字典
                    self.str_parsers[mount_name] = parser
                    log_info(f"启动RTCM解析对 [挂载点: {mount_name}进行STR修正, 时长: {duration}s]")
                else:  # realtime_web
                    parser = start_web_parser(mount_name, push_callback)
                    # Web解析模式：添加到Web解析器字典
                    self.web_parsers[mount_name] = parser
                    log_info(f"启动Web端[挂载点: {mount_name}RTCM数据解析]")
                
                # 保持原有兼容性
                self.parsers[mount_name] = parser
                log_info(f"已启动对[挂载点: {mount_name}, 模式: {mode}]的RTCM数据解析")
                return True
            except Exception as e:
                log_error(f"启动对[挂载点: {mount_name}]的RTCM数据解析失败: {str(e)}")
                return False

    def stop_parser(self, mount_name: str):
        """停止解析器（兼容原接口）"""
        with self.lock:
            if mount_name in self.parsers:
                parser = self.parsers[mount_name]
                parser.stop()
                del self.parsers[mount_name]
                
                # 从对应的分类字典中删除
                if mount_name in self.web_parsers:
                    del self.web_parsers[mount_name]
                    log_info(f"Web端对[挂载点: {mount_name}]RTCM数据解析已关闭")

                elif mount_name in self.str_parsers:
                    del self.str_parsers[mount_name]
                    log_info(f"已关闭对[挂载点: {mount_name}]的STR修正解析")

                else:
                    log_info(f"已关闭对[挂载点: {mount_name}]的RTCM数据解析")
             
    def get_result(self, mount_name: str) -> Optional[Dict]:
        """获取解析结果（兼容原接口）"""
        with self.lock:
            parser = self.parsers.get(mount_name)
            if parser:
                # 获取rtcm2.py的解析结果并转换为兼容格式
                result = parser.result.copy()
                
                # 转换为原接口期望的格式
                converted_result = self._convert_result_format(result)
                log_debug(f"获取解析结果 [挂载点: {mount_name}]: {converted_result is not None}")
                return converted_result
            
            log_debug(f"未找到解析器 [挂载点: {mount_name}]")
            return None

    def _convert_result_format(self, result: Dict) -> Dict:
        """将rtcm2.py的结果格式转换为原接口期望的格式"""
        converted = {
            "mount": result.get("mount"),
            "bitrate": result.get("bitrate", 0),
            "total_messages": sum(result.get("message_stats", {}).get("types", {}).values()),
            "last_update": time.time()
        }
        
        # 位置信息转换
        location = result.get("location")
        if location:
            converted.update({
                "station_id": location.get("station_id"),
                "lat": location.get("lat"),
                "lon": location.get("lon"),
                "country": location.get("country"),
                "city": location.get("city")
            })
        
        # 设备信息转换
        device = result.get("device")
        if device:
            converted.update({
                "receiver": device.get("receiver"),
                "antenna": device.get("antenna"),
                "firmware": device.get("firmware")
            })
        
        # 消息统计转换
        msg_stats = result.get("message_stats", {})
        if msg_stats:
            # GNSS系统组合
            gnss_set = msg_stats.get("gnss", set())
            converted["gnss_combined"] = "+".join(sorted(gnss_set)) if gnss_set else "N/A"
            
            # 载波组合
            carriers_set = msg_stats.get("carriers", set())
            converted["carrier_combined"] = "+".join(sorted(carriers_set)) if carriers_set else "N/A"
            
            # 消息类型字符串
            frequency = msg_stats.get("frequency", {})
            if frequency:
                msg_types_list = [f"{msg_id}({freq})" for msg_id, freq in frequency.items()]
                converted["message_types_str"] = ",".join(msg_types_list)
            else:
                converted["message_types_str"] = "N/A"
        
        return converted

    def stop_all(self):
        """停止所有解析器（兼容原接口）"""
        with self.lock:
            for mount_name in list(self.parsers.keys()):
                self.stop_parser(mount_name)
            log_info("所有解析器已停止")

    # Web模式相关方法（兼容原接口）
    def acquire_parser(self, mount_name: str, push_callback: Optional[Callable[[Dict], None]] = None) -> Optional[Dict]:
        """获取解析器（Web模式）"""
        success = self.start_parser(mount_name, mode="realtime_web", push_callback=push_callback)
        if success:
            return self.get_result(mount_name)
        return None

    def release_parser(self, mount_name: str):
        """释放解析器（Web模式）"""
        self.stop_parser(mount_name)

    def start_realtime_parsing(self, mount_name: str, push_callback: Optional[Callable[[Dict], None]] = None) -> bool:
        """启动实时解析（Web模式）- 改进版：先清理前一个Web解析线程，再启动新的"""
        with self.lock:
            # 第一步：清理前一个Web解析线程（如果存在）
            if self.current_web_mount and self.current_web_mount != mount_name:
                log_info(f"检测到前一个Web解析线程 [挂载点: {self.current_web_mount}]，准备清理")
                self._stop_web_parser_only(self.current_web_mount)
            
            # 第二步：如果当前挂载点已有Web解析线程，也要先停止
            if mount_name in self.web_parsers:
                log_info(f"当前挂载点 [挂载点: {mount_name}] 已有Web解析线程，先停止")
                self._stop_web_parser_only(mount_name)
            
            # 第三步：启动新的Web解析线程
            success = self.start_parser(mount_name, mode="realtime_web", push_callback=push_callback)
            if success:
                # 更新当前活跃的Web解析挂载点
                self.current_web_mount = mount_name
                log_info(f"Web解析线程启动成功，当前活跃挂载点: {mount_name}")
            
            return success

    def _stop_web_parser_only(self, mount_name: str):
        """仅停止指定挂载点的Web解析线程，不影响STR修正线程"""
        if mount_name in self.web_parsers:
            parser = self.web_parsers[mount_name]
            parser.stop()
            del self.web_parsers[mount_name]
            
            # 从通用字典中删除（如果存在）
            if mount_name in self.parsers:
                del self.parsers[mount_name]
            
            # 清理当前活跃挂载点标记
            if self.current_web_mount == mount_name:
                self.current_web_mount = None
            
            log_info(f"已停止Web解析线程 [挂载点: {mount_name}]，STR修正线程不受影响")

    def stop_realtime_parsing(self):
        """停止所有实时解析（Web模式）- 改进版：仅停止Web解析线程，保护STR修正线程"""
        with self.lock:
            # 仅停止Web解析线程，不影响STR修正线程
            web_mounts = list(self.web_parsers.keys())
            for mount_name in web_mounts:
                self._stop_web_parser_only(mount_name)
            
            # 清理当前活跃挂载点
            self.current_web_mount = None
            
            if web_mounts:
                log_info(f"已停止所有Web解析线程 [挂载点: {', '.join(web_mounts)}]，STR修正线程继续运行")
            else:
                log_info("没有活跃的Web解析线程需要停止")

    def update_parsing_heartbeat(self, mount_name: str):
        """更新解析心跳（兼容原接口，暂时无需实现）"""
        pass

    def get_parsed_mount_data(self, mount_name: str, limit: int = None) -> Optional[Dict]:
        """获取挂载点解析数据（兼容原接口）"""
        return self.get_result(mount_name)

    def get_mount_statistics(self, mount_name: str) -> Optional[Dict]:
        """获取挂载点统计信息（兼容原接口）"""
        result = self.get_result(mount_name)
        if result:
            return {
                "bitrate": result.get("bitrate", 0),
                "total_messages": result.get("total_messages", 0),
                "last_update": result.get("last_update")
            }
        return None

    def get_parser_status(self) -> Dict:
        """获取解析器状态信息"""
        with self.lock:
            return {
                "total_parsers": len(self.parsers),
                "web_parsers": len(self.web_parsers),
                "str_parsers": len(self.str_parsers),
                "current_web_mount": self.current_web_mount,
                "web_mounts": list(self.web_parsers.keys()),
                "str_mounts": list(self.str_parsers.keys())
            }

    def is_web_parsing_active(self, mount_name: str) -> bool:
        """检查指定挂载点是否有活跃的Web解析线程"""
        with self.lock:
            return mount_name in self.web_parsers

    def is_str_parsing_active(self, mount_name: str) -> bool:
        """检查指定挂载点是否有活跃的STR修正解析线程"""
        with self.lock:
            return mount_name in self.str_parsers

    def get_current_web_mount(self) -> Optional[str]:
        """获取当前活跃的Web解析挂载点"""
        return self.current_web_mount


# 全局单例管理器
parser_manager = RTCM2ParserManager()