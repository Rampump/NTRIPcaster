# -*- coding: utf-8 -*-
"""
RTCM数据解析模块（优化版）
提供RTCM消息解析、STR修正和实时数据可视化功能，优化星座信息查询逻辑
"""

# 标准库导入
import threading
import time
import socket
import logging
from typing import Dict, Optional, Callable, List, Tuple
from collections import defaultdict

# 第三方库导入
from pyrtcm import RTCMReader, RTCMMessage, parse_msm
from pyproj import Transformer

# 本地模块导入
from . import forwarder
from . import logger
from .logger import log_debug, log_info, log_warning, log_error, log_critical

# 国家代码映射表（2字符 -> 3字符）- ISO 3166-1 完整映射
COUNTRY_CODE_MAP = {
    # 亚洲
    "CN": "CHN", "JP": "JPN", "KR": "KOR", "IN": "IND", "ID": "IDN", "TH": "THA",
    "VN": "VNM", "MY": "MYS", "SG": "SGP", "PH": "PHL", "BD": "BGD", "PK": "PAK",
    "LK": "LKA", "MM": "MMR", "KH": "KHM", "LA": "LAO", "BN": "BRN", "MN": "MNG",
    "KZ": "KAZ", "UZ": "UZB", "TM": "TKM", "KG": "KGZ", "TJ": "TJK", "AF": "AFG",
    "IR": "IRN", "IQ": "IRQ", "SY": "SYR", "JO": "JOR", "LB": "LBN", "IL": "ISR",
    "PS": "PSE", "SA": "SAU", "AE": "ARE", "QA": "QAT", "BH": "BHR", "KW": "KWT",
    "OM": "OMN", "YE": "YEM", "TR": "TUR", "GE": "GEO", "AM": "ARM", "AZ": "AZE",
    "CY": "CYP", "TW": "TWN", "HK": "HKG", "MO": "MAC", "BT": "BTN", "MV": "MDV",
    "NP": "NPL", "TL": "TLS",
    
    # 欧洲
    "GB": "GBR", "DE": "DEU", "FR": "FRA", "IT": "ITA", "ES": "ESP", "PT": "PRT",
    "NL": "NLD", "BE": "BEL", "CH": "CHE", "AT": "AUT", "SE": "SWE", "NO": "NOR",
    "DK": "DNK", "FI": "FIN", "IS": "ISL", "IE": "IRL", "LU": "LUX", "MT": "MLT",
    "PL": "POL", "CZ": "CZE", "SK": "SVK", "HU": "HUN", "SI": "SVN", "HR": "HRV",
    "BA": "BIH", "RS": "SRB", "ME": "MNE", "MK": "MKD", "AL": "ALB", "GR": "GRC",
    "BG": "BGR", "RO": "ROU", "MD": "MDA", "UA": "UKR", "BY": "BLR", "LT": "LTU",
    "LV": "LVA", "EE": "EST", "RU": "RUS", "AD": "AND", "MC": "MCO", "SM": "SMR",
    "VA": "VAT", "LI": "LIE",
    
    # 北美洲
    "US": "USA", "CA": "CAN", "MX": "MEX", "GT": "GTM", "BZ": "BLZ", "SV": "SLV",
    "HN": "HND", "NI": "NIC", "CR": "CRI", "PA": "PAN", "CU": "CUB", "JM": "JAM",
    "HT": "HTI", "DO": "DOM", "TT": "TTO", "BB": "BRB", "GD": "GRD", "VC": "VCT",
    "LC": "LCA", "DM": "DMA", "AG": "ATG", "KN": "KNA", "BS": "BHS",
    
    # 南美洲
    "BR": "BRA", "AR": "ARG", "CL": "CHL", "PE": "PER", "CO": "COL", "VE": "VEN",
    "EC": "ECU", "BO": "BOL", "PY": "PRY", "UY": "URY", "GY": "GUY", "SR": "SUR",
    "GF": "GUF", "FK": "FLK",
    
    # 非洲
    "ZA": "ZAF", "EG": "EGY", "NG": "NGA", "KE": "KEN", "ET": "ETH", "GH": "GHA",
    "UG": "UGA", "TZ": "TZA", "MZ": "MOZ", "MG": "MDG", "CM": "CMR", "CI": "CIV",
    "NE": "NER", "BF": "BFA", "ML": "MLI", "MW": "MWI", "ZM": "ZMB", "ZW": "ZWE",
    "BW": "BWA", "NA": "NAM", "SZ": "SWZ", "LS": "LSO", "MU": "MUS", "SC": "SYC",
    "MR": "MRT", "SN": "SEN", "GM": "GMB", "GW": "GNB", "GN": "GIN", "SL": "SLE",
    "LR": "LBR", "TG": "TGO", "BJ": "BEN", "CV": "CPV", "ST": "STP", "GQ": "GNQ",
    "GA": "GAB", "CG": "COG", "CD": "COD", "CF": "CAF", "TD": "TCD", "LY": "LBY",
    "TN": "TUN", "DZ": "DZA", "MA": "MAR", "EH": "ESH", "SD": "SDN", "SS": "SSD",
    "ER": "ERI", "DJ": "DJI", "SO": "SOM", "RW": "RWA", "BI": "BDI", "KM": "COM",
    "AO": "AGO",
    
    # 大洋洲
    "AU": "AUS", "NZ": "NZL", "FJ": "FJI", "PG": "PNG", "SB": "SLB", "NC": "NCL",
    "PF": "PYF", "VU": "VUT", "WS": "WSM", "TO": "TON", "TV": "TUV", "KI": "KIR",
    "NR": "NRU", "PW": "PLW", "FM": "FSM", "MH": "MHL", "CK": "COK", "NU": "NIU",
    "TK": "TKL", "WF": "WLF", "AS": "ASM", "GU": "GUM", "MP": "MNP",
    
    # 南极洲
    "AQ": "ATA"
}

