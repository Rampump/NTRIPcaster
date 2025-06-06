#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import socketserver
import base64
import time
import sqlite3
import logging
from threading import Lock, Timer
from collections import deque
import os
import datetime
import signal
import sys
from flask import Flask, request, render_template, redirect, url_for, session, send_from_directory
import psutil

DEBUG = True  # 是否开启调试模式

# 设置全局日志配置（仅保留一次调用）
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

# 定义版本信息和联系邮箱
VERSION = '1.9.8'
CONTACT_EMAIL = 'i@jia.by'
HOST = '0.0.0.0'
NTRIP_PORT = 2101
WEB_PORT = 5757

BUFFER_MAXLEN = 2000
rtcm_lock = Lock()
rtcm_buffers = {}

clients_lock = Lock()
authenticated_clients = []  # 存储字典: socket, user, mount, agent, addr, auth_time, last_refresh

# logo
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
    # 获取已连接的用户数
    with clients_lock:
        connected_user_count = len(authenticated_clients)
    print(f"已连接的用户数: {connected_user_count}")

    # 获取在线挂载点
    conn = sqlite3.connect('2rtk.db')
    c = conn.cursor()
    c.execute("SELECT mount FROM running_mounts")
    online_mounts = [row[0] for row in c.fetchall()]
    conn.close()

    print(f"运行中挂载点: {', '.join(online_mounts)}")

    # 下次清屏延迟 100 秒后执行
    t = Timer(100, clear_banner)
    t.daemon = True
    t.start()

