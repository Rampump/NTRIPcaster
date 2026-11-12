#!/usr/bin/env python3
"""
web.py - Web管理模块
功能：提供前端接口，展示挂载点的实时信息，支持查看和查询挂载点解析数据
"""

import time
import json
import logging
import psutil
import re
import uuid
from datetime import datetime
from functools import wraps
from threading import Thread, Lock

from flask import (Flask, render_template_string, request, redirect, url_for, session,
                   jsonify, send_from_directory, g)
# from flask_cors import CORS  # 已移除，不需要CORS功能
import os
from flask_socketio import SocketIO, emit, join_room, leave_room

from .database import DatabaseManager, KEEP_USER_BINDING
from . import config
from . import logger
from .logger import log_debug, log_info, log_warning, log_error, log_critical, log_web_request, log_system_event
from . import connection
from . import forwarder
from .rtcm2_manager import parser_manager as rtcm_manager
from .web_parser_registry import WebParserRegistry, SubscribeBusy, SubscribeRejected

# 客户端标识 Cookie：匿名访客与登录管理员共用一套身份，
# 用来把「谁在解析哪个站」这条关系记在服务端，而不是全局单值。
CLIENT_COOKIE = 'ntrip_cid'
CLIENT_COOKIE_MAX_AGE = 365 * 24 * 3600
_CID_PATTERN = re.compile(r'^[0-9a-f]{32}$')

# Socket.IO 房间名：每个挂载点一个房间，解析数据只发给订阅了该站的人
MOUNT_ROOM_PREFIX = 'mount:'


def mount_room(mount_name):
    return f'{MOUNT_ROOM_PREFIX}{mount_name}'


# 全局服务器实例引用
server_instance = None

def set_server_instance(server):
    """设置服务器实例"""
    global server_instance
    server_instance = server

def get_server_instance():
    """获取服务器实例"""
    return server_instance

# 获取日志记录器
# web_logger = logger.get_logger('main')  # 已改用直接的log_函数