# RTCM消息类型与载波的映射关系（同时包含星座信息）
CARRIER_INFO = {
    # GPS (1070-1077)
    (1070, 1070): ("GPS", "L1"),
    (1071, 1071): ("GPS", "L1+L2"),
    (1072, 1072): ("GPS", "L2"),
    (1073, 1073): ("GPS", "L1+C1"),
    (1074, 1074): ("GPS", "L5"),
    (1075, 1075): ("GPS", "L1+L5"),
    (1076, 1076): ("GPS", "L2+L5"),
    (1077, 1077): ("GPS", "L1+L2+L5"),
    
    # GLONASS (1080-1087)
    (1080, 1080): ("GLO", "G1"),
    (1081, 1081): ("GLO", "G1+G2"),
    (1082, 1082): ("GLO", "G2"),
    (1083, 1083): ("GLO", "G1+C1"),
    (1084, 1084): ("GLO", "G3"),
    (1085, 1085): ("GLO", "G1+G3"),
    (1086, 1086): ("GLO", "G2+G3"),
    (1087, 1087): ("GLO", "G1+G2+G3"),
    
    # 伽利略 (1090-1097)
    (1090, 1090): ("GAL", "E1"),
    (1091, 1091): ("GAL", "E1+E5b"),
    (1092, 1092): ("GAL", "E5b"),
    (1093, 1093): ("GAL", "E1+C1"),
    (1094, 1094): ("GAL", "E5a"),
    (1095, 1095): ("GAL", "E1+E5a"),
    (1096, 1096): ("GAL", "E5b+E5a"),
    (1097, 1097): ("GAL", "E1+E5a+E5b"),
    
    # 日本QZSS (1100-1107)
    (1100, 1100): ("QZSS", "L1"),
    (1101, 1101): ("QZSS", "L1+L2"),
    (1102, 1102): ("QZSS", "L2"),
    (1103, 1103): ("QZSS", "L1+C1"),
    (1104, 1104): ("QZSS", "L5"),
    (1105, 1105): ("QZSS", "L1+L5"),
    (1106, 1106): ("QZSS", "L2+L5"),
    (1107, 1107): ("QZSS", "L1+L2+L5+LEX"),
    
    # 印度IRNSS (1110-1117)
    (1110, 1110): ("IRNSS", "L5"),
    (1111, 1111): ("IRNSS", "L5+S"),
    (1112, 1112): ("IRNSS", "S"),
    (1113, 1113): ("IRNSS", "L5+C1"),
    (1114, 1114): ("IRNSS", "L1"),
    (1115, 1115): ("IRNSS", "L1+L5"),
    (1116, 1116): ("IRNSS", "L1+S"),
    (1117, 1117): ("IRNSS", "L1+L5+S"),
    
    # 北斗BDS (1120-1127)
    (1120, 1120): ("BDS", "B1I"),
    (1121, 1121): ("BDS", "B1I+B3I"),
    (1122, 1122): ("BDS", "B3I"),
    (1123, 1123): ("BDS", "B1I+B2I"),
    (1124, 1124): ("BDS", "B2I"),
    (1125, 1125): ("BDS", "B1I+B2I"),
    (1126, 1126): ("BDS", "B2I+B3I"),
    (1127, 1127): ("BDS", "B1I+B2I+B3I"),
    
    # SBAS (1040-1047)
    (1040, 1040): ("SBAS", "L1"),
    (1041, 1041): ("SBAS", "L1+L5"),
    (1042, 1042): ("SBAS", "L5"),
    (1043, 1043): ("SBAS", "L1+C1"),
    (1044, 1044): ("SBAS", "L1+L2"),
    (1045, 1045): ("SBAS", "L2+L5"),
    (1046, 1046): ("SBAS", "L2"),
    (1047, 1047): ("SBAS", "L1+L2+L5")
}