# 初始化数据库
def init_db():
    """初始化NTRIP服务数据库，创建表结构并设置默认管理员"""
    conn = sqlite3.connect('2rtk.db')
    c = conn.cursor()

    # 创建管理员表（用于系统登录）
    c.execute('''
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL
    )
    ''')
    # 创建用户表（用于用户认证）
    c.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL
    )
    ''')
    # 创建挂载点表（存储可用的数据流挂载点）
    c.execute('''
    CREATE TABLE IF NOT EXISTS mounts (
        id INTEGER PRIMARY KEY,
        mount TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL
    )
    ''')
    # 创建运行中挂载点表（记录实时数据流状态）
    c.execute('''
    CREATE TABLE IF NOT EXISTS running_mounts (
        id INTEGER PRIMARY KEY,
        mount TEXT NOT NULL,                        -- 关联mounts表的挂载点名称
        start_time INTEGER NOT NULL,                -- 挂载点启动时间（UNIX时间戳格式）
        identifier TEXT,                            -- 源标识符（通常为最近城市名或描述性名称）
        format TEXT,                                -- 数据格式（如RTCM 3.2、RTCM 3.3等）
        format_details TEXT,                        -- 数据格式详情（包含具体消息类型和发送速率）
        carrier INTEGER DEFAULT 0,                  -- 载波相位信息：0=无相位，1=L1，2=L1+L2
        nav_system TEXT,                            -- 支持的导航系统（多个系统用+连接，如GPS+GLO+BDS）
        network TEXT,                               -- 所属网络名称
        country TEXT,                               -- ISO 3166国家代码（3字符）
        latitude REAL,                              -- 纬度（高精度浮点数）
        longitude REAL,                             -- 经度（高精度浮点数）
        nmea INTEGER DEFAULT 0,                     -- 是否需要NMEA输入：0=否，1=是
        solution INTEGER DEFAULT 0,                 -- 解算类型：0=单基站，1=网络解算
        generator TEXT,                             -- 生成数据的软硬件名称
        compression TEXT,                           -- 压缩算法
        authentication TEXT,                        -- 认证方式：N=无，B=Basic，D=Digest等
        fee TEXT DEFAULT 'N',                       -- 是否收费：Y=是，N=否
        bitrate INTEGER,                            -- 数据速率（bps）
        misc TEXT,                                  -- 其他杂项信息
        update_time INTEGER,                  -- 记录更新时间（自动更新）
        UNIQUE (mount),                             -- 确保每个挂载点只记录一次
        FOREIGN KEY (mount) REFERENCES mounts(mount) -- 外键约束：关联mounts表的mount字段
            ON DELETE CASCADE                       -- 级联删除：当mounts表记录删除时自动删除关联记录
            ON UPDATE CASCADE                       -- 级联更新：当mounts表记录更新时自动更新关联记录
    )
    ''')
    c.execute("DELETE FROM running_mounts")
    conn.commit()
    print("运行中的挂载点列表已清空")
    # 如果没有管理员，创建默认管理员
    c.execute("SELECT * FROM admins")
    if not c.fetchone():
        c.execute("INSERT INTO admins (username, password) VALUES ('admin', 'admin')")
        print("已创建默认管理员: admin/admin")
        print("⚠️ 请务必在首次登录后修改默认密码！")
    conn.commit()
    conn.close()
    print("数据库初始化完成")

# 处理 NTRIP 请求的处理器
class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        dbg(f"连接: {self.client_address}")
        try:
            raw = self.request.recv(1024).decode(errors='ignore')
        except Exception as e:
            logger.error(f"接收失败: {e}")
            return
        if not raw:
            return
        req, hdrs = self._parse(raw)
        protocol_version = self.get_protocol_version(req)  # 获取协议版本
        if req.startswith('SOURCE'):
            self._source(req, hdrs, protocol_version)
        elif req.startswith('GET'):
            self._get(req, hdrs, protocol_version)
        else:
            logger.warning(f"未知请求: {req}")

    def get_client_model(self, agent):
        if agent and 'NTRIP' in agent:
            return agent.split('NTRIP', 1)[1].strip()
        return 'N/A'

    def get_protocol_version(self, req):
        parts = req.split()
        if len(parts) >= 3:
            protocol = parts[-1]
            if protocol == 'HTTP/1.1':
                return 'NTRIP v2.0'
            elif protocol == 'HTTP/1.0':
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

    def _source(self, req: str, hdrs: dict, protocol_version):
        dbg("SOURCE 上传请求验证")
        p = req.split()
        if len(p) < 3:
            self.request.sendall(b'ERROR - Bad Password\r\n')
            return
        mount = p[2]

        conn = sqlite3.connect('2rtk.db')
        c = conn.cursor()
        c.execute("SELECT password FROM mounts WHERE mount =?", (mount,))
        result = c.fetchone()

        if not result or result[0] != p[1]:
            self.request.sendall(b'ERROR - Bad Password\r\n')
            dbg(f"上传认证失败，密码错误: mount={mount}, addr={self.client_address}")
            conn.close()
            return

        self.request.sendall(b'ICY 200 OK\r\n')
        agent = self.get_client_model(hdrs.get('Source-Agent', ''))
        logger.info(f"上传认证成功: mount={mount}, {agent}, addr={self.client_address}")
        print(f"⟳ {mount} 上传认证成功... 来自{self.client_address}, 基准站端:{agent}, 数据上传中")

        current_time = time.time()
        
        c.execute('''
            INSERT INTO running_mounts (
                mount, start_time, identifier, format, format_details, 
                carrier, nav_system, network, country, latitude, 
                longitude, nmea, solution, generator, compression, 
                authentication, fee, bitrate, misc, update_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            mount,                      # mount
            current_time,               # start_time
            'none',                     # identifier
            'RTCM 3.3',                 # format
            '1005(10)',                 # format_details
            0,                          # carrier
            'GPS',                      # nav_system
            '2RTK',                     # network
            'CHN',                      # country
            25.2034,                    # latitude
            110.2777,                   # longitude
            0,                          # nmea
            0,                          # solution
            agent,                      # generator
            'none',                     # compression
            'B',                        # authentication
            'N',                        # fee
            500,                        # bitrate
            'NO',                       # misc
            current_time                # update_time
        ))
        conn.commit()
        self._generate_mount_list()
        print(f"⟳ {mount} 挂载点已导入运行数据库中，数据上传中...")

        # 为该挂载点创建一个新的 rtcm_buffer
        with rtcm_lock:
            if mount not in rtcm_buffers:
                rtcm_buffers[mount] = deque(maxlen=BUFFER_MAXLEN)
        print(f"⟳ {mount} 挂载点的 rtcm_buffer 已创建，最大长度为 {BUFFER_MAXLEN} 条")

        while True:
            try:
                chunk = self.request.recv(4096)
            except Exception as e:
                logger.error(f"⟳{mount}基准站连接异常: {e}")
                break
            if not chunk:
                dbg(f"⟳{mount}基准站连接断开: {agent}, {self.client_address}")
                break
            ts = time.time()
            with rtcm_lock:
                rtcm_buffers[mount].append((ts, mount, chunk))
            self._broadcast(mount)

        c.execute("DELETE FROM running_mounts WHERE mount =?", (mount,))
        conn.commit()
        conn.close()
        logger.info(f"基准站连接断开:{agent}, {self.client_address}")
        dbg(f"⟳{mount}连接断开已从运行数据库中移除..{agent}, {self.client_address}.")
        print(f"⟳ {mount} 已从运行数据库中移除...{agent}, {self.client_address}")
        self._generate_mount_list()

        # 移除该挂载点的 rtcm_buffer
        with rtcm_lock:
            if mount in rtcm_buffers:
                del rtcm_buffers[mount]

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
            self.request.sendall(b'HTTP/1.1 404 Not Found\r\n\r\n')
            conn.close()
            return
        auth = hdrs.get('Authorization')
        if not auth:
            conn.close()
            return self._challenge()
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
            self.request.sendall(b'HTTP/1.1 401 Unauthorized\r\n\r\n')
            conn.close()
            return
        now = time.time()
        with clients_lock:
            for client in authenticated_clients:
                if client['user'] == u and client['mount'] == mount and client['agent'] == agent:
                    client['last_refresh'] = now
                    conn.close()
                    return
            sess = [client for client in authenticated_clients if client['user'] == u and client['mount'] == mount]
            if len(sess) >= 3:
                old = min(sess, key=lambda x: x['auth_time'])
                authenticated_clients.remove(old)
            client = {'socket': self.request, 'user': u, 'mount': mount, 'agent': agent, 'addr': self.client_address, 'auth_time': now - 5, 'last_refresh': now}
            authenticated_clients.append(client)
        logger.info(f"下载认证通过，用户: {u}来自{self.client_address}使用客户端: {agent}, 协议 {protocol_version}至挂载点 {mount}")
        dbg(f"下载认证通过，用户: {u}来自{self.client_address}使用客户端: {agent}, 协议 {protocol_version}至挂载点 {mount}")
        print(f"下载认证通过，用户: {u}来自{self.client_address}使用客户端: {agent}, 协议 {protocol_version}挂载点 {mount}")
        ver, date = parts[2], datetime.datetime.now(datetime.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        if ver == 'HTTP/1.1' and hdrs.get('Ntrip-Version') == 'Ntrip/2.0':
            hdr = '\r\n'.join([
                'HTTP/1.1 200 OK', 'Ntrip-Version: Ntrip/2.0', f'Server: 2RTK Caster/{VERSION}',
                f'Date: {date}', 'Cache-Control: no-store,max-age=0', 'Pragma: no-cache',
                'Connection: close', 'Content-Type: gnss/data', 'Transfer-Encoding: chunked', ''
            ])
        else:
            hdr = 'ICY 200 OK\r\n'
        self.request.sendall(hdr.encode())
        print(f"开始从 {mount} 向{self.client_address}{agent}用户: {u}发送数据流...")
        try:
            while True:
                time.sleep(0.1)
        except:
            pass
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

    def _challenge(self):
        self.request.sendall(b'HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm="NTRIP"\r\nContent-Length: 0\r\n\r\n')

    def _broadcast(self, mount):
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
                    c['socket'].sendall(chunk)
                except Exception as e:
                    logger.warning(f"通信关闭移除连接数据: addr={c['addr']}, err={e}")
                    to_remove.append(c)
                    break
        if to_remove:
            with clients_lock:
                for c in to_remove:
                    if c in authenticated_clients:
                        authenticated_clients.remove(c)

def shutdown(sig, frame):
    print("\n正在关闭caster服务器…")

    # 清空运行中的挂载点数据
    try:
        conn = sqlite3.connect('2rtk.db')
        c = conn.cursor()
        c.execute("DELETE FROM running_mounts")
        conn.commit()
        conn.close()
        print("已清空运行中的挂载点数据")
    except Exception as e:
        logger.error(f"清空运行中挂载点数据时出错: {e}")

    # 强制关闭所有已认证客户端的套接字
    global authenticated_clients
    with clients_lock:
        for client in authenticated_clients:
            try:
                # 禁用发送和接收操作，强制关闭连接
                client['socket'].shutdown(socketserver.socket.SHUT_RDWR)
                client['socket'].close()
            except Exception as e:
                logger.warning(f"关闭客户端套接字时出错: addr={client['addr']}, err={e}")
        authenticated_clients = []

    # 强制关闭socketserver实例
    try:
        # 先停止接受新的请求
        server.shutdown()
        # 关闭底层的服务器套接字，释放占用的端口
        server.server_close()
        print("NTRIP服务器已成功关闭")
    except Exception as e:
        logger.error(f"关闭NTRIP服务器时出错: err={e}")

    # 关闭数据库连接
    try:
        # 不需要再次连接和关闭，上面已经处理过
        pass
    except Exception as e:
        logger.error(f"关闭数据库连接时出错: err={e}")

    # 终止Web服务器线程
    try:
        # 由于Flask没有提供优雅的关闭方法，我们可以通过退出Python进程来强制关闭
        sys.exit(0)
    except Exception as e:
        logger.error(f"关闭Web服务器时出错: err={e}")

## Web 应用
app = Flask(__name__)
app.secret_key = '9#*&K47g@U2xR6!8pX3m$yQ0%z5L1cV7bW9dF3hJ6n2r'

ALIPAY_QR_URL = 'https://2rtk.rampump.cn/alipay.jpg'
WECHAT_QR_URL = 'https://2rtk.rampump.cn/wechat.jpg'

def format_time_duration(seconds):
    """
    将秒数转换为更易读的时间格式
    :param seconds: 秒数
    :return: 格式化后的时间字符串
    """
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
            # 存储当前URL作为登录后的跳转目标
            next_url = request.url
            return redirect(url_for('login', next=next_url))
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

# 添加获取二维码图片的路由
@app.route('/alipay_qr')
def get_alipay_qr():
    return redirect(ALIPAY_QR_URL)

@app.route('/wechat_qr')
def get_wechat_qr():
    return redirect(WECHAT_QR_URL)

@app.route('/static/<path:filename>')
def serve_static(filename):
    # 为图标和背景图设置较长的缓存时间
    cache_timeout = 2592000  # 30天（以秒为单位）
    
    # 为favicon.ico设置特定的MIME类型
    if filename == 'favicon.ico':
        return send_from_directory(
            os.path.join(app.root_path, 'static'),
            filename,
            mimetype='image/vnd.microsoft.icon',
            cache_timeout=cache_timeout
        )
    
    # 其他静态文件使用默认MIME类型检测
    return send_from_directory(
        os.path.join(app.root_path, 'static'),
        filename,
        cache_timeout=cache_timeout
    )

@app.route('/')
def index():
    # 获取系统信息
    cpu_percent = psutil.cpu_percent(interval=1)
    mem_percent = psutil.virtual_memory().percent
    # 计算程序运行时长
    start_datetime = datetime.datetime.fromtimestamp(START_TIME)
    formatted_start_time = start_datetime.strftime('%Y-%m-%d %H:%M:%S')
    now = datetime.datetime.now()

    # 获取运行中的挂载点
    running_mounts = []
    with sqlite3.connect('2rtk.db') as conn:
        c = conn.cursor()
        c.execute("SELECT mount, start_time FROM running_mounts")
        for row in c.fetchall():
            mount = row[0]
            start_time = datetime.datetime.fromtimestamp(row[1])
            running_mounts.append((mount, start_time))

    # 直接从全局列表中获取在线用户信息
    running_users = []
    with clients_lock:
        for client in authenticated_clients:
            username = client['user']
            mount = client['mount']
            agent = client['agent']
            ip = client['addr'][0]
            port = client['addr'][1]
            auth_time = auth_time = datetime.datetime.fromtimestamp(client['auth_time'])
            running_users.append((username, mount, agent, ip, port,auth_time ))

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
        session.pop('admin', None)  # 清除会话中的管理员标识
        return redirect(url_for('index'))  # 退出后返回到首页

    # 存储目标URL（如果有的话）
    next_url = request.args.get('next') or request.referrer
    if next_url and url_for('login') in next_url:
        next_url = None  # 避免循环重定向到登录页面
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        # 检测空白表单提交
        if not username or not password:
            return render_template('login.html', error='用户名和密码不能为空。')

        with sqlite3.connect('2rtk.db') as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM admins WHERE username =? AND password =?", (username, password))
            if c.fetchone():
                session['admin'] = username
                # 登录成功后重定向到目标URL或首页
                return redirect(next_url or url_for('index'))
            else:
                return render_template('login.html', error='用户名或密码错误')
    
    # 存储目标URL到会话中
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
                # 检测空白表单提交
                if not username or not password:
                    error = '用户名和密码不能为空。'
                else:
                    # 检查用户名是否已存在
                    c.execute("SELECT * FROM users WHERE username =?", (username,))
                    if c.fetchone():
                        error = '用户名已存在，请选择其他用户名。'
                    else:
                        c.execute("INSERT INTO users (username, password) VALUES (?,?)", (username, password))
                        conn.commit()
                        return redirect(url_for('user_management'))  # PRG 模式
            elif 'delete_user' in request.form:
                user_id = request.form.get('delete_user')
                c.execute("DELETE FROM users WHERE id =?", (user_id,))
                conn.commit()
                # 通知相关客户端重新验证或断开连接
                with clients_lock:
                    for client in authenticated_clients:
                        if client['user'] == get_username_by_id(user_id):
                            try:
                                client['socket'].sendall(b'REAUTH')  # 发送重新验证信号
                            except Exception as e:
                                logger.warning(f"通知客户端重新验证失败: {e}")
                            authenticated_clients.remove(client)
                return redirect(url_for('user_management'))  # PRG 模式
            elif 'update_user' in request.form:
                user_id = request.form.get('update_user')
                username = request.form.get('username')
                password = request.form.get('password')
                # 检测空白表单提交
                if not username or not password:
                    error = '用户名和密码不能为空。'
                else:
                    # 检查新用户名是否已存在（排除当前用户）
                    c.execute("SELECT * FROM users WHERE username =? AND id !=?", (username, user_id))
                    if c.fetchone():
                        error = '用户名已存在，请选择其他用户名。'
                    else:
                        c.execute("UPDATE users SET username =?, password =? WHERE id =?", (username, password, user_id))
                        conn.commit()
                        # 通知相关客户端重新验证或断开连接
                        with clients_lock:
                            for client in authenticated_clients:
                                if client['user'] == get_username_by_id(user_id):
                                    try:
                                        client['socket'].sendall(b'REAUTH')  # 发送重新验证信号
                                    except Exception as e:
                                        logger.warning(f"通知客户端重新验证失败: {e}")
                                    authenticated_clients.remove(client)
                        return redirect(url_for('user_management'))  # PRG 模式
    
    with sqlite3.connect('2rtk.db') as conn:
        c = conn.cursor()
        c.execute("SELECT id, username, password FROM users")
        users = c.fetchall()

    # 获取在线用户列表
    with clients_lock:
        online_usernames = [client['user'] for client in authenticated_clients]

    # 创建一个包含用户在线状态的列表
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
                # 检测空白表单提交
                if not mount or not password:
                    error = '挂载点名称和密码不能为空。'
                else:
                    # 检查挂载点名称是否已存在
                    c.execute("SELECT * FROM mounts WHERE mount =?", (mount,))
                    if c.fetchone():
                        error = '挂载点名称已存在，请选择其他名称。'
                    else:
                        c.execute("INSERT INTO mounts (mount, password) VALUES (?,?)", (mount, password))
                        conn.commit()
                        return redirect(url_for('mount_management'))  # PRG 模式
            elif 'delete_mount' in request.form:
                mount_id = request.form.get('delete_mount')
                c.execute("DELETE FROM mounts WHERE id =?", (mount_id,))
                conn.commit()
                # 通知相关客户端重新验证或断开连接
                with clients_lock:
                    for client in authenticated_clients:
                        if client['mount'] == get_mount_by_id(mount_id):
                            try:
                                client['socket'].sendall(b'REAUTH')  # 发送重新验证信号
                            except Exception as e:
                                logger.warning(f"通知客户端重新验证失败: {e}")
                            authenticated_clients.remove(client)
                return redirect(url_for('mount_management'))  # PRG 模式
            elif 'update_mount' in request.form:
                mount_id = request.form.get('update_mount')
                mount = request.form.get('mount')
                password = request.form.get('password')
                # 检测空白表单提交
                if not mount or not password:
                    error = '挂载点名称和密码不能为空。'
                else:
                    # 检查新挂载点名称是否已存在（排除当前挂载点）
                    c.execute("SELECT * FROM mounts WHERE mount =? AND id !=?", (mount, mount_id))
                    if c.fetchone():
                        error = '挂载点名称已存在，请选择其他名称。'
                    else:
                        c.execute("UPDATE mounts SET mount =?, password =? WHERE id =?", (mount, password, mount_id))
                        conn.commit()
                        # 通知相关客户端重新验证或断开连接
                        with clients_lock:
                            for client in authenticated_clients:
                                if client['mount'] == get_mount_by_id(mount_id):
                                    try:
                                        client['socket'].sendall(b'REAUTH')  # 发送重新验证信号
                                    except Exception as e:
                                        logger.warning(f"通知客户端重新验证失败: {e}")
                                    authenticated_clients.remove(client)
                        return redirect(url_for('mount_management'))  # PRG 模式
    
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
        # 检测空白表单提交
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
                session.pop('admin', None)  # 移除管理员会话
                return redirect(url_for('login'))  # PRG 模式
            else:
                return "旧密码错误"
    
    return render_template('change_admin_password.html', VERSION=VERSION,
                           CONTACT_EMAIL=CONTACT_EMAIL)

if __name__ == '__main__':
    # 初始化数据库
    init_db()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    # 初启动定时清屏
    clear_banner()
    # 启动 caster 服务器
    try:
        server = socketserver.ThreadingTCPServer((HOST, NTRIP_PORT), Handler)
    except OSError as e:
        logger.error(f"端口绑定失败: {e}")
        sys.exit(1)
    print(f"2RTK NTRIP Caster {VERSION}已启动:{HOST}:{NTRIP_PORT}")
    logger.info(f"启动 2RTK Caster {HOST}:{NTRIP_PORT}")
    # 启动 Web 服务器
    import threading
    web_thread = threading.Thread(target=app.run, kwargs={'host': HOST, 'port': WEB_PORT})
    print (f"2RTK Caster Web 管理界面已启动:{HOST}:{WEB_PORT}")
    web_thread.daemon = True
    web_thread.start()
    signal.signal(signal.SIGINT, shutdown)
    server.serve_forever()