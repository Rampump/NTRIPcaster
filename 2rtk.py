#!/usr/bin/env python3
import socketserver
import base64
import time
import sqlite3
import logging
from threading import Lock, Timer
from collections import deque
import socket
import os
import sys
import datetime
import signal
from flask import Flask, request, render_template, redirect, url_for, session, send_from_directory
import psutil

DEBUG = True

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.WARNING,
    format='[%(asctime)s] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('2RTK')

START_TIME = time.time()
def dbg(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)

VERSION = '1.9.8'
CONTACT_EMAIL = 'i@jia.by'
HOST = '0.0.0.0'
NTRIP_PORT = 2101
WEB_PORT = 5757

BUFFER_MAXLEN = 2000
rtcm_lock = Lock()
rtcm_buffers = {}

clients_lock = Lock()
authenticated_clients = []

BANNER = r"""
    ██████╗ ██████╗ ████████╗██╗  ██╗
    ╚════██╗██╔══██╗╚══██╔══╝██║ ██╔╝
     █████╔╝██████╔╝   ██║   █████╔╝
    ██╔═══╝ ██╔══██╗   ██║   ██  ██
    ███████╗██║  ██║   ██║   ██╗  ██╗
    ╚══════╝╚═╝  ╔═╝   ╚═╝   ╚═╝  ╚═╝
         2RTK Ntrip Caster/{}
""".format(VERSION)

def clear_banner():
    os.system('cls' if os.name == 'nt' else 'clear')
    print(BANNER)
    with clients_lock:
        connected_user_count = len(authenticated_clients)
    print(f"已连接的用户数: {connected_user_count}")

    conn = sqlite3.connect('2rtk.db')
    c = conn.cursor()
    c.execute("SELECT mount FROM running_mounts")
    online_mounts = [row[0] for row in c.fetchall()]
    conn.close()

    print(f"运行中挂载点: {', '.join(online_mounts)}")

    t = Timer(100, clear_banner)
    t.daemon = True
    t.start()

# ===================================
# 数据库初始化和管理
# ===================================
def init_db():
    conn = sqlite3.connect('2rtk.db')
    c = conn.cursor()

    c.execute('''
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL
    )
    ''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL
    )
    ''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS mounts (
        id INTEGER PRIMARY KEY,
        mount TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL
    )
    ''')
    c.execute('''
    CREATE TABLE IF NOT EXISTS running_mounts (
        id INTEGER PRIMARY KEY,
        mount TEXT NOT NULL,
        start_time INTEGER NOT NULL,
        identifier TEXT,
        format TEXT,
        format_details TEXT,
        carrier INTEGER DEFAULT 0,
        nav_system TEXT,
        network TEXT,
        country TEXT,
        latitude REAL,
        longitude REAL,
        nmea INTEGER DEFAULT 0,
        solution INTEGER DEFAULT 0,
        generator TEXT,
        compression TEXT,
        authentication TEXT,
        fee TEXT DEFAULT 'N',
        bitrate INTEGER,
        misc TEXT,
        update_time INTEGER,
        UNIQUE (mount),
        FOREIGN KEY (mount) REFERENCES mounts(mount)
            ON DELETE CASCADE
            ON UPDATE CASCADE
    )
    ''')
    c.execute("DELETE FROM running_mounts")
    conn.commit()
    print("运行中的挂载点列表已清空")
    c.execute("SELECT * FROM admins")
    if not c.fetchone():
        c.execute("INSERT INTO admins (username, password) VALUES ('admin', 'admin')")
        print("已创建默认管理员: admin/admin")
        print(" 请务必在首次登录后修改默认密码！")
    conn.commit()
    conn.close()
    print("数据库初始化完成")