# 数据类型枚举
class DataType:
    MSM_SATELLITE = "msm_satellite"  # MSM卫星信号数据
    GEOGRAPHY = "geography"          # 地理位置数据
    DEVICE_INFO = "device_info"      # 设备信息
    BITRATE = "bitrate"              # 比特率数据
    MESSAGE_STATS = "message_stats"  # 消息统计数据


class RTCMParserThread(threading.Thread):
    """RTCM数据解析线程"""
    
    def __init__(self, mount_name: str, mode: str = "str_fix", 
                 duration: int = 30, push_callback: Optional[Callable[[Dict], None]] = None):
        super().__init__(daemon=True)
        self.mount_name = mount_name
        self.mode = mode  # str_fix：STR修正；realtime_web：Web可视化
        self.duration = duration  # 仅STR模式有效
        self.push_callback = push_callback  # 数据推送回调
        
        # 线程控制
        self.running = threading.Event()
        self.running.set()
        
        # 解析结果存储
        self.result: Dict = {
            "mount": mount_name,
            "location": None,  # 位置信息：ecef、经纬度等
            "device": None,    # 设备信息：接收机、天线等
            "bitrate": None,   # 比特率
            "message_stats": {
                "types": defaultdict(int),  # 消息类型计数
                "gnss": set(),              # 星座集合
                "carriers": set(),          # 载波集合
                "frequency": {}             # 消息频率
            }
        }
        self.result_lock = threading.Lock()
        
        # 通信管道
        self.pipe_r, self.pipe_w = socket.socketpair()
        self.pipe_r.settimeout(5.0)  # 增加超时时间以减少网络延迟导致的错误
        
        # 统计相关变量
        self.stats_start_time = time.time()
        self.total_bytes = 0  # 总字节数（用于比特率计算）
        self.last_stats_time = time.time()  # 上次统计时间
        self.stats_delay = 5.0  # 延迟5秒开始统计，避免缓冲区历史数据影响
        self.stats_enabled = False  # 统计是否已启用
        
        log_debug(f"RTCMParserThread初始化 [挂载点: {mount_name}, 模式: {mode}]")

    def run(self):
        """线程主逻辑"""
        log_info(f"启动解析线程 [挂载点: {self.mount_name}, 模式: {self.mode}]")
        try:
            # 注册数据订阅
            forwarder.register_subscriber(self.mount_name, self.pipe_w)
            stream = self.pipe_r.makefile("rb")
            reader = RTCMReader(stream)
            self.start_time = time.time()

            while self.running.is_set():
                # STR模式超时检查
                if self.mode == "str_fix" and time.time() - self.start_time > self.duration:
                    log_info(f"RTCM解析线程已完成 [挂载点: {self.mount_name}, 时长: {self.duration}s]")
                    break

                # 读取并解析消息
                try:
                    raw, msg = next(reader)
                    if not msg:
                        continue
                    
                    # 检查是否需要启用统计（延迟5秒后开始）
                    current_time = time.time()
                    if not self.stats_enabled and current_time - self.start_time >= self.stats_delay:
                        self.stats_enabled = True
                        self.stats_start_time = current_time  # 重置统计开始时间
                        self.last_stats_time = current_time
                        self.total_bytes = 0  # 重置字节计数
                        log_info(f"开始统计比特率 [挂载点: {self.mount_name}] - 延迟{self.stats_delay}秒后启用")
                    
                    # 更新总字节数（仅在统计启用后）
                    if self.stats_enabled:
                        self.total_bytes += len(raw)
                    
                    # 解析消息类型
                    msg_id = self._get_msg_id(msg)
                    if msg_id:
                        # 调试：记录特殊消息类型
                        # if msg_id in (1005, 1006, 1033):
                        #     log_info(f"[RTCM解析] 接收到消息类型 {msg_id} - 挂载点: {self.mount_name}")
                        
                        # 通用统计更新
                        self._update_message_stats(msg_id)
                        
                        # 模式分发处理
                        if self.mode == "str_fix":
                            self._process_str_fix(msg, msg_id, raw)
                        else:  # realtime_web
                            self._process_realtime_web(msg, msg_id, raw)
                    
                    # 10秒统计更新（仅在统计启用后）
                    if self.stats_enabled and time.time() - self.last_stats_time >= 10:
                        self._calculate_bitrate()
                        self._calculate_message_frequency()
                        self._generate_gnss_carrier_info()

                except StopIteration:
                    break
                except socket.timeout:
                    continue
                except Exception as e:
                    # 对超时相关错误进行特殊处理，避免日志过多
                    error_msg = str(e)
                    if "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
                        # 超时错误只记录调试信息，不记录错误日志
                        continue
                    else:
                        log_error(f"消息解析错误 [挂载点: {self.mount_name}]: {error_msg}")

        except Exception as e:
            log_error(f"解析线程异常 [挂载点: {self.mount_name}]: {str(e)}")
        finally:
            # 清理资源
            forwarder.unregister_subscriber(self.mount_name, self.pipe_w)
            self.pipe_r.close()
            self.pipe_w.close()
            log_info(f"解析线程停止 [挂载点: {self.mount_name}]")

    def _get_msg_id(self, msg: RTCMMessage) -> Optional[int]:
        """获取消息ID（安全处理）"""
        try:
            return int(getattr(msg, 'identity', -1))
        except (ValueError, TypeError):
            return None

    # -------------------------- 位置信息处理函数 --------------------------
    def _process_location_message(self, msg: RTCMMessage, msg_id: int) -> None:
        """处理1005/1006消息，提取位置信息和基准站ID"""
        if msg_id not in (1005, 1006):
            return

        # print(f"[1005/1006消息] 收到位置消息: {msg_id}")
            # print(f"[1005/1006消息] 消息对象: {msg}")
            # print(f"[1005/1006消息] 消息属性: {dir(msg)}")

        # 提取基准站ID
        station_id = getattr(msg, "DF003", None) if hasattr(msg, "DF003") else None
        # print(f"[1005/1006消息] 基准站ID: {station_id}")
        
        # 提取ECEF坐标并转换为经纬度
        try:
            x, y, z = msg.DF025, msg.DF026, msg.DF027
            # print(f"[1005/1006消息] ECEF坐标: X={x}, Y={y}, Z={z}")
            
            transformer = Transformer.from_crs("epsg:4978", "epsg:4326", always_xy=True)
            lon, lat, height = transformer.transform(x, y, z)
            # print(f"[1005/1006消息] 转换后坐标: 经度={lon}, 纬度={lat}, 高程={height}")
            
            # 反向地理编码 - 直接获取完整信息，无需映射转换
            country_code, country_name, city = self._reverse_geocode(lat, lon)
            # 为了保持STR修正的兼容性，仍需要3字符国家代码
            country_3code = COUNTRY_CODE_MAP.get(country_code, country_code) if country_code else None
            # print(f"[1005/1006消息] 地理编码: 国家代码={country_code}, 国家名称={country_name}, 城市={city}")

            # 整理结果 - 优化数据结构，包含原始XYZ和基准站ID
            location_data = {
                "mount": self.mount_name,
                "mount_name": self.mount_name,  # 前端兼容字段
                "station_id": station_id,
                "id": station_id,  # 前端兼容字段
                "name": self.mount_name,  # 前端兼容字段
                # 原始ECEF坐标
                "ecef": {"x": x, "y": y, "z": z},
                "x": x,  # 前端兼容字段
                "y": y,  # 前端兼容字段
                "z": z,  # 前端兼容字段
                # 转换后的地理坐标
                "lat": round(lat, 8),
                "latitude": round(lat, 8),  # 前端兼容字段
                "lon": round(lon, 8),
                "longitude": round(lon, 8),  # 前端兼容字段
                "height": round(height, 3),
                # 地理位置信息
                "country": country_3code,  # 3字符国家代码（用于STR修正）
                "country_code": country_code,  # 2字符国家代码
                "country_name": country_name,  # 完整国家名称（前端直接使用）
                "city": city
            }

            # print(f"[1005/1006消息] 最终数据: {location_data}")

            # 更新结果并推送
            with self.result_lock:
                self.result["location"] = location_data
            self._push_data(DataType.GEOGRAPHY, location_data)
            # print(f"[1005/1006消息] 数据已推送到前端")

        except Exception as e:
            # print(f"[1005/1006消息] 位置信息解析错误: {str(e)}")
            log_error(f"位置信息解析错误: {str(e)}")

    def _reverse_geocode(self, lat: float, lon: float, min_population: int = 10000) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        坐标反向查询国家代码、完整国家名称和城市名称
        
        参数:
            lat: 纬度
            lon: 经度
            min_population: 最小人口阈值（过滤小城市，默认10000）
        
        返回:
            Tuple[国家代码(2字符), 完整国家名称, 城市名称]，失败时返回(None, None, None)
        """
        try:
            import reverse_geocode  # 注意：是reverse_geocode库，非reverse_geocoder
            # 对于单个坐标，使用get方法而不是search方法
            result = reverse_geocode.get((lat, lon), min_population=min_population)
            if not result:
                log_warning(f"地理编码查询无结果: lat={lat}, lon={lon}")
                return None, None, None
            
            # 提取所需字段（处理可能的缺失值）
            country_code = result.get("country_code")
            country_name = result.get("country")
            city_name = result.get("city")
            return country_code, country_name, city_name
        except ImportError:
            log_warning("未安装reverse_geocode库，请先执行：pip install reverse-geocode")
            return None, None, None
        except Exception as e:
            log_warning(f"地理编码查询失败: {str(e)}")
            return None, None, None

    # -------------------------- 1033消息处理函数 --------------------------
    def _process_device_info(self, msg: RTCMMessage, msg_id: int) -> None:
        """处理1033消息，提取设备信息"""
        if msg_id != 1033:
            return

        # print(f"[1033消息] 收到设备信息消息: {msg_id}")
        # print(f"[1033消息] 消息对象: {msg}")
        # print(f"[1033消息] 消息属性: {dir(msg)}")

        try:
            # 提取设备信息（根据RTCM 1033标准字段）
            # 1033消息的字段是分段的，需要拼接
            
            # 提取天线描述（DF030_xx字段）
            antenna_parts = []
            # print(f"[1033消息] 开始解析天线描述字段...")
            for i in range(1, 21):  # DF030_01 到 DF030_20
                field_name = f"DF030_{i:02d}"
                if hasattr(msg, field_name):
                    part = getattr(msg, field_name)
                    # print(f"[1033消息] {field_name}: {part} (类型: {type(part)})")
                    if part and part != 0:  # 跳过空值
                        antenna_parts.append(chr(part) if isinstance(part, int) and 0 < part < 256 else str(part))
            antenna = ''.join(antenna_parts).strip() if antenna_parts else None
            # print(f"[1033消息] 天线描述拼接结果: '{antenna}'")
            
            # 提取接收机类型（DF228_xx字段）
            receiver_parts = []
            # print(f"[1033消息] 开始解析接收机类型字段...")
            for i in range(1, 31):  # DF228_01 到 DF228_30
                field_name = f"DF228_{i:02d}"
                if hasattr(msg, field_name):
                    part = getattr(msg, field_name)
                    # print(f"[1033消息] {field_name}: {part} (类型: {type(part)})")
                    if part and part != 0:  # 跳过空值
                        receiver_parts.append(chr(part) if isinstance(part, int) and 0 < part < 256 else str(part))
            receiver = ''.join(receiver_parts).strip() if receiver_parts else None
            # print(f"[1033消息] 接收机类型拼接结果: '{receiver}'")
            
            # 提取固件版本（DF230_xx字段）
            firmware_parts = []
            # print(f"[1033消息] 开始解析固件版本字段...")
            for i in range(1, 21):  # DF230_01 到 DF230_20
                field_name = f"DF230_{i:02d}"
                if hasattr(msg, field_name):
                    part = getattr(msg, field_name)
                    # print(f"[1033消息] {field_name}: {part} (类型: {type(part)})")
                    if part and part != 0:  # 跳过空值
                        firmware_parts.append(chr(part) if isinstance(part, int) and 0 < part < 256 else str(part))
            firmware = ''.join(firmware_parts).strip() if firmware_parts else None
            # print(f"[1033消息] 固件版本拼接结果: '{firmware}'")
            
            # 提取天线序列号（DF033字段或其他可能字段）
            antenna_serial = getattr(msg, "DF033", None)
            # print(f"[1033消息] DF033 (天线序列号): {antenna_serial}")
            if not antenna_serial:
                # 尝试其他可能的序列号字段
                antenna_serial = getattr(msg, "DF032", None)
                # print(f"[1033消息] DF032 (备用序列号): {antenna_serial}")
            
            # print(f"[1033消息] 接收机: {receiver}")
            # print(f"[1033消息] 固件: {firmware}")
            # print(f"[1033消息] 天线: {antenna}")
            # print(f"[1033消息] 天线序列号: {antenna_serial}")
            
            # 打印所有可用属性以便调试
            # print(f"[1033消息] 所有属性: {[attr for attr in dir(msg) if not attr.startswith('_')]}")
            
            device_data = {
                "mount": self.mount_name,
                "receiver": receiver,
                "firmware": firmware,
                "antenna": antenna,
                "antenna_firmware": antenna_serial
            }

            # print(f"[1033消息] 最终数据: {device_data}")

            # 更新结果并推送
            with self.result_lock:
                self.result["device"] = device_data
            self._push_data(DataType.DEVICE_INFO, device_data)
            # print(f"[1033消息] 数据已推送到前端")

        except Exception as e:
            # print(f"[1033消息] 设备信息解析错误: {str(e)}")
            log_error(f"设备信息解析错误: {str(e)}")

    # -------------------------- 比特率统计函数 --------------------------
    def _calculate_bitrate(self) -> None:
        """计算比特率（延迟启动后的真实数据）"""
        if not self.stats_enabled:
            return
            
        current_time = time.time()
        elapsed = current_time - self.last_stats_time
        if elapsed < 1:  # 避免除以零
            return

        bitrate = (self.total_bytes * 8) / elapsed  # 字节转比特
        total_elapsed = current_time - self.stats_start_time  # 总统计时间
        
        with self.result_lock:
            self.result["bitrate"] = round(bitrate, 2)
        
        log_debug(f"比特率统计 [挂载点: {self.mount_name}] - 周期: {elapsed:.1f}s, 字节: {self.total_bytes}, 比特率: {bitrate:.2f}bps, 总统计时间: {total_elapsed:.1f}s")
        
        self._push_data(DataType.BITRATE, {
            "mount": self.mount_name,
            "bitrate": round(bitrate, 2),
            "period": f"{elapsed:.1f}s"
        })
        log_debug(f"比特率更新 [挂载点: {self.mount_name}]: {bitrate:.2f} bps, 周期: {elapsed:.1f}s, 字节数: {self.total_bytes}")
        
        # 重置字节计数器和统计时间，避免累积导致比特率偏大
        self.total_bytes = 0
        self.last_stats_time = current_time

    # -------------------------- 消息类型统计函数 --------------------------
    def _update_message_stats(self, msg_id: int) -> None:
        """更新消息类型计数、星座和载波信息"""
        with self.result_lock:
            # 消息类型计数
            self.result["message_stats"]["types"][msg_id] += 1
            
            # 从CARRIER_INFO同时获取星座和载波信息
            for (start, end), (gnss, carrier) in CARRIER_INFO.items():
                if start <= msg_id <= end:
                    # 添加星座信息
                    self.result["message_stats"]["gnss"].add(gnss)
                    
                    # 拆分组合载波（如L1+L2拆分为L1和L2）
                    for c in carrier.split("+"):
                        self.result["message_stats"]["carriers"].add(c)
                    break

    def _calculate_message_frequency(self) -> None:
        """计算10秒内消息类型频率"""
        with self.result_lock:
            types = self.result["message_stats"]["types"]
            frequency = {}
            for msg_id, count in types.items():
                # 频率取整，至少为1
                freq = max(1, round(count / 10))  # 10秒统计周期
                frequency[msg_id] = freq
            self.result["message_stats"]["frequency"] = frequency

    def _generate_gnss_carrier_info(self) -> None:
        """生成星座和载波组合字符串并推送"""
        with self.result_lock:
            gnss_str = "+".join(sorted(self.result["message_stats"]["gnss"])) or "N/A"
            carrier_str = "+".join(sorted(self.result["message_stats"]["carriers"])) or "N/A"
            types_str = ",".join([f"{k}({v})" for k, v in self.result["message_stats"]["frequency"].items()])

            stats_data = {
                "mount": self.mount_name,
                "message_types": types_str,
                "gnss": gnss_str,
                "carriers": carrier_str
            }

        self._push_data(DataType.MESSAGE_STATS, stats_data)
        log_debug(f"统计信息更新 [挂载点: {self.mount_name}]: {stats_data}")

    # -------------------------- MSM消息处理函数 --------------------------
    def _process_msm_messages(self, msg: RTCMMessage, msg_id: int) -> None:
        """处理MSM消息，提取卫星信号强度"""
        # 判断是否为MSM消息（1040-1127范围）
        if not (1040 <= msg_id <= 1127):
            return

        try:
            # 解析MSM消息
            msm_result = parse_msm(msg)
            if not msm_result:
                return
            
            # parse_msm返回(meta, msmsats, msmcells)元组
            meta, msmsats, msmcells = msm_result
            
            if not msmcells:
                return
            
            # 构建卫星信号数据
            sats_data = []
            for cell in msmcells:
                # 提取信号强度数据
                cnr = cell.get('DF408') or cell.get('DF403') or cell.get('DF405') or 0
                if cnr > 0:  # 只处理有效的信号强度数据
                    sat_data = {
                        "id": cell.get('CELLPRN', 0),
                        "signal_type": cell.get('CELLSIG', 0),
                        "snr": cnr,
                        "lock_time": cell.get('DF407', 0),
                        "pseudorange": cell.get('DF400', 0),
                        "carrier_phase": cell.get('DF401', 0) or cell.get('DF406', 0),
                        "doppler": cell.get('DF404', 0)
                    }
                    sats_data.append(sat_data)
            
            if sats_data:
                # 推送MSM卫星信号数据
                self._push_data(DataType.MSM_SATELLITE, {
                    "gnss": meta.get('gnss', 'UNKNOWN'),
                    "msg_type": msg_id,
                    "station_id": meta.get('station', 0),
                    "epoch": meta.get('epoch', 0),
                    "total_sats": len(sats_data),
                    "sats": sats_data
                })
                log_debug(f"推送MSM卫星信号数据: {meta.get('gnss')} 消息{msg_id}, {len(sats_data)}个卫星")

        except Exception as e:
            log_debug(f"MSM消息解析跳过: {str(e)}")  # 非致命错误，仅调试日志

    # -------------------------- 模式处理分发 --------------------------
    def _process_str_fix(self, msg: RTCMMessage, msg_id: int, raw: bytes) -> None:
        """STR修正模式处理逻辑"""
        # 只处理必要消息类型
        if msg_id in (1005, 1006):
            self._process_location_message(msg, msg_id)
        elif msg_id == 1033:
            self._process_device_info(msg, msg_id)

    def _process_realtime_web(self, msg: RTCMMessage, msg_id: int, raw: bytes) -> None:
        """Web实时模式处理逻辑（处理所有消息类型）"""
        # print(f"[Web模式] 处理消息ID: {msg_id}")
        
        # 处理位置信息（1005/1006）
        if msg_id in (1005, 1006):
            # print(f"[Web模式] 调用位置消息处理函数")
            self._process_location_message(msg, msg_id)
        
        # 处理设备信息（1033）
        elif msg_id == 1033:
            # print(f"[Web模式] 调用设备信息处理函数")
            self._process_device_info(msg, msg_id)
        
        # 处理MSM消息
        elif msg_id in range(1070, 1130):
            # MSM消息不打印详细信息，避免刷屏
            self._process_msm_messages(msg, msg_id)
        else:
            self._process_location_message(msg, msg_id)
            self._process_device_info(msg, msg_id)
            self._process_msm_messages(msg, msg_id)

    # -------------------------- 数据推送 --------------------------
    def _push_data(self, data_type: str, data: Dict) -> None:
        """通过回调函数推送数据"""
        if self.push_callback:
            try:
                self.push_callback({
                    "mount_name": self.mount_name,  # 添加mount_name字段
                    "data_type": data_type,  # 修改为data_type以保持一致性
                    "timestamp": time.time(),
                    **data  # 展开data字典的内容到顶层
                })
            except Exception as e:
                log_error(f"数据推送失败: {str(e)}")

    # -------------------------- 线程控制 --------------------------
    def stop(self) -> None:
        """停止解析线程"""
        self.running.clear()
        self.join(timeout=5)
        log_info(f"[挂载点: {self.mount_name}解析线程已关闭]")


# -------------------------- 解析接口 --------------------------
def start_str_fix_parser(mount_name: str, duration: int = 30, 
                         callback: Optional[Callable[[Dict], None]] = None) -> RTCMParserThread:
    """启动STR修正模式解析线程"""
    parser = RTCMParserThread(mount_name, mode="str_fix", duration=duration, push_callback=callback)
    parser.start()
    return parser


def start_web_parser(mount_name: str, callback: Optional[Callable[[Dict], None]] = None) -> RTCMParserThread:
    """启动Web实时解析线程"""
    parser = RTCMParserThread(mount_name, mode="realtime_web", push_callback=callback)
    parser.start()
    return parser