class WebManager:
    """Web管理器"""

    #: 演示模式下仍允许的 POST 接口（新增用户 / 新增挂载点 / 登录）
    DEMO_ALLOWED_POSTS = {'/api/login', '/api/users', '/api/mounts'}
    #: 演示模式下仍允许的 POST 后缀（RTCM 解析启停与心跳：只动内存里的查看线程，不动数据库）
    DEMO_ALLOWED_PARSE_SUFFIXES = (
        '/rtcm-parse/start',
        '/rtcm-parse/stop',
        '/rtcm-parse/heartbeat',
    )

    def __init__(self, db_manager, data_forwarder, start_time):
        self.db_manager = db_manager
        self.data_forwarder = data_forwarder
        self.start_time = start_time
        
        # 注意：不再额外创建 connection.ConnectionManager() 实例，
        # 全文件统一使用 connection.get_connection_manager() 单例，避免重复状态。
        
        # 模板目录和静态文件目录
        self.template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
        self.static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
        
        # 创建Flask应用
        self.app = Flask(__name__, static_folder=self.static_dir, static_url_path='/static')
        self.app.secret_key = config.FLASK_SECRET_KEY
        
        # 会话安全：HttpOnly + SameSite=Lax（阻断跨站表单提交携带 Cookie）+ 空闲超时
        from datetime import timedelta
        self.app.config.update(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE='Lax',
            SESSION_COOKIE_SECURE=bool(getattr(config, 'SESSION_COOKIE_SECURE', False)),
            PERMANENT_SESSION_LIFETIME=timedelta(seconds=config.SESSION_TIMEOUT),
        )
        
        # 登录失败计数：{(ip, username): {'count': n, 'locked_until': ts}}
        self._login_state = {}
        self._login_state_lock = Lock()
        
        # 配置CORS - 已移除，项目为同域部署，不需要CORS功能
        # CORS(self.app, origins="*" if config.DEBUG else config.WEBSOCKET_CONFIG['cors_allowed_origins'])
        
        # 创建SocketIO实例
        # 在Windows上明确使用threading模式，避免eventlet兼容性问题
        # 移除CORS配置，项目为同域部署不需要跨域支持
        self.socketio = SocketIO(
            self.app, 
            async_mode='threading',  # 明确指定threading模式
            # cors_allowed_origins="*" if config.DEBUG else config.WEBSOCKET_CONFIG['cors_allowed_origins'],  # 已移除CORS
            ping_timeout=config.WEBSOCKET_CONFIG['ping_timeout'],
            ping_interval=config.WEBSOCKET_CONFIG['ping_interval']
        )
        
        # Web 端 RTCM 实时解析的订阅注册表。
        # 线程按挂载点复用、客户端只持有订阅：10 个人看同一个站只跑 1 个解析线程，
        # A 切站不会再把 B 打断（改造前是全局单线程，start 会主动杀掉上一个）。
        self.parser_registry = WebParserRegistry(
            push_callback_provider=self._make_push_callback,
            event_sink=self._emit_parse_event,
        )
        self.parser_registry.start()

        # socket sid -> 当前订阅的挂载点（用于切站时先离开旧房间）
        self._sid_rooms = {}
        self._sid_room_lock = Lock()

        # 注册路由
        self._register_routes()
        self._register_socketio_events()
        
        # 实时数据推送线程
        self.push_thread = None
        self.push_running = False
        
        # 设置logger的web实例引用，用于实时日志推送
        logger.set_web_instance(self)

        # 演示模式启动自检（仅告警，不重置口令）
        self._demo_self_check()
    
    def _format_uptime_simple(self, uptime_seconds):
        """格式化运行时间（简单版本）"""
        try:
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            
            if days > 0:
                return f"{days}天{hours}小时{minutes}分钟"
            elif hours > 0:
                return f"{hours}小时{minutes}分钟"
            else:
                return f"{minutes}分钟"
        except:
            return "0分钟"
    
    def _client_ip(self):
        """获取客户端 IP

        只有显式开启 TRUST_PROXY_HEADERS 时才信任 X-Forwarded-For，
        否则客户端可以随意伪造该头绕过登录限流。
        """
        if getattr(config, 'TRUST_PROXY_HEADERS', False):
            forwarded = request.headers.get('X-Forwarded-For', '')
            if forwarded:
                return forwarded.split(',')[0].strip()
        return request.remote_addr or 'unknown'

    # ------------------------------------------------------------ 客户端标识
    def _ensure_client_id(self):
        """读取（或新建）客户端标识 cid

        cid 只是「这台浏览器」的随机标识，用来记录它订阅了哪个挂载点，
        不承载任何权限语义——真正的权限仍然看 session。所以放在 HttpOnly Cookie
        里即可，前端不需要（也读不到）它。
        """
        cid = request.cookies.get(CLIENT_COOKIE)
        if cid and _CID_PATTERN.match(cid):
            return cid
        cid = uuid.uuid4().hex
        g.new_client_id = cid
        return cid

    def _client_id(self):
        """取当前请求的 cid（before_request 已算好，兜底再算一次）"""
        cid = getattr(g, 'client_id', None)
        if cid:
            return cid
        cid = self._ensure_client_id()
        g.client_id = cid
        return cid

    # ------------------------------------------------------------ 演示模式
    def _demo_write_blocked(self):
        """演示模式下拦截写操作

        规则是 fail-closed：除了明确列出的「新增用户 / 新增挂载点 / 启停解析」，
        其余任何非只读请求一律 403。将来新增的写接口默认是安全的，
        要放开必须显式加进白名单。
        """
        if not config.DEMO_MODE:
            return None
        method = request.method.upper()
        if method in ('GET', 'HEAD', 'OPTIONS'):
            return None

        path = request.path
        if not path.startswith('/api/'):
            return None

        if method == 'POST':
            if path in self.DEMO_ALLOWED_POSTS:
                return None
            if path.startswith('/api/mount/') and path.endswith(self.DEMO_ALLOWED_PARSE_SUFFIXES):
                return None
            if path.startswith('/api/public/'):
                return None

        return jsonify({
            'error': '演示模式：仅允许新增，不支持修改或删除',
            'code': 'demo_readonly',
        }), 403

    def _demo_self_check(self):
        """启动自检：演示模式下 admin 口令必须是默认值

        不强制重置（用户明确要求），只在口令已被改过时告警——
        否则演示站会变成「谁都进不去」，而且改密码本身又被 403 挡住，无法自救。
        """
        if not config.DEMO_MODE:
            return
        username = config.DEFAULT_ADMIN.get('username', 'admin')
        password = config.DEFAULT_ADMIN.get('password', 'admin123')
        try:
            ok = self.db_manager.verify_admin(username, password)
        except Exception as e:
            log_warning(f"演示模式自检失败，无法校验管理员口令: {e}")
            return
        if not ok:
            log_warning(
                f"演示模式已开启，但管理员 {username} 的口令不是默认值 {password}。"
                "演示模式下无法通过界面修改口令，请确认数据库是否为新建库，"
                "或临时关闭 demo_mode 改回默认口令。"
            )
        else:
            log_info(f"演示模式已开启：管理员 {username} / {password}，写操作仅限新增")


    # ------------------------------------------------------------ Socket 房间
    def _bind_socket_room(self, mount):
        """把当前 socket 从旧挂载点房间移到新挂载点房间（mount=None 表示离开）"""
        sid = request.sid
        with self._sid_room_lock:
            old = self._sid_rooms.get(sid)
            if old == mount:
                return
        if old:
            try:
                leave_room(mount_room(old))
            except Exception as e:
                log_debug(f"离开房间失败 [挂载点: {old}]: {e}")
        if mount:
            try:
                join_room(mount_room(mount))
            except Exception as e:
                log_warning(f"加入房间失败 [挂载点: {mount}]: {e}")
                return
            with self._sid_room_lock:
                self._sid_rooms[sid] = mount
        else:
            with self._sid_room_lock:
                self._sid_rooms.pop(sid, None)

    @staticmethod
    def _public_stats(stats):
        """把完整系统统计裁剪成可以给匿名访客的版本

        去掉用户明细（账号名 + 连接 IP）与源站 IP：这些属于内部信息，
        「数据页对所有人开放」不等于「把用户和源站地址也公开」。
        """
        if not isinstance(stats, dict):
            return stats
        out = dict(stats)
        out.pop('users', None)
        mounts = stats.get('mounts')
        if isinstance(mounts, list):
            safe = []
            for m in mounts:
                if not isinstance(m, dict):
                    continue
                safe.append({
                    'mount_name': m.get('mount_name'),
                    'uptime': m.get('uptime'),
                    'data_count': m.get('data_count'),
                    'total_bytes': m.get('total_bytes'),
                    'total_messages': m.get('total_messages'),
                    'data_rate': m.get('data_rate'),
                    'user_count': m.get('user_count'),
                    'status': m.get('status'),
                })
            out['mounts'] = safe
        return out


    # ------------------------------------------------------------ RTCM 解析
    def _make_push_callback(self, mount_name):
        """构造某个挂载点的解析数据推送回调

        关键改动：emit 带上 room，只发给订阅了该站的人。
        改造前是无房间广播，所有人收到所有人的数据 —— 这是「串台」的直接原因。
        """
        def _push(parsed_data):
            if not isinstance(parsed_data, dict):
                return
            mount = parsed_data.get('mount_name') or parsed_data.get('mount') or mount_name
            if not mount:
                return
            try:
                self.socketio.emit('rtcm_realtime_data', parsed_data, room=mount_room(mount))
            except Exception as e:
                log_debug(f"推送解析数据失败 [挂载点: {mount}]: {e}")
        return _push

    def _emit_parse_event(self, event, payload, mount_name):
        """把控制类事件推给某个挂载点的订阅者（例如源站离线导致解析被动停止）"""
        try:
            self.socketio.emit(event, payload, room=mount_room(mount_name))
        except Exception as e:
            log_debug(f"推送解析事件失败 [挂载点: {mount_name}]: {e}")

    def _validate_mount_param(self, mount_name):
        """校验 URL 路径里的挂载点名（它会被拼进日志与房间名）"""
        valid, err = self._validate_alphanumeric(mount_name, '挂载点名称')
        if not valid:
            return err or '挂载点名称非法'
        if len(mount_name) > 50:
            return '挂载点名称过长'
        return None

    def _get_parsed_mount_data(self, mount_name):
        """取某个挂载点的解析结果

        Web 解析线程由订阅注册表持有（按挂载点复用），不再登记到 RTCM2ParserManager，
        所以先问注册表，再回退到旧接口（STR 修正线程仍走旧管理器）。
        """
        try:
            parser = self.parser_registry.get_parser(mount_name)
            if parser is not None and hasattr(parser, 'result'):
                return rtcm_manager._convert_result_format(parser.result.copy())
        except Exception as e:
            log_debug(f"从注册表读取解析结果失败 [挂载点: {mount_name}]: {e}")
        return rtcm_manager.get_parsed_mount_data(mount_name)

    def _parse_subscribe(self, mount_name):
        """订阅某挂载点的实时解析（HTTP 层统一入口，登录与匿名共用）"""
        err = self._validate_mount_param(mount_name)
        if err:
            return jsonify({'error': err, 'code': 'bad_request'}), 400

        cm = connection.get_connection_manager()
        if not cm.is_mount_online(mount_name):
            return jsonify({'error': f'挂载点 {mount_name} 当前离线，无法解析', 'code': 'mount_offline'}), 400

        cid = self._client_id()
        try:
            snapshot = self.parser_registry.subscribe(cid, mount_name, self._client_ip())
        except SubscribeBusy as e:
            return jsonify({'error': e.message, 'code': e.code}), 429
        except SubscribeRejected as e:
            return jsonify({'error': e.message, 'code': e.code}), 400

        log_system_event(f"挂载点 {mount_name} 实时RTCM解析已订阅（订阅者 {snapshot.get('subscribers', 1)}）")
        return jsonify({
            'success': True,
            'message': f'Real-time RTCM parsing subscribed: {mount_name}',
            'mount_name': mount_name,
            'subscribers': snapshot.get('subscribers', 1),
        })

    def _parse_unsubscribe(self, mount_name=None):
        """退订：只摘掉当前客户端那条边，不影响别人"""
        cid = self._client_id()
        released = self.parser_registry.unsubscribe(cid)
        if released:
            log_system_event(f"已退订挂载点 {released} 的实时RTCM解析")
            return jsonify({'success': True, 'released': released})
        return jsonify({'success': True, 'released': None})

    def _parse_status(self):
        cid = self._client_id()
        status = self.parser_registry.status(cid)
        # STR 修正线程仍由原管理器负责，这里一并回传，保持旧字段兼容
        try:
            legacy = rtcm_manager.get_parser_status()
            status['str_parsers'] = legacy.get('str_parsers', 0)
            status['str_mounts'] = legacy.get('str_mounts', [])
        except Exception:
            status.setdefault('str_parsers', 0)
            status.setdefault('str_mounts', [])
        return jsonify({'success': True, 'status': status,
                        'message': 'Parser status retrieved successfully'})

    def _parse_heartbeat(self):
        data = request.get_json(silent=True) or {}
        mount_name = (data.get('mount_name') or '').strip() or None
        alive = self.parser_registry.touch(self._client_id(), mount_name)
        return jsonify({'success': True, 'parsing': alive})

    def _login_lock_remaining(self, username):
        """返回剩余锁定秒数，0 表示未锁定"""
        key = (self._client_ip(), (username or '').lower())
        now = time.time()
        with self._login_state_lock:
            state = self._login_state.get(key)
            if not state:
                return 0
            remaining = state.get('locked_until', 0) - now
            if remaining > 0:
                return int(remaining) + 1
            # 锁定期已过，重置计数
            if state.get('locked_until'):
                self._login_state.pop(key, None)
            return 0

    def _record_login_failure(self, username):
        """记录一次登录失败，达到阈值后锁定"""
        key = (self._client_ip(), (username or '').lower())
        now = time.time()
        with self._login_state_lock:
            state = self._login_state.setdefault(key, {'count': 0, 'locked_until': 0})
            state['count'] += 1
            if state['count'] >= config.LOGIN_MAX_ATTEMPTS:
                state['locked_until'] = now + config.LOGIN_LOCKOUT_SECONDS
                log_warning(f"登录失败次数过多，已锁定 {config.LOGIN_LOCKOUT_SECONDS}s: ip={key[0]}, user={username}")
            # 简单防止字典无限增长
            if len(self._login_state) > 10000:
                for k in [k for k, v in self._login_state.items() if v.get('locked_until', 0) < now]:
                    self._login_state.pop(k, None)

    def _clear_login_failure(self, username):
        key = (self._client_ip(), (username or '').lower())
        with self._login_state_lock:
            self._login_state.pop(key, None)

    def _validate_alphanumeric(self, value, field_name):
        """验证输入是否只包含英文字母、数字、下划线和中横线"""
        if not value:
            return False, f"{field_name}不能为空"
        
        # 允许英文字母、数字、下划线和中横线
        if not re.match(r'^[a-zA-Z0-9_-]+$', value):
            return False, f"{field_name}只能包含英文字母、数字、下划线和中横线"
        
        return True, ""
    
    def _load_template(self, template_name, **kwargs):
        """加载外部模板文件"""
        # 演示模式 / 登录态对所有模板可见：登录页据此提示演示账号，
        # 主界面据此决定是否渲染写入口。各调用点不必逐个传。
        kwargs.setdefault('demo_mode', bool(config.DEMO_MODE))
        kwargs.setdefault('logged_in', bool(session.get('admin_logged_in')))
        kwargs.setdefault('public_access', bool(config.PUBLIC_ACCESS))
        template_path = os.path.join(self.template_dir, template_name)
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                template_content = f.read()
            return render_template_string(template_content, **kwargs)
        except FileNotFoundError:
            log_error(f"模板文件未找到: {template_path}")
            return f"<h1>模板文件未找到: {template_name}</h1>"
        except Exception as e:
            log_error(f"加载模板文件失败: {e}")
            return f"<h1>加载模板失败: {str(e)}</h1>"
    
    def _register_routes(self):
        """注册Flask路由"""
        
        # 默认拒绝：/api/ 下的接口一律要求登录，白名单放行少数公开接口。
        # 之前靠逐个接口手加 @require_login，漏加过 /api/system/stats 等 4 个敏感接口。
        #
        # /api/public/ 是「匿名公开命名空间」：只有这个前缀对未登录访客开放，
        # 内部复用同一个实现。以后要把数据页开放给所有人，新增只读接口只要挂在
        # 这个前缀下，就不会因为「白名单里再塞一条路径」而误暴露私有接口。
        PUBLIC_API_PATHS = {'/api/login', '/api/app_info'}
        PUBLIC_API_PREFIX = '/api/public/'

        @self.app.before_request
        def _enforce_api_login():
            path = request.path
            if not path.startswith('/api/'):
                return None

            # 所有 /api 请求统一分配（或读取）客户端标识，供解析订阅使用
            g.client_id = self._ensure_client_id()

            if path in PUBLIC_API_PATHS:
                return None
            if path.startswith(PUBLIC_API_PREFIX):
                # 公开命名空间默认关闭：不开启时匿名访问按未登录处理，
                # 避免私有部署把自己的站点清单和 CPU 负载公开出去
                if config.PUBLIC_ACCESS:
                    return None
                return jsonify({'error': '未登录或登录已过期'}), 401

            if not session.get('admin_logged_in'):
                return jsonify({'error': '未登录或登录已过期'}), 401

            # 已登录后再叠一道演示模式写锁：GET 放行，写操作按白名单 fail-closed
            return self._demo_write_blocked()

        @self.app.after_request
        def _persist_client_id(response):
            """首次访问时下发 cid；之后每次访问顺延有效期"""
            if not request.path.startswith('/api/'):
                return response
            cid = getattr(g, 'new_client_id', None) or getattr(g, 'client_id', None)
            if cid:
                response.set_cookie(
                    CLIENT_COOKIE, cid,
                    max_age=CLIENT_COOKIE_MAX_AGE,
                    httponly=True,
                    samesite='Lax',
                    secure=bool(getattr(config, 'SESSION_COOKIE_SECURE', False)),
                    path='/',
                )
            return response
        
        @self.app.route('/static/<path:filename>')
        def static_files(filename):
            """静态文件服务"""
            static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
            return send_from_directory(static_dir, filename)
        
        @self.app.route('/health')
        def health():
            """健康检查端点

            供 Docker HEALTHCHECK、compose 的 service_healthy 依赖以及负载均衡探活使用。
            不要求登录，也不访问数据库，避免探活本身成为压力源。
            """
            uptime = time.time() - self.start_time if self.start_time else 0
            return jsonify({
                'status': 'ok',
                'version': config.APP_VERSION,
                'uptime': round(uptime, 3),
                'demo_mode': bool(config.DEMO_MODE),
            }), 200

        @self.app.route('/')
        def index():
            """主页 - SPA应用"""
            # 未登录且未开启公开访问：直接去登录页（与旧版行为一致），
            # 而不是先渲染一个什么数据都取不到的空壳
            if not session.get('admin_logged_in') and not config.PUBLIC_ACCESS:
                return redirect(url_for('login'))

            # 应用信息统一来自 config（版本取 VERSION 文件，联系方式取 config.ini [app]），
            # 这里不再写死任何字面量，改配置/改版本号后前端自动跟着变。
            current_year = datetime.now().year

            return self._load_template('spa.html',
                                     app_name=config.APP_NAME,
                                     app_version=config.APP_VERSION,
                                     current_year=current_year,
                                     contact_email=config.APP_CONTACT,
                                     website_url=config.APP_WEBSITE,
                                     # 服务端直接注入，前端首帧就知道该不该渲染写入口，
                                     # 避免「先闪出编辑/删除按钮、几百毫秒后消失」
                                     logged_in=bool(session.get('admin_logged_in')),
                                     demo_mode=bool(config.DEMO_MODE),
                                     public_access=bool(config.PUBLIC_ACCESS))
        
        @self.app.route('/classic')
        def classic_index():
            """经典页面已并入 SPA，保留路由做重定向（原实现加载不存在的 index.html，必然 404）"""
            return redirect('/')
        
        @self.app.route('/login', methods=['GET', 'POST'])
        def login():
            """登录页面"""
            if request.method == 'POST':
                # 表单验证
                username = request.form.get('username', '').strip()
                password = request.form.get('password', '').strip()
                
                # 防止空白提交
                if not username or not password:
                    return self._load_template('login.html', error="用户名和密码不能为空")
                
                # 长度验证
                if len(username) < 2 or len(username) > 50:
                    return self._load_template('login.html', error="用户名长度必须在2-50个字符之间")
                
                if len(password) < 6 or len(password) > 100:
                    return self._load_template('login.html', error="密码长度必须在6-100个字符之间")
                
                # 验证用户名字符
                username_valid, username_error = self._validate_alphanumeric(username, "用户名")
                if not username_valid:
                    return self._load_template('login.html', error=username_error)
                
                # 验证密码字符
                password_valid, password_error = self._validate_alphanumeric(password, "密码")
                if not password_valid:
                    return self._load_template('login.html', error=password_error)
                
                # 登录失败限流（按 IP + 用户名）
                remaining = self._login_lock_remaining(username)
                if remaining > 0:
                    return self._load_template('login.html', error=f"登录失败次数过多，请 {remaining} 秒后再试")

                # 演示模式：只认默认管理员账号，避免历史库里残留的其它管理员也能进后台
                if config.DEMO_MODE and username != config.DEFAULT_ADMIN.get('username', 'admin'):
                    return self._load_template('login.html', error="演示模式仅允许使用默认管理员账号登录")

                if self.db_manager.verify_admin(username, password):
                    self._clear_login_failure(username)
                    session.permanent = True   # 启用 PERMANENT_SESSION_LIFETIME 空闲超时
                    session['admin_logged_in'] = True
                    session['admin_username'] = username
                    
                    # 检查重定向参数
                    redirect_page = request.args.get('redirect')
                    if redirect_page and redirect_page in ['users', 'mounts', 'settings']:
                        return redirect(f'/?page={redirect_page}')
                    
                    return redirect(url_for('index'))
                else:
                    self._record_login_failure(username)
                    return self._load_template('login.html', error="用户名或密码错误")
            
            return self._load_template('login.html')
        
        @self.app.route('/logout', methods=['GET', 'POST'])
        def logout():
            """登出"""
            session.clear()
            if request.method == 'POST':
                return jsonify({'success': True})
            return redirect(url_for('login'))
        
        @self.app.route('/api/login', methods=['POST'])
        def api_login():
            """API登录"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({'error': '请求数据格式错误'}), 400
                
                username = data.get('username', '').strip()
                password = data.get('password', '').strip()
                
                # 防止空白提交
                if not username or not password:
                    return jsonify({'error': '用户名和密码不能为空'}), 400
                
                # 长度验证
                if len(username) < 2 or len(username) > 50:
                    return jsonify({'error': '用户名长度必须在2-50个字符之间'}), 400
                
                if len(password) < 6 or len(password) > 100:
                    return jsonify({'error': '密码长度必须在6-100个字符之间'}), 400
                
                # 防止SQL注入的基本字符检查
                if any(char in username for char in ["'", '"', ';', '--', '/*', '*/', 'xp_']):
                    return jsonify({'error': '用户名包含非法字符'}), 400
                
                # 登录失败限流（按 IP + 用户名）
                remaining = self._login_lock_remaining(username)
                if remaining > 0:
                    return jsonify({'error': f'登录失败次数过多，请 {remaining} 秒后再试'}), 429

                # 演示模式：只认默认管理员账号
                if config.DEMO_MODE and username != config.DEFAULT_ADMIN.get('username', 'admin'):
                    return jsonify({'error': '演示模式仅允许使用默认管理员账号登录',
                                    'code': 'demo_account'}), 403

                if self.db_manager.verify_admin(username, password):
                    self._clear_login_failure(username)
                    session.permanent = True
                    session['admin_logged_in'] = True
                    session['admin_username'] = username
                    return jsonify({
                        'success': True,
                        'message': '登录成功',
                        'token': 'session_based'  # 使用session而不是JWT
                    })
                else:
                    self._record_login_failure(username)
                    return jsonify({'error': '用户名或密码错误'}), 401
            except Exception as e:
                    log_error(f"API登录失败: {e}")
                    return jsonify({'error': '登录失败'}), 500

        
        @self.app.route('/api/mount_info/<mount>')
        @self.require_login
        def mount_info(mount):
            """获取指定挂载点的解析信息并返回给前端"""
            parsed_data = self._get_parsed_mount_data(mount)
            statistics = rtcm_manager.get_mount_statistics(mount)
            
            if parsed_data:
                return jsonify({
                    'success': True,
                    'data': parsed_data,
                    'statistics': statistics
                })
            else:
                return jsonify({
                    'success': False,
                    'message': '挂载点数据不存在或未解析'
                })
        

        
        @self.app.route('/api/system/restart', methods=['POST'])
        @self.require_login
        def restart_system():
            """重启程序API"""
            try:
                import os
                import sys
                import threading
                
                def delayed_restart():
                    """延迟重启程序：走正常关闭流程，由容器 restart 策略 / systemd 拉起"""
                    time.sleep(1)  # 给响应时间返回
                    log_info("管理员请求重启程序，发送 SIGTERM 触发优雅关闭")
                    try:
                        import signal as _signal
                        _signal.raise_signal(_signal.SIGTERM)
                    except Exception as exc:
                        log_error(f"发送重启信号失败，回退为强制退出: {exc}")
                        os._exit(0)
                
                # 在新线程中执行重启
                restart_thread = threading.Thread(target=delayed_restart)
                restart_thread.daemon = True
                restart_thread.start()
                
                return jsonify({
                    'success': True,
                    'message': '程序重启指令已发送'
                })
                
            except Exception as e:
                    log_error(f"重启程序失败: {e}")
                    return jsonify({
                        'success': False,
                        'error': str(e)
                    }), 500
        

        
        @self.app.route('/api/mount/<mount_name>/realtime')
        @self.require_login
        def api_get_mount_realtime(mount_name):
            """获取指定挂载点的实时解析数据"""
            try:
                realtime_data = self._get_parsed_mount_data(mount_name)
                if realtime_data is None:
                    return jsonify({'error': 'Mount not found'}), 404
                return jsonify(realtime_data)
            except Exception as e:
                    log_error(f"获取挂载点 {mount_name} 实时数据失败: {e}")
                    return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mount/initialize', methods=['POST'])
        @self.require_login
        def api_initialize_mount():
            """初始化挂载点（仅调试用）

            这个接口不接真实源站，只在内存里登记一个"在线"挂载点。生产环境一旦被调用，
            SOURCETABLE 里就会出现永远没有数据的幽灵挂载点，客户端连上后干等。
            因此仅在 debug_mode=true 时开放。
            """
            try:
                if not config.DEBUG:
                    return jsonify({'error': '调试接口，仅在 debug_mode=true 时可用'}), 403

                data = request.get_json() or {}
                mount_name = (data.get('mount_name') or '').strip()
                if not mount_name:
                    return jsonify({'error': 'Mount name is required'}), 400

                # 挂载点名会直接进在线列表与 STR 表，必须与 NTRIP 侧同一套白名单
                valid, err = self._validate_alphanumeric(mount_name, "挂载点名称")
                if not valid or len(mount_name) > 50:
                    return jsonify({'error': err or '挂载点名称过长'}), 400

                connection.get_connection_manager().add_mount_connection(mount_name, '127.0.0.1', 'Web Interface')
                log_system_event(f"挂载点 {mount_name} 初始化成功")
                return jsonify({'success': True, 'message': f'Mount {mount_name} initialized'})
            except Exception as e:
                log_error(f"初始化挂载点失败: {e}")
                return jsonify({'error': str(e)}), 500
        

        

        
        @self.app.route('/api/bypass/stop-all', methods=['POST'])
        @self.require_login
        def api_stop_all_bypass_parsing():
            """停止所有挂载点的旁路解析（管理员运维动作，会真的停掉所有人的解析）"""
            try:
                stopped = self.parser_registry.stop_all()
                log_system_event(f"所有旁路解析已停止，共 {len(stopped)} 个挂载点")
                return jsonify({'success': True, 'stopped': stopped,
                                'message': 'All bypass parsing stopped'})
            except Exception as e:
                log_error(f"停止所有旁路解析失败: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mount/<mount_name>/simulate', methods=['POST'])
        @self.require_login
        def api_simulate_mount_data(mount_name):
            """为挂载点模拟数据（原实现只打印日志却返回 success，属于误导，改为明确不支持）"""
            try:
                log_system_event(f"挂载点 {mount_name} 数据模拟请求：该功能未实现")
                return jsonify({
                    'success': False,
                    'error': '数据模拟功能未实现，请使用真实 NTRIP 源站推流'
                }), 501
            except Exception as e:
                log_error(f"模拟挂载点数据失败: {e}")
                return jsonify({'error': str(e)}), 500
        
        # ==================== RTCM 实时解析（登录接口） ====================
        # 语义改造（v2.3.6）：
        #   start  = 订阅（幂等：重复调不重启线程，也不会打断别人）
        #   stop   = 只退订自己；/api/mount/rtcm-parse/stop 退订我的全部订阅
        #   status = 只回显「我」的挂载点，不再把别人在看什么暴露出去
        @self.app.route('/api/mount/<mount_name>/rtcm-parse/start', methods=['POST'])
        @self.require_login
        def api_start_rtcm_parsing(mount_name):
            """订阅指定挂载点的实时RTCM解析"""
            try:
                return self._parse_subscribe(mount_name)
            except Exception as e:
                log_error(f"启动实时RTCM解析失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/mount/<mount_name>/rtcm-parse/stop', methods=['POST'])
        @self.require_login
        def api_stop_rtcm_parsing_for_mount(mount_name):
            """退订指定挂载点（只影响当前客户端，不打断其他访客）"""
            try:
                err = self._validate_mount_param(mount_name)
                if err:
                    return jsonify({'error': err, 'code': 'bad_request'}), 400
                return self._parse_unsubscribe(mount_name)
            except Exception as e:
                log_error(f"退订实时RTCM解析失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/mount/rtcm-parse/stop', methods=['POST'])
        @self.require_login
        def api_stop_rtcm_parsing():
            """退订当前客户端的全部解析订阅

            改造前这里是「停掉所有人的解析线程」，一个人关标签页就能打断全部访客。
            现在只摘掉自己那条订阅边；线程的引用计数归零后由注册表宽限期回收。
            """
            try:
                return self._parse_unsubscribe()
            except Exception as e:
                log_error(f"停止实时RTCM解析失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/mount/rtcm-parse/status', methods=['GET'])
        @self.require_login
        def api_get_rtcm_parsing_status():
            """获取当前客户端的RTCM解析状态"""
            try:
                return self._parse_status()
            except Exception as e:
                log_error(f"获取解析器状态失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/mount/rtcm-parse/heartbeat', methods=['POST'])
        @self.require_login
        def api_rtcm_parsing_heartbeat():
            """实时RTCM解析心跳维持

            改造前是空实现（update_parsing_heartbeat 里只有 pass），
            前端也从没调用过，所以「访客直接关浏览器」留下的订阅永远不回收。
            """
            try:
                return self._parse_heartbeat()
            except Exception as e:
                log_error(f"更新解析心跳失败: {e}")
                return jsonify({'error': str(e)}), 500

        # ==================== RTCM 实时解析（匿名公开接口） ====================
        # 与登录接口共用同一套实现，只是不要求登录。
        # 匿名访客必须能自己发起解析，数据页（卫星图、覆盖圈）才可能对所有人开放。
        # 真正防滥用的手段是线程上限 + 单 IP 频率，而不是「必须登录」。
        @self.app.route('/api/public/mount/<mount_name>/rtcm-parse/start', methods=['POST'])
        def public_start_rtcm_parsing(mount_name):
            try:
                return self._parse_subscribe(mount_name)
            except Exception as e:
                log_error(f"匿名订阅解析失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/public/mount/<mount_name>/rtcm-parse/stop', methods=['POST'])
        def public_stop_rtcm_parsing_for_mount(mount_name):
            try:
                err = self._validate_mount_param(mount_name)
                if err:
                    return jsonify({'error': err, 'code': 'bad_request'}), 400
                return self._parse_unsubscribe(mount_name)
            except Exception as e:
                log_error(f"匿名退订解析失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/public/mount/rtcm-parse/stop', methods=['POST'])
        def public_stop_rtcm_parsing():
            try:
                return self._parse_unsubscribe()
            except Exception as e:
                log_error(f"匿名停止解析失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/public/mount/rtcm-parse/status', methods=['GET'])
        def public_get_rtcm_parsing_status():
            try:
                return self._parse_status()
            except Exception as e:
                log_error(f"匿名查询解析状态失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/public/mount/rtcm-parse/heartbeat', methods=['POST'])
        def public_rtcm_parsing_heartbeat():
            try:
                return self._parse_heartbeat()
            except Exception as e:
                log_error(f"匿名解析心跳失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/alipay_qr')
        def alipay_qr():
            """支付宝二维码"""
            return redirect(config.ALIPAY_QR_URL)
        
        @self.app.route('/wechat_qr')
        def wechat_qr():
            """微信二维码"""
            return redirect(config.WECHAT_QR_URL)
        

        @self.app.route('/api/app_info')
        def api_app_info():
            """获取应用信息"""
            try:
                return jsonify({
                    'name': config.APP_NAME,
                    'version': config.APP_VERSION,
                    'description': config.APP_DESCRIPTION,
                    'author': config.APP_AUTHOR,
                    'contact': config.APP_CONTACT,
                    'website': config.APP_WEBSITE,
                    # 登录页 / 外部调用据此判断演示模式；界面主体用服务端注入的
                    # window.__DEMO_MODE__，避免首帧闪出写入口
                    'demo_mode': bool(config.DEMO_MODE),
                    'logged_in': bool(session.get('admin_logged_in')),
                })
            except Exception as e:
                log_error(f"获取应用信息失败: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/admin/password', methods=['PUT'])
        @self.require_login
        def api_admin_password():
            """修改当前登录管理员的口令

            设置页原来把用户名硬编码成 'admin' 再打 /api/users/admin；一旦部署时
            config.ini 的 [admin] username 换成了别的名字，请求会掉进"普通用户"分支
            （users 表里并没有这个管理员），返回"用户不存在"，管理员在界面上改不了口令。
            这里直接以会话里的管理员名为准。
            """
            try:
                data = request.get_json() or {}
                new_password = (data.get('password') or '').strip()

                if not new_password:
                    return jsonify({'error': '新密码不能为空'}), 400

                password_valid, password_error = self._validate_alphanumeric(new_password, "新密码")
                if not password_valid:
                    return jsonify({'error': password_error}), 400

                if len(new_password) < 6 or len(new_password) > 100:
                    return jsonify({'error': '新密码长度必须在6-100个字符之间'}), 400

                admin_username = session.get('admin_username') or config.DEFAULT_ADMIN['username']
                if not self.db_manager.update_admin_password(admin_username, new_password):
                    return jsonify({'error': '管理员密码更新失败'}), 500

                log_system_event(f"管理员 {admin_username} 修改了口令")
                return jsonify({'message': '管理员口令更新成功'})
            except Exception as e:
                log_error(f"修改管理员口令失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/users', methods=['GET', 'POST'])
        @self.require_login
        def api_users():
            """用户管理API"""
            if request.method == 'GET':
                # 获取用户列表
                try:
                    users = self.db_manager.get_all_users()
                    
                    # 获取在线用户信息
                    try:
                        online_users = connection.get_connection_manager().get_online_users()
                        online_usernames = list(online_users.keys())
                    except Exception as e:
                        log_error(f"获取在线用户失败: {e}")
                        online_usernames = []
                    
                    # 将tuple转换为字典格式并添加在线状态和连接数
                    user_list = []
                    for user in users:
                        username = user[1]
                        connection_count = connection.get_connection_manager().get_user_connection_count(username)
                        connect_time = connection.get_connection_manager().get_user_connect_time(username)
                        user_dict = {
                            'id': user[0],
                            'username': username,
                            'online': username in online_usernames,
                            'connection_count': connection_count,
                            'connect_time': connect_time or '-'  # 接入时间
                        }
                        user_list.append(user_dict)
                    
                    return jsonify(user_list)
                except Exception as e:
                    log_error(f"获取用户列表失败: {e}")
                    return jsonify({'error': str(e)}), 500
            
            elif request.method == 'POST':
                # 添加用户
                try:
                    data = request.get_json()
                    if not data:
                        return jsonify({'error': '请求数据格式错误'}), 400
                    
                    username = data.get('username', '').strip()
                    password = data.get('password', '').strip()
                    
                    # 表单验证
                    if not username or not password:
                        return jsonify({'error': '用户名和密码不能为空'}), 400
                    
                    # 验证用户名字符
                    username_valid, username_error = self._validate_alphanumeric(username, "用户名")
                    if not username_valid:
                        return jsonify({'error': username_error}), 400
                    
                    # 验证密码字符
                    password_valid, password_error = self._validate_alphanumeric(password, "密码")
                    if not password_valid:
                        return jsonify({'error': password_error}), 400
                    
                    elif len(username) < 2 or len(username) > 50:
                        return jsonify({'error': '用户名长度必须在2-50个字符之间'}), 400
                    elif len(password) < 6 or len(password) > 100:
                        return jsonify({'error': '密码长度必须在6-100个字符之间'}), 400
                    
                    # 检查用户是否已存在
                    existing_users = [u[1] for u in self.db_manager.get_all_users()]
                    if username in existing_users:
                        return jsonify({'error': '用户名已存在'}), 400

                    # 管理员账号在 admins 表里，users 表同名不会冲突，但管理页会出现一个
                    # 同名"用户"：点编辑会走到管理员分支，改掉的是真正的管理员口令。
                    admin_name = str(config.DEFAULT_ADMIN.get('username') or '')
                    if admin_name and username.lower() == admin_name.lower():
                        return jsonify({'error': '该用户名已被管理员账号占用'}), 400
                    
                    success, message = self.db_manager.add_user(username, password)
                    if success:
                        return jsonify({'message': message}), 201
                    else:
                        return jsonify({'error': message}), 400
                    
                except Exception as e:
                    log_error(f"添加用户失败: {e}")
                    return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/users/<username>', methods=['PUT', 'DELETE'])
        @self.require_login
        def api_user_detail(username):
            """用户详情管理API"""
            if request.method == 'PUT':
                # 更新用户信息（密码或用户名）
                try:
                    data = request.get_json()
                    if not data:
                        return jsonify({'error': '请求数据格式错误'}), 400
                    
                    new_password = data.get('password', '').strip()
                    new_username = data.get('username', '').strip()
                    
                    # 检查是否是管理员账户
                    if username == config.DEFAULT_ADMIN['username']:
                        # 管理员只能修改密码，不能修改用户名
                        if new_username:
                            return jsonify({'error': '管理员用户名不能修改'}), 400
                        
                        if not new_password:
                            return jsonify({'error': '新密码不能为空'}), 400
                        
                        # 验证密码字符
                        password_valid, password_error = self._validate_alphanumeric(new_password, "新密码")
                        if not password_valid:
                            return jsonify({'error': password_error}), 400
                        
                        elif len(new_password) < 6 or len(new_password) > 100:
                            return jsonify({'error': '新密码长度必须在6-100个字符之间'}), 400
                        
                        # 管理员密码更新
                        success = self.db_manager.update_admin_password(username, new_password)
                        if success:
                            return jsonify({'message': f'管理员 {username} 密码更新成功'})
                        else:
                            return jsonify({'error': '管理员密码更新失败'}), 500
                    else:
                        # 普通用户：用户名和口令可以在同一次保存里一起改
                        # （编辑框就是这样提交的，原来写成 if 用户名 / elif 口令，
                        #  同时提交时口令会被静默丢弃，界面还提示改名成功）
                        if not new_username and not new_password:
                            return jsonify({'error': '请提供要更新的密码或用户名'}), 400

                        users = self.db_manager.get_all_users()
                        user_id = None
                        for user in users:
                            if user[1] == username:
                                user_id = user[0]
                                break

                        if user_id is None:
                            return jsonify({'error': '用户不存在'}), 400

                        if new_username:
                            username_valid, username_error = self._validate_alphanumeric(new_username, "用户名")
                            if not username_valid:
                                return jsonify({'error': username_error}), 400

                            if len(new_username) < 2 or len(new_username) > 50:
                                return jsonify({'error': '用户名长度必须在2-50个字符之间'}), 400

                            existing_users = [u[1] for u in users]
                            if new_username in existing_users and new_username != username:
                                return jsonify({'error': '用户名已存在'}), 400

                            admin_name = str(config.DEFAULT_ADMIN.get('username') or '')
                            if admin_name and new_username.lower() == admin_name.lower():
                                return jsonify({'error': '该用户名已被管理员账号占用'}), 400

                        if new_password:
                            # 与新增用户同一套校验，避免出现"能改出来但建不出来"的口令
                            password_valid, password_error = self._validate_alphanumeric(new_password, "新密码")
                            if not password_valid:
                                return jsonify({'error': password_error}), 400
                            if len(new_password) < 6 or len(new_password) > 100:
                                return jsonify({'error': '新密码长度必须在6-100个字符之间'}), 400

                        # 改名或改口令都必须让旧会话失效
                        forwarder.force_disconnect_user(username)

                        # password=None 表示保留原口令
                        success, message = self.db_manager.update_user(
                            user_id,
                            new_username or username,
                            new_password or None,
                        )
                        if not success:
                            return jsonify({'error': message}), 400

                        parts = []
                        if new_username and new_username != username:
                            parts.append(f'用户名从 {username} 更新为 {new_username}')
                        if new_password:
                            parts.append('口令已更新')
                        return jsonify({'message': '；'.join(parts) if parts else message})
                    
                except Exception as e:
                    log_error(f"更新用户失败: {e}")
                    return jsonify({'error': str(e)}), 500
            
            elif request.method == 'DELETE':
                # 删除用户
                try:
                    # 强制下线用户
                    forwarder.force_disconnect_user(username)
                    success, result = self.db_manager.delete_user(username)
                    if success:
                        return jsonify({'message': f'用户 {result} 删除成功'})
                    else:
                        return jsonify({'error': result}), 400
                    
                except Exception as e:
                    log_error(f"删除用户失败: {e}")
                    return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mounts', methods=['GET', 'POST'])
        @self.require_login
        def api_mounts():
            """挂载点管理API"""
            if request.method == 'GET':
                # 获取挂载点列表
                try:
                    mounts = self.db_manager.get_all_mounts()
                    online_mounts = connection.get_connection_manager().get_online_mounts()
                    
                    # 将tuple转换为字典格式并添加运行状态和连接信息
                    mount_list = []
                    for mount in mounts:
                        mount_name = mount[1]
                        is_online = mount_name in online_mounts
                        # 获取实际的数据率
                        data_rate_str = '0 B/s'
                        if is_online:
                            mount_info = connection.get_connection_manager().get_mount_info(mount_name)
                            if mount_info and 'data_rate' in mount_info:
                                data_rate_bps = mount_info['data_rate']
                                if data_rate_bps >= 1024:
                                    data_rate_str = f'{data_rate_bps/1024:.2f} KB/s'
                                else:
                                    data_rate_str = f'{data_rate_bps:.2f} B/s'
                        
                        mount_dict = {
                            'id': mount[0],
                            'mount': mount_name,
                            # 不回传明文密码：列表接口一旦泄露等于交出全部挂载点口令
                            'password': '******' if mount[2] else '',
                            'password_set': bool(mount[2]),
                            'username': mount[4] if len(mount) > 4 else None,  # 用户名
                            'lat': mount[5] if len(mount) > 5 and mount[5] is not None else 0,
                            'lon': mount[6] if len(mount) > 6 and mount[6] is not None else 0,
                            'active': is_online,
                            'connections': connection.get_connection_manager().get_mount_connection_count(mount_name) if is_online else 0,
                            'data_rate': data_rate_str
                        }
                        mount_list.append(mount_dict)
                    
                    return jsonify(mount_list)
                except Exception as e:
                    log_error(f"获取挂载点列表失败: {e}")
                    return jsonify({'error': str(e)}), 500
            
            elif request.method == 'POST':
                # 添加挂载点
                try:
                    data = request.get_json()
                    if not data:
                        return jsonify({'error': '请求数据格式错误'}), 400
                    
                    mount = data.get('mount', '').strip()
                    password = data.get('password', '').strip()
                    user_id = data.get('user_id')  # 可选的用户ID参数
                    bind_username = data.get('username')  # 可选的用户名绑定（与 PUT 保持一致）
                    if isinstance(bind_username, str):
                        bind_username = bind_username.strip()
                    else:
                        bind_username = None
                    
                    # 表单验证
                    if not mount or not password:
                        return jsonify({'error': '挂载点名称和密码不能为空'}), 400
                    
                    # 验证挂载点名称字符
                    mount_valid, mount_error = self._validate_alphanumeric(mount, "挂载点名称")
                    if not mount_valid:
                        return jsonify({'error': mount_error}), 400
                    
                    # 验证密码字符
                    password_valid, password_error = self._validate_alphanumeric(password, "密码")
                    if not password_valid:
                        return jsonify({'error': password_error}), 400
                    
                    elif len(mount) < 2 or len(mount) > 50:
                        return jsonify({'error': '挂载点名称长度必须在2-50个字符之间'}), 400
                    elif len(password) < 6 or len(password) > 100:
                        return jsonify({'error': '密码长度必须在6-100个字符之间'}), 400
                    
                    # 如果指定了user_id，验证用户是否存在
                    if user_id is not None:
                        try:
                            user_id = int(user_id)
                            users = self.db_manager.get_all_users()
                            user_ids = [u[0] for u in users]  # u[0] 是用户ID
                            if user_id not in user_ids:
                                return jsonify({'error': '指定的用户不存在'}), 400
                        except (ValueError, TypeError):
                            return jsonify({'error': '用户ID格式错误'}), 400
                    # 也允许直接传用户名（写入路径只认 user_id，但前端/脚本两种写法都存在，
                    # 以前传 username 会被静默忽略，建出来的挂载点是"未绑定"状态）
                    elif bind_username:
                        users = self.db_manager.get_all_users()
                        matched = [u for u in users if u[1] == bind_username]
                        if not matched:
                            return jsonify({'error': f'用户 "{bind_username}" 不存在'}), 400
                        user_id = matched[0][0]
                    
                    # 检查挂载点是否已存在
                    existing_mounts = [m[1] for m in self.db_manager.get_all_mounts()]
                    if mount in existing_mounts:
                        return jsonify({'error': '挂载点已存在'}), 400
                    
                    success, message = self.db_manager.add_mount(mount, password, user_id)
                    if success:
                        return jsonify({'message': message}), 201
                    else:
                        return jsonify({'error': message}), 400
                    
                except Exception as e:
                    log_error(f"添加挂载点失败: {e}")
                    return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/mounts/<mount_name>', methods=['PUT', 'DELETE'])
        @self.require_login
        def api_mount_detail(mount_name):
            """挂载点详情管理API"""
            if request.method == 'PUT':
                # 更新挂载点
                try:
                    data = request.get_json()
                    if not data:
                        return jsonify({'error': '请求数据格式错误'}), 400
                    
                    new_password = data.get('password', '').strip()
                    new_mount_name = data.get('mount_name', '').strip()
                    new_user_id = data.get('user_id')
                    username = data.get('username')
                    # 请求体里没有绑定信息时，必须"保持原绑定"，不能把 None 当成解绑
                    binding_provided = ('user_id' in data) or ('username' in data)
                    
                    # 验证新挂载点名称
                    if new_mount_name:
                        # 验证挂载点名称字符
                        mount_valid, mount_error = self._validate_alphanumeric(new_mount_name, "挂载点名称")
                        if not mount_valid:
                            return jsonify({'error': mount_error}), 400
                        
                        if len(new_mount_name) < 2 or len(new_mount_name) > 50:
                            return jsonify({'error': '挂载点名称长度必须在2-50个字符之间'}), 400
                        
                        # 检查新挂载点名称是否已存在
                        existing_mounts = [m[1] for m in self.db_manager.get_all_mounts()]
                        if new_mount_name in existing_mounts and new_mount_name != mount_name:
                            return jsonify({'error': '挂载点名称已存在'}), 400
                    
                    # 处理用户绑定（支持用户名和用户ID两种方式）
                    if username is not None:
                        if username == "" or (isinstance(username, str) and username.lower() == "null"):
                            new_user_id = None  # 空字符串或"null"表示解除绑定
                        else:
                            # 验证用户名字符
                            username_valid, username_error = self._validate_alphanumeric(username, "用户名")
                            if not username_valid:
                                return jsonify({'error': username_error}), 400
                            
                            # 通过用户名查找用户ID
                            users = self.db_manager.get_all_users()
                            user_found = False
                            for user in users:
                                if user[1] == username:  # user[1] 是用户名
                                    new_user_id = user[0]  # user[0] 是用户ID
                                    user_found = True
                                    break
                            if not user_found:
                                return jsonify({'error': f'用户 "{username}" 不存在'}), 400
                    elif new_user_id is not None:
                        # 兼容原有的用户ID方式
                        if new_user_id == "" or (isinstance(new_user_id, str) and new_user_id.lower() == "null"):
                            new_user_id = None  # 空字符串或"null"转换为None
                        elif new_user_id is not None:
                            try:
                                new_user_id = int(new_user_id)
                                # 检查用户是否存在
                                users = self.db_manager.get_all_users()
                                user_exists = any(user[0] == new_user_id for user in users)
                                if not user_exists:
                                    return jsonify({'error': '指定的用户不存在'}), 400
                            except (ValueError, TypeError):
                                return jsonify({'error': '用户ID格式错误'}), 400
                    
                    if new_password:
                        # 验证密码字符
                        password_valid, password_error = self._validate_alphanumeric(new_password, "密码")
                        if not password_valid:
                            return jsonify({'error': password_error}), 400
                        
                        if len(new_password) < 6 or len(new_password) > 100:
                            return jsonify({'error': '新密码长度必须在6-100个字符之间'}), 400
                    
                    # 强制下线挂载点：读当前记录（既用于判断是否真的变了，也用于改名后清理旧在线条目）
                    mounts = self.db_manager.get_all_mounts()
                    current = None
                    for mount in mounts:
                        if mount[1] == mount_name:  # mount[1] 是挂载点名称
                            current = mount
                            break
                    
                    if current is None:
                        return jsonify({'error': '挂载点不存在'}), 400
                    
                    mount_id = current[0]
                    old_user_id = current[3]
                    
                    # 使用update_mount函数更新挂载点信息
                    success, result = self.db_manager.update_mount(
                        mount_id, 
                        new_mount_name if new_mount_name else None,
                        new_password if new_password else None,
                        new_user_id if binding_provided else KEEP_USER_BINDING
                    )
                    if success:
                        # 名称/口令/绑定任何一项变了，都必须让现有连接失效：
                        # 旧口令的源站继续推流等于口令修改没生效，改名后旧名字还会残留在
                        # 在线表与 STR 表里（管理页出现一个注册表中并不存在的挂载点）。
                        renamed = bool(new_mount_name) and new_mount_name != mount_name
                        binding_changed = binding_provided and new_user_id != old_user_id
                        if renamed or binding_changed or new_password:
                            try:
                                cm = connection.get_connection_manager()
                                # 先断开源站（upload 端），再清理下载端与缓冲区
                                cm.remove_mount_connection(
                                    mount_name,
                                    '挂载点已重命名为 %s' % new_mount_name if renamed else '挂载点配置已更新'
                                )
                                forwarder.force_disconnect_mount(mount_name)
                            except Exception as cleanup_error:
                                log_warning(f"更新挂载点后清理在线连接失败: {cleanup_error}")
                        
                        # 构建返回消息
                        messages = []
                        if new_mount_name:
                            messages.append(f'挂载点名称从 {mount_name} 更新为 {new_mount_name}')
                        if new_password:
                            messages.append('挂载点密码已更新')
                        if binding_provided:
                            if new_user_id is None:
                                messages.append('挂载点所属用户已清除')
                            else:
                                if username and username != "":
                                    messages.append(f'挂载点所属用户已更新为 {username}')
                                else:
                                    messages.append(f'挂载点所属用户已更新为用户ID {new_user_id}')
                        
                        if not messages:
                            messages.append('挂载点信息更新成功')
                        
                        return jsonify({'message': '; '.join(messages)})
                    else:
                        return jsonify({'error': result}), 400
                    
                except Exception as e:
                    log_error(f"更新挂载点失败: {e}")
                    return jsonify({'error': str(e)}), 500
            
            elif request.method == 'DELETE':
                # 删除挂载点
                try:
                    # 获取挂载点ID
                    mounts = self.db_manager.get_all_mounts()
                    mount_id = None
                    for mount in mounts:
                        if mount[1] == mount_name:  # mount[1] 是挂载点名称
                            mount_id = mount[0]  # mount[0] 是ID
                            break
                    
                    if mount_id is None:
                        return jsonify({'error': '挂载点不存在'}), 400
                    
                    # 强制下线挂载点
                    forwarder.force_disconnect_mount(mount_name)
                    success, result = self.db_manager.delete_mount(mount_name)
                    if success:
                        # 清理挂载点连接数据
                        connection.get_connection_manager().remove_mount_connection(mount_name)
                        return jsonify({'message': f'挂载点 {result} 删除成功'})
                    else:
                        return jsonify({'error': result}), 400
                    
                except Exception as e:
                    log_error(f"删除挂载点失败: {e}")
                    return jsonify({'error': str(e)}), 500

        

        

        
        @self.app.route('/api/mount/<mount_name>/online')
        @self.require_login
        def api_mount_online_status(mount_name):
            """检查挂载点是否在线"""
            try:
                is_online = connection.is_mount_online(mount_name)
                mount_info = None
                if is_online:
                    mount_info = connection.get_connection_manager().get_mount_info(mount_name)
                
                return jsonify({
                    'mount_name': mount_name,
                    'online': is_online,
                    'mount_info': mount_info
                })
            except Exception as e:
                log_error(f"检查挂载点在线状态失败: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/system/stats')
        @self.require_login
        def api_system_stats():
            """获取系统统计数据"""
            try:
                # 获取服务器实例
                server = get_server_instance()
                if server and hasattr(server, 'get_system_stats'):
                    stats = server.get_system_stats()
                
                    return jsonify(stats)
                else:
                    log_error("API错误: 无法获取服务器实例或get_system_stats方法")
                    return jsonify({'error': '无法获取系统统计数据'}), 500
            except Exception as e:
                log_error(f"API异常: 获取系统统计数据失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/public/system/stats')
        def api_public_system_stats():
            """匿名版系统统计：只给聚合值

            数据页对所有人开放，但用户明细（账号名 + 连接 IP）和源站 IP 属于
            内部信息，必须裁掉——「公开数据」不等于「公开用户」。
            """
            try:
                server = get_server_instance()
                if server and hasattr(server, 'get_system_stats'):
                    return jsonify(self._public_stats(server.get_system_stats()))
                return jsonify({'error': '无法获取系统统计数据'}), 500
            except Exception as e:
                log_error(f"匿名系统统计失败: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/api/str-table', methods=['GET'])
        @self.require_login
        def api_str_table():
            """获取实时STR表数据"""
            try:
                # 获取所有在线挂载点的STR数据
                cm = connection.get_connection_manager()
                str_data = cm.get_all_str_data()
                
                # 生成完整的挂载点列表（包括STR表）
                mount_list = cm.generate_mount_list()
                
                return jsonify({
                    'success': True,
                    'str_data': str_data,
                    'mount_list': mount_list,
                    'timestamp': time.time()
                })
            except Exception as e:
                log_error(f"获取STR表失败: {e}")
                return jsonify({
                    'success': False,
                    'error': str(e)
                }), 500
        
        @self.app.route('/api/mounts/online', methods=['GET'])
        @self.require_login
        def api_online_mounts_detailed():
            """获取详细的在线挂载点信息"""
            try:
                cm = connection.get_connection_manager()
                online_mounts = cm.get_online_mounts()
                
                # 为每个挂载点添加详细信息
                detailed_mounts = {}
                for mount_name, mount_info in online_mounts.items():
                    detailed_mounts[mount_name] = {
                        'basic_info': mount_info,
                        'str_data': cm.get_mount_str_data(mount_name),
                        'statistics': cm.get_mount_statistics(mount_name),
                        'connection_count': cm.get_mount_connection_count(mount_name)
                    }
                
                return jsonify({
                    'success': True,
                    'online_mounts': detailed_mounts,
                    'total_count': len(detailed_mounts),
                    'timestamp': time.time()
                })
            except Exception as e:
                log_error(f"获取在线挂载点详细信息失败: {e}")
                return jsonify({
                    'success': False,
                    'error': str(e)
                }), 500
        
        @self.app.route('/api/mount/<mount_name>/rtcm-parse/history', methods=['GET'])
        @self.require_login
        def api_get_rtcm_history(mount_name):
            """获取指定挂载点的历史解析数据"""
            try:
                # Web 解析线程现在挂在订阅注册表里（按挂载点复用），
                # 不再登记到 RTCM2ParserManager，所以先问注册表再回退旧接口。
                parsed_data = self._get_parsed_mount_data(mount_name)
                if parsed_data:
                    return jsonify({
                        'success': True,
                        'data': parsed_data
                    })
                else:
                    return jsonify({
                        'success': False,
                        'error': 'No data available for this mount point'
                    }), 404
            except Exception as e:
                log_error(f"获取挂载点{mount_name}历史数据失败: {e}")
                return jsonify({'error': str(e)}), 500

    
    def _ensure_forwarder_started(self):
        """确保forwarder已启动（已在main.py中启动，此方法保留用于兼容性）"""
        # forwarder已经在main.py中启动，这里不需要重复启动
        pass
    
    def _register_socketio_events(self):
        """注册SocketIO事件"""
        
        @self.socketio.on('connect')
        def handle_connect(auth=None):
            """客户端连接

            改造后不再强制登录：数据页（STR 表、在线挂载点、实时解析）要对所有人开放，
            匿名访客必须能收推送。敏感内容（用户列表、系统日志、含 IP 的统计）
            仍然只发给已登录的 data_push 房间。
            """
            authed = bool(session.get('admin_logged_in'))
            if not authed and not config.PUBLIC_ACCESS:
                # 默认（公开访问关闭）仍要求先登录，与旧版行为一致
                log_warning(f"拒绝未登录的 WebSocket 连接: {request.remote_addr}")
                return False
            room = 'data_push' if authed else 'data_public'
            join_room(room)
            log_web_request('websocket', 'connect',
                            (request.sid or 'unknown')[:12],
                            'WebSocket客户端连接（%s）' % ('管理员' if authed else '匿名'))
            emit('status', {
                'message': '连接成功',
                'authenticated': authed,
                'demo_mode': bool(config.DEMO_MODE),
            })

        @self.socketio.on('disconnect')
        def handle_disconnect():
            """客户端断开连接

            改造前这里会调用 stop_realtime_parsing()，等于「一个人关标签页，
            所有人的解析都停掉」。现在只清理该 socket 自己的房间记录：
            订阅关系由心跳超时（web_parser_registry 巡检）回收，
            短暂重连窗口内的解析不会被打断。
            """
            sid = request.sid
            with self._sid_room_lock:
                mount = self._sid_rooms.pop(sid, None)
            log_web_request('websocket', 'disconnect', (sid or 'unknown')[:12],
                            'WebSocket客户端断开' + (f'（挂载点 {mount}）' if mount else ''))

        @self.socketio.on('rtcm_subscribe')
        def handle_rtcm_subscribe(data):
            """订阅某挂载点的解析数据（加入 Socket.IO 房间）

            房间在握手连接上是「免费」的：join 之后服务端按房间 emit，
            同一个站的访客收到同一份数据，不同站互相看不见。
            """
            payload = data or {}
            mount = (payload.get('mount_name') or payload.get('mount') or '').strip()
            if not mount:
                emit('error', {'message': '缺少挂载点名称'})
                return
            err = self._validate_mount_param(mount)
            if err:
                emit('error', {'message': err})
                return
            self._bind_socket_room(mount)
            emit('rtcm_subscribed', {'mount_name': mount})

        @self.socketio.on('rtcm_unsubscribe')
        def handle_rtcm_unsubscribe(data=None):
            """离开挂载点房间（不传参数表示全部离开）"""
            payload = data or {}
            mount = (payload.get('mount_name') or payload.get('mount') or '').strip()
            with self._sid_room_lock:
                current = self._sid_rooms.get(request.sid)
            # 指定了挂载点但不是我当前所在房间：忽略，避免把新订阅误退掉
            if mount and current and mount != current:
                emit('rtcm_unsubscribed', {'mount_name': mount, 'ignored': True})
                return
            self._bind_socket_room(None)
            emit('rtcm_unsubscribed', {'mount_name': mount or current})

        @self.socketio.on('request_mount_data')
        def handle_request_mount_data(data):
            """请求挂载点数据"""
            mount = (data or {}).get('mount')
            if mount:
                parsed_data = self._get_parsed_mount_data(mount)
                statistics = rtcm_manager.get_mount_statistics(mount)
                emit('mount_data', {
                    'mount': mount,
                    'data': parsed_data,
                    'statistics': statistics
                })

        @self.socketio.on('request_recent_data')
        def handle_request_recent_data(data):
            """前端请求挂载点最近解析的数据"""
            mount_name = (data or {}).get('mount_name')
            if mount_name:
                recent_data = self._get_parsed_mount_data(mount_name)
                emit('recent_data_response', {
                    'mount_name': mount_name,
                    'data': recent_data
                })

        @self.socketio.on('request_system_stats')
        def handle_request_system_stats():
            """请求系统统计数据"""
            try:
                server = get_server_instance()
                if server and hasattr(server, 'get_system_stats'):
                    stats = server.get_system_stats()
                    if stats:
                        if not session.get('admin_logged_in'):
                            stats = self._public_stats(stats)
                        emit('system_stats_update', {
                            'stats': stats,
                            'timestamp': time.time()
                        })
                    else:
                        emit('error', {'message': '无法获取系统统计数据'})
                else:
                    emit('error', {'message': '服务器实例不可用'})
            except Exception as e:
                log_error(f"处理系统统计数据请求失败: {e}")
                emit('error', {'message': str(e)})
    
    def require_login(self, f):
        """登录装饰器"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('admin_logged_in'):
                # 检查是否是API请求
                if request.path.startswith('/api/'):
                    return jsonify({'error': '未登录或登录已过期'}), 401
                else:
                    return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    
    def start_rtcm_parsing(self):
        """启动RTCM解析进程，持续解析数据并推送到前端"""
        # 现在RTCM解析集成在connection_manager中，无需单独启动
        
        # 启动实时数据推送
        if not self.push_running:
            self.push_running = True
            self.push_thread = Thread(target=self._push_data_loop, daemon=True)
            self.push_thread.start()
            log_system_event('Web实时数据推送已启动')
    
    def stop_rtcm_parsing(self):
        """停止RTCM解析"""
        # 现在RTCM解析集成在connection_manager中，无需单独停止
        
        # 停止实时数据推送
        if self.push_running:
            self.push_running = False
            if self.push_thread:
                self.push_thread.join(timeout=5)
            log_system_event('Web实时数据推送已停止')
    
    def _push_data_loop(self):
        """实时数据推送循环

        原实现每 3 秒无差别推一遍四份数据：
        - 挂载点/用户上下线最多要等 3 秒才反映到界面（"断线了但页面还显示在线"）；
        - 统计里的运行时长/连接数取自 10 秒缓存，跳变更明显。
        现在改成两档：
        1) 每 0.5 秒比对一次"拓扑签名"（挂载点名集合 + 用户连接数），一有变化立刻推；
        2) 每 REALTIME_PUSH_INTERVAL 秒仍然全量推一次（统计、STR 表、心跳）。
        """
        log_info("数据推送循环已启动")
        tick = 0.5
        last_users_sig = None
        last_mounts_sig = None
        last_full_push = 0.0

        while self.push_running:
            try:
                cm = connection.get_connection_manager()
                now = time.time()

                # 先比签名（只读名字，不深拷贝），有变化才去取完整快照
                mounts_sig, users_sig = cm.get_online_signature()
                changed = (users_sig != last_users_sig) or (mounts_sig != last_mounts_sig)
                full_due = (now - last_full_push) >= config.REALTIME_PUSH_INTERVAL

                if changed or full_due:
                    # 用户明细只给登录后台的人；挂载点列表数据页也要用，两个房间都发
                    self.socketio.emit('online_users_update', {
                        'users': cm.get_online_users(),
                        'timestamp': now
                    }, to='data_push')
                    mounts_payload = {
                        'mounts': cm.get_online_mounts(),
                        'timestamp': now
                    }
                    self.socketio.emit('online_mounts_update', mounts_payload, to='data_push')
                    self.socketio.emit('online_mounts_update', mounts_payload, to='data_public')
                    last_users_sig = users_sig
                    last_mounts_sig = mounts_sig

                if full_due:
                    last_full_push = now

                    server = get_server_instance()
                    stats = server.get_system_stats() if server and hasattr(server, 'get_system_stats') else None
                    if stats:
                        self.socketio.emit('system_stats_update', {
                            'stats': stats,
                            'timestamp': now
                        }, to='data_push')
                        # 匿名房间只给聚合值：不含用户明细与源站 IP
                        self.socketio.emit('system_stats_update', {
                            'stats': self._public_stats(stats),
                            'timestamp': now
                        }, to='data_public')

                    # STR 表就是「大家都能看的实时数据」，两个房间都推
                    str_data = cm.get_all_str_data()
                    str_payload = {
                        'str_data': str_data,
                        'timestamp': now
                    }
                    self.socketio.emit('str_data_update', str_payload, to='data_push')
                    self.socketio.emit('str_data_update', str_payload, to='data_public')

                time.sleep(tick)
            except Exception as e:
                log_error(f"数据推送异常: {e}", exc_info=True)
                time.sleep(1)
    
    def push_log_message(self, message, log_type='info'):
        """推送日志消息到前端"""
        try:
            self.socketio.emit('log_message', {
                'message': message,
                'type': log_type,
                'timestamp': time.time()
            }, to='data_push')
        except Exception as e:
            log_error(f"推送日志消息失败: {e}")
    
    def _format_uptime(self, uptime_seconds):
        """格式化运行时间"""
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        seconds = int(uptime_seconds % 60)
        
        if days > 0:
            return f"{days}天 {hours}小时 {minutes}分钟"
        elif hours > 0:
            return f"{hours}小时 {minutes}分钟"
        else:
            return f"{minutes}分钟 {seconds}秒"
    

    
    def run(self, host=None, port=None, debug=None):
        """启动Web服务器"""
        host = host or config.HOST
        port = port or config.WEB_PORT
        debug = debug if debug is not None else config.DEBUG
        self.socketio.run(self.app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)
    

# 便捷函数
def create_web_manager(db_manager, data_forwarder, start_time):
    """创建Web管理器实例"""
    return WebManager(db_manager, data_forwarder, start_time)