# ===================================
# NTRIP服务器处理类
# ===================================
class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        dbg(f"连接: {self.client_address}")
        try:
            raw = self.request.recv(1024).decode(errors='ignore')
        except Exception as e:
            logger.error(f"Reception failed: {e}")
            return
        if not raw:
            return
        req, hdrs = self._parse(raw)
        protocol_version = self.get_protocol_version(req, hdrs)
        
        if req.startswith('SOURCE'):
            self._source(req, hdrs, protocol_version)
        elif req.startswith('GET'):
            self._get(req, hdrs, protocol_version)
        else:
            logger.warning(f"Unknown request: {req}")
            # 返回标准400错误响应
            if protocol_version == 'NTRIP v2.0':
                self.request.sendall(b'HTTP/1.1 400 Bad Request\r\n\r\n')
            else:
                self.request.sendall(b'ICY 400 Bad Request\r\n')

    def get_client_model(self, agent):
        if agent and 'NTRIP' in agent:
            return agent.split('NTRIP', 1)[1].strip()
        return 'N/A'

    def get_protocol_version(self, req, hdrs):
        """修复协议版本判断，结合请求行和Ntrip-Version头"""
        parts = req.split()
        if len(parts) >= 3:
            protocol = parts[-1]
            # NTRIP v2.0必须同时满足HTTP/1.1和Ntrip-Version头
            if protocol == 'HTTP/1.1' and hdrs.get('Ntrip-Version') == 'Ntrip/2.0':
                return 'NTRIP v2.0'
            # NTRIP v1.0使用HTTP/1.0或没有Ntrip-Version头的HTTP/1.1
            elif protocol == 'HTTP/1.0' or (protocol == 'HTTP/1.1' and 'Ntrip-Version' not in hdrs):
                return 'NTRIP v1.0'
        return 'N/A'

    def _parse(self, raw: str):
        lines = raw.splitlines()
        req = lines[0] if lines else ''
        h = {}
        for l in lines[1:]:
            if ': ' in l:
                k, v = l.split(': ', 1)
                h[k.strip()] = v.strip()
        return req, h

    def _send_error_response(self, protocol_version, status_code):
        """根据协议版本发送对应的错误响应"""
        if protocol_version == 'NTRIP v2.0':
            responses = {
                400: b'HTTP/1.1 400 Bad Request\r\n\r\n',
                401: b'HTTP/1.1 401 Unauthorized\r\n\r\n',
                404: b'HTTP/1.1 404 Not Found\r\n\r\n',
                500: b'HTTP/1.1 500 Internal Server Error\r\n\r\n'
            }
        else:  # NTRIP v1.0 或未知版本
            responses = {
                400: b'ICY 400 Bad Request\r\n',
                401: b'ICY 401 Unauthorized\r\n',
                404: b'ICY 404 Not Found\r\n',
                500: b'ICY 500 Internal Server Error\r\n'
            }
        # 发送对应状态码的响应，如果不存在则使用500
        self.request.sendall(responses.get(status_code, responses[500]))

    def _source(self, req: str, hdrs: dict, protocol_version):
        """支持标准和非标准SOURCE请求格式的处理逻辑"""
        dbg("SOURCE upload request verification")
        p = req.strip().split()

        # 初始化
        mount = None
        password = None
        auth_mode = None  # 'standard', 'legacy'

        # ===== 检测请求格式 =====
        if len(p) >= 3:
            # 标准格式：SOURCE /mount HTTP/1.0 or HTTP/1.1
            if p[0].upper() == 'SOURCE' and p[1].startswith('/') and p[2].startswith('HTTP/'):
                auth_mode = 'standard'
                mount = p[1].lstrip('/')
            # RTKLIB / 非标准简化模式：SOURCE <password> <mount>
            elif p[0].upper() == 'SOURCE' and not p[2].startswith('HTTP/'):
                auth_mode = 'legacy'
                password = p[1]
                mount = p[2]

        if not auth_mode or not mount or (not password and auth_mode != 'standard'):
            self._send_error_response(protocol_version, 400)
            logger.warning(f"无法解析上传请求格式: {req}")
            return

        # ===== 标准认证模式（Authorization: Basic） =====
        if auth_mode == 'standard':
            auth_header = hdrs.get('Authorization')
            if not auth_header or not auth_header.startswith('Basic '):
                self._challenge(protocol_version)
                return
            try:
                b64data = auth_header.split()[1]
                username, password = base64.b64decode(b64data).decode().split(':', 1)
            except Exception as e:
                self._send_auth_failure(protocol_version)
                logger.warning(f"Base64解析失败: {e}")
                return

        # ===== 验证挂载点密码 =====
        conn = sqlite3.connect('2rtk.db')
        c = conn.cursor()
        c.execute("SELECT password FROM mounts WHERE mount = ?", (mount,))
        result = c.fetchone()
        if not result or result[0] != password:
            conn.close()
            self._send_auth_failure(protocol_version)
            dbg(f"上传认证失败，密码错误: mount={mount}, addr={self.client_address}")
            return

        # ===== 成功响应 =====
        if protocol_version == 'NTRIP v2.0':
            self.request.sendall(b'HTTP/1.1 200 OK\r\nNtrip-Version: Ntrip/2.0\r\n\r\n')
        else:
            self.request.sendall(b'ICY 200 OK\r\n')

        agent = self.get_client_model(hdrs.get('Source-Agent', ''))
        logger.info(f"上传认证成功: mount={mount}, agent={agent}, addr={self.client_address}, 模式={auth_mode}")
        print(f"⟳ {mount} 上传认证成功 来自{self.client_address}, 基准站:{agent}, 模式={auth_mode}")

        # ===== 运行数据库记录 =====
        current_time = time.time()
        try:
            c.execute('''INSERT OR REPLACE INTO running_mounts (
                mount, start_time, identifier, format, format_details,
                carrier, nav_system, network, country, latitude,
                longitude, nmea, solution, generator, compression,
                authentication, fee, bitrate, misc, update_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
                mount,
                current_time,
                'none',
                'RTCM 3.3',
                '1005(10)',
                0,
                'GPS',
                '2RTK',
                'CHN',
                00.000,
                00.000,
                0,
                0,
                agent,
                'none',
                'B',
                'N',
                500,
                'NO',
                current_time
            ))
            conn.commit()
        except Exception as e:
            logger.error(f"挂载点运行数据库插入失败: {e}")
        conn.close()

        self._generate_mount_list()
        print(f"⟳ {mount} 运行中挂载点已更新")

        with rtcm_lock:
            if mount not in rtcm_buffers:
                rtcm_buffers[mount] = deque(maxlen=BUFFER_MAXLEN)

        # ===== 接收数据并广播 =====
        while True:
            try:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                ts = time.time()
                with rtcm_lock:
                    rtcm_buffers[mount].append((ts, mount, chunk))
                self._broadcast(mount)
            except Exception as e:
                logger.warning(f"基站上传断开: {e}")
                break

        # ===== 断开清理 =====
        conn = sqlite3.connect('2rtk.db')
        c = conn.cursor()
        c.execute("DELETE FROM running_mounts WHERE mount = ?", (mount,))
        conn.commit()
        conn.close()

        with rtcm_lock:
            if mount in rtcm_buffers:
                del rtcm_buffers[mount]

        logger.info(f"上传连接断开: {mount}, addr={self.client_address}")
        self._generate_mount_list()

    def _generate_mount_list(self):
        conn = sqlite3.connect('2rtk.db')
        c = conn.cursor()
        try:
            c.execute("SELECT mount, identifier, format, format_details, carrier, nav_system, network, country, latitude, longitude, nmea, solution, generator, compression, authentication, fee, bitrate, misc FROM running_mounts")
            rows = c.fetchall()
            mount_list = []
            for row in rows:
                mount_info = ';'.join([str(item) if item is not None else '' for item in ['STR'] + list(row)])
                mount_list.append(mount_info)
            mount_list.append(f'NET;2RTK;2RTK;N;N;2rtk.com;{HOST}:{NTRIP_PORT};{CONTACT_EMAIL};;')
            mount_list.append('ENDSOURCETABLE;')
            tbl = '\n'.join(mount_list)
            print("生成挂载点列表成功")
            logger.info("生成挂载点列表成功")
            with open('mount_list.txt', 'w') as f:
                f.write(tbl)
            print("挂载点列表已更新 mount_list.txt")
        except Exception as e:
            logger.error(f"生成挂载点列表失败: {e}")
        finally:
            conn.close()

    def _get(self, req: str, hdrs: dict, protocol_version):
        dbg("用户端请求验证中")
        parts = req.split()
        if len(parts) < 3:
            return
        path = parts[1]
        mount = path.lstrip('/')
        if path == '/':
            logger.info(f"请求挂载点列表: {self.client_address}")
            agent = self.get_client_model(hdrs.get('User-Agent', ''))
            print(f"请求挂载点列表，客户端: {agent}, 来自：{self.client_address}")
            print(f"已向{self.client_address}, {agent}发送挂载点列表")
            return self._send_list()
        
        agent = self.get_client_model(hdrs.get('User-Agent', ''))
        logger.info(f"请求挂载点 {mount} 下载认证，客户端: {agent}, 来自：{self.client_address}, 协议版本: {protocol_version}")
        print(f"请求挂载点 {mount} 下载认证，客户端: {agent}, 来自：{self.client_address}, 协议版本: {protocol_version}")
        
        conn = sqlite3.connect('2rtk.db')
        c = conn.cursor()
        c.execute("SELECT mount FROM mounts WHERE mount =?", (mount,))
        if not c.fetchone():
            if protocol_version == 'NTRIP v2.0':
                self.request.sendall(b'HTTP/1.1 404 Not Found\r\n\r\n')
            else:
                self.request.sendall(b'ICY 404 Not Found\r\n')
            conn.close()
            return
        
        auth = hdrs.get('Authorization')
        if not auth:
            conn.close()
            return self._challenge(protocol_version)
        
        try:
            m, cred = auth.split(' ', 1)
            if m.upper() != 'BASIC':
                raise
            u, pw = base64.b64decode(cred).decode().split(':', 1)
            c.execute("SELECT password FROM users WHERE username =?", (u,))
            result = c.fetchone()
            if not result or result[0] != pw:
                raise
        except:
            self._send_auth_failure(protocol_version)
            conn.close()
            return
        
        now = time.time()
        with clients_lock:
            # 检查是否已有相同连接
            for client in authenticated_clients:
                if client['user'] == u and client['mount'] == mount and client['agent'] == agent:
                    client['last_refresh'] = now
                    conn.close()
                    return
            
            # 限制同用户同挂载点的连接数
            sess = [client for client in authenticated_clients if client['user'] == u and client['mount'] == mount]
            if len(sess) >= 3:
                old = min(sess, key=lambda x: x['auth_time'])
                authenticated_clients.remove(old)
            
            # 添加新客户端
            client = {
                'socket': self.request, 
                'user': u, 
                'mount': mount, 
                'agent': agent, 
                'addr': self.client_address, 
                'auth_time': now - 5, 
                'last_refresh': now,
                'protocol_version': protocol_version  
            }
            authenticated_clients.append(client)
        
        logger.info(f"下载认证通过，用户: {u}来自{self.client_address}使用客户端: {agent}, 协议 {protocol_version}至挂载点 {mount}")
        print(f"下载认证通过，用户: {u}来自{self.client_address}使用客户端: {agent}, 协议 {protocol_version}挂载点 {mount}")
        
        # 发送响应头
        ver, date = parts[2], datetime.datetime.now(datetime.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        if protocol_version == 'NTRIP v2.0':
            hdr = '\r\n'.join([
                'HTTP/1.1 200 OK', 
                'Ntrip-Version: Ntrip/2.0', 
                f'Server: 2RTK Caster/{VERSION}',
                f'Date: {date}', 
                'Cache-Control: no-store,max-age=0', 
                'Pragma: no-cache',
                'Connection: close', 
                'Content-Type: gnss/data', 
                'Transfer-Encoding: chunked', 
                ''
            ])
        else:
            hdr = 'ICY 200 OK\r\n'
        self.request.sendall(hdr.encode())
        print(f"开始从 {mount} 向{self.client_address}{agent}用户: {u}发送数据流...")

        
        try:
            self.request.setblocking(0)  
            while True:
                
                time.sleep(0.01)
        except Exception as e:
            logger.debug(f"客户端连接中断: {e}")
        finally:
            with clients_lock:
                if client in authenticated_clients:
                    authenticated_clients.remove(client)
            conn.close()
            logger.info(f"下载连接关闭: {self.client_address}用户: {u}已从挂载点 {mount} 断开连接")
            print(f"下载连接关闭: {self.client_address}用户: {u}已从挂载点 {mount} 断开连接")

    def _send_list(self):
        try:
            with open('mount_list.txt', 'r') as f:
                tbl = f.read()
        except FileNotFoundError:
            tbl = ''
        date = datetime.datetime.now(datetime.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        resp = '\r\n'.join([
            'SOURCETABLE 200 OK', f'Server: 2RTK Caster/{VERSION}', f'Date: {date}',
            'Content-Type: text/plain', f'Content-Length: {len(tbl)}', 'Connection: close', '', '', tbl
        ])
        self.request.sendall(resp.encode())

    def _challenge(self, protocol_version):
        """根据协议版本发送对应的认证挑战"""
        if protocol_version == 'NTRIP v2.0':
            self.request.sendall(b'HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm="NTRIP"\r\nContent-Length: 0\r\n\r\n')
        else:
            self.request.sendall(b'ICY 401 Unauthorized\r\nWWW-Authenticate: Basic realm="NTRIP"\r\n\r\n')

    def _send_auth_failure(self, protocol_version):
        """根据协议版本发送对应的认证失败响应"""
        if protocol_version == 'NTRIP v2.0':
            self.request.sendall(b'HTTP/1.1 401 Unauthorized\r\n\r\n')
        else:
            self.request.sendall(b'ICY 401 Unauthorized\r\n')

    def _broadcast(self, mount):
        """修复数据传输模式，对v2.0使用分块编码"""
        with rtcm_lock:
            if mount in rtcm_buffers:
                data = [(ts, chunk) for ts, mp, chunk in rtcm_buffers[mount] if mp == mount]
            else:
                data = []
        
        to_remove = []
        with clients_lock:
            clients = list(authenticated_clients)
        
        for c in clients:
            if c['mount'] != mount:
                continue
            
            for ts, chunk in data:
                if ts <= c['auth_time']:
                    continue
                
                try:
                    # 对NTRIP v2.0使用分块编码
                    if c['protocol_version'] == 'NTRIP v2.0':
                        # 分块格式: 十六进制长度\r\n数据\r\n
                        chunk_data = f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n"
                        c['socket'].sendall(chunk_data)
                    else:
                        c['socket'].sendall(chunk)
                except Exception as e:
                    logger.warning(f"Communication closed, removing connection: addr={c['addr']}, err={e}")
                    to_remove.append(c)
                    break
        
        if to_remove:
            with clients_lock:
                for c in to_remove:
                    if c in authenticated_clients:
                        authenticated_clients.remove(c)

# ===================================
# 服务器关闭处理
# ===================================
def shutdown(sig, frame):
    print("\n正在关闭caster服务器…")
    
    # 1. 首先停止接受新的连接
    try:
        server.shutdown()
        print("已停止接受新的连接")
    except Exception as e:
        logger.error(f"停止接受新连接时出错: err={e}")

    # 2. 关闭所有客户端连接
    global authenticated_clients
    with clients_lock:
        for client in authenticated_clients:
            try:
                client['socket'].settimeout(5)
                client['socket'].shutdown(socket.SHUT_RDWR)
                client['socket'].close()
            except Exception as e:
                logger.warning(f"关闭客户端套接字时出错: addr={client['addr']}, err={e}")
        authenticated_clients = []
    print("已关闭所有客户端连接")

    # 3. 清理数据库
    try:
        conn = sqlite3.connect('2rtk.db')
        c = conn.cursor()
        c.execute("DELETE FROM running_mounts")
        conn.commit()
        conn.close()
        print("已清空运行中的挂载点数据")
    except Exception as e:
        logger.error(f"清空运行中挂载点数据时出错: {e}")

    # 4. 完全关闭服务器
    try:
        server.server_close()
        print("NTRIP服务器已完全关闭")
    except Exception as e:
        logger.error(f"关闭NTRIP服务器时出错: err={e}")
    try:
        func = request.environ.get('werkzeug.server.shutdown')
        if func is not None:
            func()
            print("已关闭Web服务器")
    except Exception as e:
        logger.error(f"关闭Web服务器时出错: {e}")
    
    print("服务器关闭完成，正在退出...")
    sys.exit(0)

# ===================================
# Flask Web应用
# ===================================
app = Flask(__name__)
app.secret_key = '9#*&K47g@U2xR6!8pX3m$yQ0%z5L1cV7bW9dF3hJ6n2r'

ALIPAY_QR_URL = 'https://2rtk.rampump.cn/alipay.jpg'
WECHAT_QR_URL = 'https://2rtk.rampump.cn/wechat.jpg'

def format_time_duration(seconds):
    days = int(seconds // 86400)
    seconds %= 86400
    hours = int(seconds // 3600)
    seconds %= 3600
    minutes = int(seconds // 60)
    secs = int(seconds % 60)

    parts = []
    if days > 0:
        parts.append(f"{days} 天")
    if hours > 0 or parts:
        parts.append(f"{hours} 小时")
    if minutes > 0 or parts:
        parts.append(f"{minutes} 分钟")
    parts.append(f"{secs} 秒")

    return " ".join(parts)

def require_login(func):
    def wrapper(*args, **kwargs):
        if 'admin' not in session:
            next_url = request.url
            return redirect(url_for('login', next=next_url))
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

@app.route('/alipay_qr')
def get_alipay_qr():
    return redirect(ALIPAY_QR_URL)

@app.route('/wechat_qr')
def get_wechat_qr():
    return redirect(WECHAT_QR_URL)

@app.route('/static/<path:filename>')
def serve_static(filename):
    cache_timeout = 2592000
    
    if filename == 'favicon.ico':
        return send_from_directory(
            os.path.join(app.root_path, 'static'),
            filename,
            mimetype='image/vnd.microsoft.icon',
            cache_timeout=cache_timeout
        )
    
    return send_from_directory(
        os.path.join(app.root_path, 'static'),
        filename,
        cache_timeout=cache_timeout
    )

@app.route('/')
def index():
    cpu_percent = psutil.cpu_percent(interval=1)
    mem_percent = psutil.virtual_memory().percent
    start_datetime = datetime.datetime.fromtimestamp(START_TIME)
    formatted_start_time = start_datetime.strftime('%Y-%m-%d %H:%M:%S')
    now = datetime.datetime.now()

    running_mounts = []
    with sqlite3.connect('2rtk.db') as conn:
        c = conn.cursor()
        c.execute("SELECT mount, start_time FROM running_mounts")
        for row in c.fetchall():
            mount = row[0]
            start_time = datetime.datetime.fromtimestamp(row[1])
            running_mounts.append((mount, start_time))

    running_users = []
    with clients_lock:
        for client in authenticated_clients:
            username = client['user']
            mount = client['mount']
            agent = client['agent']
            ip = client['addr'][0]
            port = client['addr'][1]
            auth_time = datetime.datetime.fromtimestamp(client['auth_time'])
            running_users.append((username, mount, agent, ip, port, auth_time ))

    return render_template('index.html',
                           cpu_percent=cpu_percent,
                           mem_percent=mem_percent,
                           program_runtime=formatted_start_time,
                           running_mounts=running_mounts,
                           running_users=running_users,
                           now=now,
                           VERSION=VERSION,
                           CONTACT_EMAIL=CONTACT_EMAIL,
                           NTRIP_PORT=NTRIP_PORT)

@app.route('/login', methods=['GET', 'POST'])
def login():
    logout = request.args.get('logout')
    if logout:
        session.pop('admin', None)
        return redirect(url_for('index'))

    next_url = request.args.get('next') or request.referrer
    if next_url and url_for('login') in next_url:
        next_url = None
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            return render_template('login.html', error='用户名和密码不能为空。')

        with sqlite3.connect('2rtk.db') as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM admins WHERE username =? AND password =?", (username, password))
            if c.fetchone():
                session['admin'] = username
                return redirect(next_url or url_for('index'))
            else:
                return render_template('login.html', error='用户名或密码错误')
    
    session['next_url'] = next_url
    
    return render_template('login.html')

def get_username_by_id(user_id):
    with sqlite3.connect('2rtk.db') as conn:
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE id =?", (user_id,))
        result = c.fetchone()
    return result[0] if result else None

def get_mount_by_id(mount_id):
    with sqlite3.connect('2rtk.db') as conn:
        c = conn.cursor()
        c.execute("SELECT mount FROM mounts WHERE id =?", (mount_id,))
        result = c.fetchone()
    return result[0] if result else None

@app.route('/user_management', methods=['GET', 'POST'])
@require_login
def user_management():
    error = None
    if request.method == 'POST':
        with sqlite3.connect('2rtk.db') as conn:
            c = conn.cursor()
            if 'add_user' in request.form:
                username = request.form.get('username')
                password = request.form.get('password')
                if not username or not password:
                    error = '用户名和密码不能为空。'
                else:
                    c.execute("SELECT * FROM users WHERE username =?", (username,))
                    if c.fetchone():
                        error = '用户名已存在，请选择其他用户名。'
                    else:
                        c.execute("INSERT INTO users (username, password) VALUES (?,?)", (username, password))
                        conn.commit()
                        return redirect(url_for('user_management'))
            elif 'delete_user' in request.form:
                user_id = request.form.get('delete_user')
                c.execute("DELETE FROM users WHERE id =?", (user_id,))
                conn.commit()
                with clients_lock:
                    for client in authenticated_clients:
                        if client['user'] == get_username_by_id(user_id):
                            try:
                                client['socket'].sendall(b'REAUTH')
                            except Exception as e:
                                logger.warning(f"通知客户端重新验证失败: {e}")
                            authenticated_clients.remove(client)
                return redirect(url_for('user_management'))
            elif 'update_user' in request.form:
                user_id = request.form.get('update_user')
                username = request.form.get('username')
                password = request.form.get('password')
                if not username or not password:
                    error = '用户名和密码不能为空。'
                else:
                    c.execute("SELECT * FROM users WHERE username =? AND id !=?", (username, user_id))
                    if c.fetchone():
                        error = '用户名已存在，请选择其他用户名。'
                    else:
                        c.execute("UPDATE users SET username =?, password =? WHERE id =?", (username, password, user_id))
                        conn.commit()
                        with clients_lock:
                            for client in authenticated_clients:
                                if client['user'] == get_username_by_id(user_id):
                                    try:
                                        client['socket'].sendall(b'REAUTH')
                                    except Exception as e:
                                        logger.warning(f"通知客户端重新验证失败: {e}")
                                    authenticated_clients.remove(client)
                        return redirect(url_for('user_management'))
    
    with sqlite3.connect('2rtk.db') as conn:
        c = conn.cursor()
        c.execute("SELECT id, username, password FROM users")
        users = c.fetchall()

    with clients_lock:
        online_usernames = [client['user'] for client in authenticated_clients]

    users_with_status = []
    for user_id, username, password in users:
        status = "在线" if username in online_usernames else "离线"
        users_with_status.append((user_id, username, password, status))
    
    return render_template('user_management.html', users=users_with_status, VERSION=VERSION,
                           CONTACT_EMAIL=CONTACT_EMAIL, online_usernames=online_usernames, error=error)

@app.route('/mount_management', methods=['GET', 'POST'])
@require_login
def mount_management():
    error = None
    if request.method == 'POST':
        with sqlite3.connect('2rtk.db') as conn:
            c = conn.cursor()
            if 'add_mount' in request.form:
                mount = request.form.get('mount')
                password = request.form.get('password')
                if not mount or not password:
                    error = '挂载点名称和密码不能为空。'
                else:
                    c.execute("SELECT * FROM mounts WHERE mount =?", (mount,))
                    if c.fetchone():
                        error = '挂载点名称已存在，请选择其他名称。'
                    else:
                        c.execute("INSERT INTO mounts (mount, password) VALUES (?,?)", (mount, password))
                        conn.commit()
                        return redirect(url_for('mount_management'))
            elif 'delete_mount' in request.form:
                mount_id = request.form.get('delete_mount')
                c.execute("DELETE FROM mounts WHERE id =?", (mount_id,))
                conn.commit()
                with clients_lock:
                    for client in authenticated_clients:
                        if client['mount'] == get_mount_by_id(mount_id):
                            try:
                                client['socket'].sendall(b'REAUTH')
                            except Exception as e:
                                logger.warning(f"通知客户端重新验证失败: {e}")
                            authenticated_clients.remove(client)
                return redirect(url_for('mount_management'))
            elif 'update_mount' in request.form:
                mount_id = request.form.get('update_mount')
                mount = request.form.get('mount')
                password = request.form.get('password')
                if not mount or not password:
                    error = '挂载点名称和密码不能为空。'
                else:
                    c.execute("SELECT * FROM mounts WHERE mount =? AND id !=?", (mount, mount_id))
                    if c.fetchone():
                        error = '挂载点名称已存在，请选择其他名称。'
                    else:
                        c.execute("UPDATE mounts SET mount =?, password =? WHERE id =?", (mount, password, mount_id))
                        conn.commit()
                        with clients_lock:
                            for client in authenticated_clients:
                                if client['mount'] == get_mount_by_id(mount_id):
                                    try:
                                        client['socket'].sendall(b'REAUTH')
                                    except Exception as e:
                                        logger.warning(f"通知客户端重新验证失败: {e}")
                                    authenticated_clients.remove(client)
                        return redirect(url_for('mount_management'))
    
    with sqlite3.connect('2rtk.db') as conn:
        c = conn.cursor()
        c.execute("SELECT id, mount, password FROM mounts")
        mounts = c.fetchall()
        c.execute("SELECT mount FROM running_mounts")
        running_mounts = [row[0] for row in c.fetchall()]
    
    return render_template('mount_management.html', mounts=mounts, running_mounts=running_mounts,
                           VERSION=VERSION, CONTACT_EMAIL=CONTACT_EMAIL, error=error)

@app.route('/change_admin_password', methods=['GET', 'POST'])
@require_login
def change_admin_password():
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        if not old_password or not new_password or not confirm_password:
            return "旧密码、新密码和确认新密码不能为空。"
        if new_password != confirm_password:
            return "新密码和确认新密码不一致。"

        with sqlite3.connect('2rtk.db') as conn:
            c = conn.cursor()
            c.execute("SELECT password FROM admins WHERE id = 1")
            result = c.fetchone()
            if result and result[0] == old_password:
                c.execute("UPDATE admins SET password =? WHERE id = 1", (new_password,))
                conn.commit()
                session.pop('admin', None)
                return redirect(url_for('login'))
            else:
                return "旧密码错误"
    
    return render_template('change_admin_password.html', VERSION=VERSION,
                           CONTACT_EMAIL=CONTACT_EMAIL)

# ===================================
# 主程序入口
# ===================================
if __name__ == '__main__':
    init_db()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    clear_banner()
    try:
        server = socketserver.ThreadingTCPServer((HOST, NTRIP_PORT), Handler)
    except OSError as e:
        logger.error(f"端口绑定失败: {e}")
        sys.exit(1)
    print(f"2RTK NTRIP Caster {VERSION}已启动:{HOST}:{NTRIP_PORT}")
    logger.info(f"启动 2RTK Caster {HOST}:{NTRIP_PORT}")
    import threading
    web_thread = threading.Thread(target=app.run, kwargs={'host': HOST, 'port': WEB_PORT, 'use_reloader': False})
    print (f"2RTK Caster Web 管理界面已启动:{HOST}:{WEB_PORT}")
    web_thread.daemon = True
    web_thread.start()
    signal.signal(signal.SIGINT, shutdown)
    server.serve_forever()