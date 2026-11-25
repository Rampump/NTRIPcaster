#!/usr/bin/env python3
"""
external_caster.py - 外部 Caster STR 拉取与按需 relay

设计文档：docs/external-caster-relay-design.md

职责：
  1. 每个外部源一个拉取线程，按 6h 周期（失败指数退避）拉 SOURCETABLE 并缓存；
  2. 把外部挂载点融合进本地 STR 表（由 connection.py 调用 get_fused_rows）；
  3. 用户订阅外部挂载点时按需建立上游连接，把 RTCM 喂给 forwarder.upload_data()；
  4. 账号池 / agent 标识池 / GGA 上行 / 外部统计 / 三级限额。

边界：
  - 总开关 EXTERNAL_CASTER_ENABLED=false 时本模块完全不产生行为；
  - 外部挂载点绝不进 connection.online_mounts（会与源站上传占用判定冲突）；
  - relay 只调 forwarder.upload_data()，绝不调 add_client()（参数是下游 socket）。
"""

import base64
import hashlib
import json
import logging
import os
import random
import re
import socket
import ssl
import threading
import time
import traceback
from collections import deque

from cryptography.fernet import Fernet, InvalidToken

from . import config, database, forwarder
from .logger import log_info, log_warning, log_error, log_debug

# 出站固定回退标识（UA 池关闭或全部冷却时使用）
FIXED_AGENT = '2RTK NTRIP Caster'
# 挂载点名白名单：NTRIP 路径段与 Web 路由共用，防注入
MOUNT_NAME_RE = re.compile(r'^[A-Za-z0-9_\-.~]{1,128}$')
# 上游明确表示"挂载点不存在"的响应（只有这类错误才触发按需 STR 刷新）
NO_MOUNT_RE = re.compile(
    r'no such mountpoint|unknown mountpoint|mountpoint not found|mount not found', re.I)
GGA_LINE_RE = re.compile(br'\$([A-Z]{2}GGA,[^\r\n*]*)\*([0-9A-Fa-f]{2})')


def _valid_mount(name):
    return bool(name) and bool(MOUNT_NAME_RE.match(name))


def _jitter(seconds):
    """±EXTERNAL_STR_JITTER 随机抖动，避免多源同时请求"""
    j = max(0.0, min(1.0, config.EXTERNAL_STR_JITTER))
    return max(1.0, seconds * (1.0 + random.uniform(-j, j)))


def _fernet_key():
    """从持久化的 SECRET_KEY 派生 32 字节 Fernet 密钥"""
    return base64.urlsafe_b64encode(
        hashlib.sha256(config.SECRET_KEY.encode('utf-8')).digest())


class _SockReader:
    """带内部缓冲的 HTTP 行读取器：makefile() 会预读吞掉后续 RTCM 字节，不能用于 relay"""

    def __init__(self, sock, timeout):
        self.sock = sock
        self.buf = b''
        self.total = 0
        sock.settimeout(timeout)

    def fill(self):
        chunk = self.sock.recv(8192)
        if not chunk:
            raise EOFError('upstream closed')
        self.total += len(chunk)
        if self.total > config.EXTERNAL_MAX_STR_BYTES:
            raise ValueError('STR response too large')
        self.buf += chunk

    def readline(self, max_line=8192):
        while b'\n' not in self.buf:
            if len(self.buf) > max_line:
                raise ValueError('HTTP line too long')
            self.fill()
        line, self.buf = self.buf.split(b'\n', 1)
        return line.rstrip(b'\r')


def _parse_str_line(line):
    """19 段 STR 行 → (mount, lat, lon)；任何异常返回 None（整张表里坏行只跳过该行）"""
    try:
        parts = line.split(';')
        if len(parts) < 19 or parts[0].strip() != 'STR':
            return None
        mount = parts[1].strip()
        if not _valid_mount(mount):
            return None
        lat = float(parts[9]) if parts[9].strip() else None
        lon = float(parts[10]) if parts[10].strip() else None
        return mount, line, lat, lon
    except (ValueError, IndexError, AttributeError):
        return None


def _nmea_checksum_ok(payload):
    crc = 0
    for b in payload:
        crc ^= b
    return crc


class RelayState:
    """单条外部 relay 的运行时状态（按 mount 唯一）"""

    def __init__(self, mount, source, account, profile, sock, reader):
        self.mount = mount
        self.source_id = source['id']
        self.account_id = account['id'] if account else None
        self.profile_id = profile['id'] if profile else None
        self.profile_string = profile['agent_string'] if profile else FIXED_AGENT
        self.sock = sock
        self.reader = reader
        self.holders = set()
        self.refs = 0
        self.state = 'connecting'
        self.last_data_at = time.time()
        self.last_error = ''
        self.send_lock = threading.Lock()
        self.recv_thread = None
        self.grace_thread = None
        self.gga_line = b''
        self.gga_source_tag = ''
        self.gga_sent_at = 0.0
        self.gga_thread = None
        self.lease_thread = None
        self.stop_event = threading.Event()
        self.released = False   # 防重复归还 semaphore / 账号租约


class ExternalCasterManager:
    """外部 caster 管理器（进程内单例）"""

    def __init__(self):
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._started = False
        self._fernet = None
        self._fetch_threads = {}
        # 每个 fetcher 独立 stop_event：reload_config 必须能真正停掉旧线程，
        # 否则每改一次源配置就会多一个常驻拉取线程，6h 到期时同时拉多次。
        self._fetch_stop_events = {}
        # per-source 单飞：定时 / 按需 / 强制刷新不得并发拉同一张 STR 表。
        self._fetch_inflight = set()
        self._relays = {}
        self._source_relay_count = {}
        self._demand_inflight = set()
        self._demand_history = {}          # source_id -> deque[timestamp]
        self._negative_cache = {}          # mount -> 过期时间
        self._profile_cache = []
        self._account_profiles = {}        # account_id -> profile dict
        self._stats = {}                   # mount -> 统计字典（外部单独存，字段对齐）

    # ---------- 生命周期 ----------

    def start(self):
        if not config.EXTERNAL_CASTER_ENABLED:
            return
        with self._lock:
            if self._started:
                return
            if not config.SECRET_KEY_PERSISTENT:
                # 账号/上游密码密文依赖稳定密钥；卷不可写时启动即失败，
                # 否则密文会在下次重启后变成永久乱码。
                raise RuntimeError(
                    '外部 Caster 已启用，但 DATA_DIR 不可写导致密钥无法持久化；'
                    '请先修复挂载卷权限再启动')
            self._fernet = Fernet(_fernet_key())
            self._started = True
            self._stop_event.clear()
            database.gc_expired_leases()
            self._load_agent_profiles()
            for src in database.list_external_casters(enabled_only=True):
                self._start_fetcher(src['id'])
            log_info('外部 Caster 模块已启动', module='external_caster')

    def stop(self):
        with self._lock:
            if not self._started:
                return
            self._started = False
            self._stop_event.set()
            fetchers = list(self._fetch_threads.values())
            stop_events = list(self._fetch_stop_events.values())
            self._fetch_threads.clear()
            self._fetch_stop_events.clear()
            relays = list(self._relays.values())
        for ev in stop_events:
            ev.set()
        for t in fetchers:
            t.join(timeout=3)
        for relay in relays:
            self._close_relay(relay.mount, 'shutdown')
        log_info('外部 Caster 模块已停止', module='external_caster')

    def reload_config(self):
        """管理员改配置后重建拉取线程（必须先停旧线程，避免重复 fetcher）"""
        if not config.EXTERNAL_CASTER_ENABLED:
            return
        with self._lock:
            old_threads = dict(self._fetch_threads)
            old_events = dict(self._fetch_stop_events)
            self._fetch_threads.clear()
            self._fetch_stop_events.clear()
        for ev in old_events.values():
            ev.set()
        for t in old_threads.values():
            t.join(timeout=5)
        for src in database.list_external_casters(enabled_only=True):
            self._start_fetcher(src['id'])

    def on_source_deleted(self, source_id):
        """源被删除后清理其内存态：停 fetcher、关 relay、清统计。

        删除源时数据库子表会被清掉，但内存里的 relay 仍持有 src/account，
        若不主动关闭会继续向上游拉流并占用全局 relay 名额。
        """
        with self._lock:
            stop_event = self._fetch_stop_events.pop(source_id, None)
            self._fetch_threads.pop(source_id, None)
            self._fetch_inflight.discard(source_id)
            self._demand_inflight.discard(source_id)
            mounts = [m for m, r in self._relays.items()
                      if r.source_id == source_id]
            self._source_relay_count.pop(source_id, None)
        if stop_event:
            stop_event.set()
        for mount in mounts:
            self._close_relay(mount, 'source_deleted')
        log_info(f'外部源已删除，内存态清理完成: source_id={source_id}',
                 module='external_caster')

    # ---------- 加解密与 profile ----------

    def encrypt(self, plain):
        if not plain:
            return ''
        return self._fernet.encrypt(plain.encode('utf-8')).decode('ascii')

    def decrypt(self, cipher):
        if not cipher:
            return ''
        try:
            return self._fernet.decrypt(cipher.encode('ascii')).decode('utf-8')
        except (InvalidToken, ValueError):
            log_warning('外部源密码解密失败，请检查密钥是否变更', module='external_caster')
            return ''

    def mask_secret(self, cipher):
        """API 只回掩码，绝不回明文或密文"""
        return '******' if cipher else ''

    def _load_agent_profiles(self):
        path = config.EXTERNAL_UA_PROFILE_FILE
        if config.EXTERNAL_UA_ROTATION == 'per_account':
            try:
                with open(path, encoding='utf-8') as f:
                    profiles = json.load(f)
                database.seed_agent_profiles(profiles)
            except (OSError, ValueError) as e:
                log_warning(f'agent profile 文件不可用({e})，回退固定标识',
                            module='external_caster')
        self._profile_cache = database.list_agent_profiles(available_only=True)

    def _choose_profile(self, account=None):
        """按账号绑定 / 加权随机 / 按源粘滞选 agent profile；全不可用回退固定串"""
        if not config.EXTERNAL_UA_POOL_ENABLED:
            return None
        if not self._profile_cache:
            self._load_agent_profiles()
        pool = list(self._profile_cache)
        if not config.EXTERNAL_UA_ALLOW_BROWSER:
            pool = [p for p in pool if not p['is_browser']]
        if not pool:
            log_warning('agent 池无可用 profile，回退固定标识',
                        module='external_caster')
            return None

        if config.EXTERNAL_UA_ROTATION == 'per_account' and account:
            bound_id = account.get('agent_profile_id')
            bound = next((p for p in pool if p['id'] == bound_id), None)
            if bound is None:
                bound = self._weighted_choice(pool)
                if bound:
                    database.update_external_account(
                        account['id'], {'agent_profile_id': bound['id']})
                    self._account_profiles[account['id']] = bound
            return bound

        if config.EXTERNAL_UA_ROTATION == 'sticky_source' and account:
            idx = account['source_id'] % len(pool)
            return pool[idx]
        return self._weighted_choice(pool)

    @staticmethod
    def _weighted_choice(pool):
        total = sum(max(1, p['weight']) for p in pool)
        r = random.uniform(0, total)
        upto = 0
        for p in pool:
            upto += max(1, p['weight'])
            if r <= upto:
                return p
        return pool[-1]

    def _on_agent_rejected(self, profile_id):
        """上游 403/406：冷却该 profile 并指数加重，换一个重试（不持续试探）"""
        if not profile_id:
            return
        row = next((p for p in self._profile_cache if p['id'] == profile_id), None)
        n = (row or {}).get('block_count', 0) + 1
        delay = min(config.EXTERNAL_UA_BLOCK_COOLDOWN * (2 ** min(n - 1, 3)),
                    config.EXTERNAL_UA_BLOCK_COOLDOWN * 8)
        database.block_agent_profile(profile_id, time.time() + delay)
        self._profile_cache = [p for p in self._profile_cache if p['id'] != profile_id]
        log_warning(f'agent profile #{profile_id} 被上游拒绝，冷却 {int(delay)}s',
                    module='external_caster')

    def list_agent_profiles(self):
        return database.list_agent_profiles()

    def unblock_agent_profile(self, profile_id):
        with self._lock:
            database.update_agent_profile(profile_id, {
                'blocked_until': None, 'block_count': 0})
            self._profile_cache = []
            self._load_agent_profiles()
        return True

    # ---------- 上游 HTTP ----------

    @staticmethod
    def _request_line(mount, host, port, agent, username='', password=''):
        path = '/' if not mount else '/' + mount
        lines = [
            f'GET {path} HTTP/1.1',
            f'Host: {host}:{port}',
            f'User-Agent: {agent}',
            'Ntrip-Version: Ntrip/2.0',
            'Accept: */*',
            'Connection: close',
        ]
        if username:
            token = base64.b64encode(f'{username}:{password}'.encode('utf-8')).decode('ascii')
            lines.append(f'Authorization: Basic {token}')
        return ('\r\n'.join(lines) + '\r\n\r\n').encode('ascii')

    def _connect(self, source, mount, account=None):
        """建 TCP(+TLS) 连接并发出 GET；返回 (sock, reader, status, headers)"""
        agent_profile = self._choose_profile(account)
        agent = agent_profile['agent_string'] if agent_profile else FIXED_AGENT
        username, password = '', ''
        if account:
            username = account['username']
            password = self.decrypt(account['password_enc'])
        elif source.get('username'):
            username = source['username']
            password = self.decrypt(source['password_enc'])

        sock = socket.create_connection(
            (source['host'], int(source['port'])),
            timeout=config.EXTERNAL_CONNECT_TIMEOUT)
        if source.get('tls'):
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=source['host'])
        sock.sendall(self._request_line(
            mount, source['host'], source['port'], agent, username, password))
        reader = _SockReader(sock, config.EXTERNAL_READ_TIMEOUT)
        status_line = reader.readline().decode('utf-8', 'replace')
        m = re.search(r'\s(\d{3})\b', status_line)
        status = int(m.group(1)) if m else 0
        headers = {}
        while True:
            line = reader.readline()
            if not line:
                break
            if b':' in line:
                k, v = line.split(b':', 1)
                headers[k.decode('ascii', 'ignore').strip().lower()] = \
                    v.decode('utf-8', 'replace').strip()
        return sock, reader, status, headers, agent_profile

    # ---------- STR 拉取 ----------

    def _start_fetcher(self, source_id):
        with self._lock:
            if source_id in self._fetch_threads:
                return
            stop_event = threading.Event()
            t = threading.Thread(
                target=self._fetch_loop, args=(source_id, stop_event),
                name=f'ext-fetch-{source_id}', daemon=True)
            self._fetch_threads[source_id] = t
            self._fetch_stop_events[source_id] = stop_event
            t.start()

    def _try_fetch(self, source, fail_count=0, demand=False):
        """per-source 单飞拉取；被其他路径占用时返回 False，不重复请求上游。"""
        source_id = source['id']
        with self._lock:
            if source_id in self._fetch_inflight:
                return False
            self._fetch_inflight.add(source_id)
        try:
            self._fetch_once(source, fail_count, demand)
            return True
        finally:
            with self._lock:
                self._fetch_inflight.discard(source_id)

    def _fetch_loop(self, source_id, stop_event):
        fail_count = 0
        while not self._stop_event.is_set() and not stop_event.is_set():
            src = database.get_external_caster(source_id)
            if not src or not src['enabled']:
                break
            now = time.time()
            wait = (src['next_fetch_at'] or 0) - now
            if wait > 0:
                # 分段等待（便于及时响应停止信号）；醒来后必须回到循环
                # 重新读取 next_fetch_at 判断，否则每 60s 就会误拉一次。
                if stop_event.wait(timeout=min(wait, 60)):
                    break
                continue
            if self._stop_event.is_set() or stop_event.is_set():
                break
            try:
                if not self._try_fetch(src, fail_count):
                    # 定时/按需/强制刷新正在拉同一张表，稍后再看，不算失败
                    time.sleep(0.5)
                    continue
                fail_count = 0
            except Exception as e:
                fail_count += 1
                base_delay = min(
                    max(config.EXTERNAL_STR_REFRESH, 60) * (2 ** min(fail_count - 1, 3)),
                    config.EXTERNAL_STR_BACKOFF_MAX)
                # 401 视为账号问题，直接退到上限，不快速重试
                if getattr(e, 'http_status', None) == 401:
                    base_delay = config.EXTERNAL_STR_BACKOFF_MAX
                database.update_external_fetch_state(
                    source_id, fail_count, time.time() + _jitter(base_delay))
                database.update_external_status(source_id, 'error', str(e))
                log_warning(f'STR fetch failed: source={src["name"]} error={e}',
                            module='external_caster')
            time.sleep(0.2)

    def _fetch_once(self, source, fail_count=0, demand=False):
        """拉一次 STR 表；成功落库并复位退避。raise 异常由 _fetch_loop 处理。"""
        # 诊断：记录每次真实拉表的调用栈，定位是谁在触发（写文件，不推前端）
        try:
            stack = ' <- '.join(
                f'{fr.name}:{fr.lineno}'
                for fr in traceback.extract_stack()[-8:-1])
            logging.getLogger('ntrip.external_caster').warning(
                'STR_FETCH_TRACE source=%s demand=%s stack=%s',
                source['name'], demand, stack)
        except Exception:
            pass
        database.update_external_status(source['id'], 'fetching')
        sock = reader = None
        profile_id = None
        try:
            sock, reader, status, _headers, profile = self._connect(source, '')
            profile_id = profile['id'] if profile else None
            if profile_id:
                database.mark_agent_profile_used(profile_id)
            if status in (401, 403, 406):
                err = _FetchError(f'HTTP {status}', status)
                if status in (403, 406):
                    self._on_agent_rejected(profile_id)
                raise err
            if status != 200:
                raise _FetchError(f'HTTP {status}', status)

            rows = []
            seen = set()
            got_any_line = False
            while True:
                try:
                    line = reader.readline()
                except EOFError:
                    # 部分 caster（如 2rtk.net）不发 ENDSOURCETABLE，直接关连接；
                    # 只要已经收到过 SOURCETABLE 行，EOF 就视为正常收表结束。
                    if not got_any_line:
                        raise _FetchError('upstream closed before SOURCETABLE')
                    break
                if not line:
                    break
                text = line.decode('utf-8', 'replace').strip()
                if not text:
                    continue
                got_any_line = True
                if 'ENDSOURCETABLE' in text.upper():
                    break
                if not text.startswith('STR;'):
                    continue                      # CAS; / NET; 等行不缓存
                parsed = _parse_str_line(text)
                if not parsed:
                    log_warning(f'跳过坏 STR 行: source={source["name"]}',
                                module='external_caster')
                    continue
                mount, str_line, lat, lon = parsed
                if mount in seen:
                    continue                      # 表内同名只取第一条，不让整表作废
                seen.add(mount)
                rows.append((mount, str_line, lat, lon))

            database.replace_external_mounts(source['id'], rows, source['priority'])
            interval = _jitter(config.EXTERNAL_STR_REFRESH if not demand
                               else config.EXTERNAL_STR_REFRESH)
            database.update_external_fetch_state(source['id'], 0, time.time() + interval)
            database.update_external_status(source['id'], 'ok', '', time.time())
            log_info(f'STR fetch ok: source={source["name"]} entries={len(rows)}',
                     module='external_caster')
            return len(rows)
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    def _refresh_locked(self, source_id, mount):
        """按需 STR 刷新的单飞 + 冷却 + 每小时≤2次 + 负缓存（调用方持锁）"""
        now = time.time()
        if self._negative_cache.get(mount, 0) > now:
            return False
        if source_id in self._demand_inflight:
            return False
        src = database.get_external_caster(source_id)
        if not src or not src['enabled']:
            return False
        if now - (src['last_demand_refresh_at'] or 0) < config.EXTERNAL_STR_DEMAND_COOLDOWN:
            return False
        hist = self._demand_history.setdefault(source_id, deque())
        while hist and now - hist[0] > 3600:
            hist.popleft()
        if len(hist) >= 2:
            return False
        hist.append(now)
        self._demand_inflight.add(source_id)
        database.update_external_fetch_state(
            source_id, 0, None, demand_refresh_at=now)
        return True

    def request_str_refresh(self, source_id, mount, force=False):
        """relay 确认上游挂载点不存在时调用；401/403/超时不得调用此方法。"""
        with self._lock:
            if force:
                now = time.time()
                if source_id in self._demand_inflight:
                    return False
                self._demand_inflight.add(source_id)
                database.update_external_fetch_state(
                    source_id, 0, None, demand_refresh_at=now)
            elif not self._refresh_locked(source_id, mount):
                return False

        def worker():
            src = database.get_external_caster(source_id)
            try:
                if src:
                    # 单飞：若定时 fetcher 正在拉同一张表，放弃本次按需刷新，
                    # 避免两个线程同时打上游。
                    self._try_fetch(src, demand=True)
            except Exception as e:
                log_warning(f'按需 STR 刷新失败: {e}', module='external_caster')
            finally:
                with self._lock:
                    self._demand_inflight.discard(source_id)

        threading.Thread(target=worker, name=f'ext-demand-{source_id}',
                         daemon=True).start()
        return True

    def force_refresh(self, source_id):
        return self.request_str_refresh(source_id, '', force=True)

    # ---------- 供 connection.py 使用的融合数据 ----------

    def get_fused_rows(self):
        """按外部源优先级返回去重后的 STR 行（本地优先由 connection 侧负责）"""
        rows = {}
        for src in database.list_external_casters(enabled_only=True):
            for row in database.get_external_mounts(src['id']):
                mount = row['mount']
                if not _valid_mount(mount):
                    continue
                prev = rows.get(mount)
                if prev is None or src['priority'] > prev['priority']:
                    rows[mount] = {
                        'mount': mount, 'str_line': row['str_line'],
                        'lat': row['lat'], 'lon': row['lon'],
                        'priority': src['priority'],
                        'source_id': src['id'], 'source_name': src['name'],
                    }
        return list(rows.values())

    def is_mount_available(self, mount):
        return _valid_mount(mount) and database.get_external_mount(mount) is not None

    # ---------- relay 按需启停 ----------

    def acquire(self, mount, holder):
        """订阅外部挂载点；holder 形如 ntrip:<cid> / web:<cid>，同一 holder 幂等"""
        if not config.EXTERNAL_CASTER_ENABLED or not _valid_mount(mount):
            return False
        new_relay = None
        with self._lock:
            relay = self._relays.get(mount)
            if relay and relay.state != 'closed':
                if holder in relay.holders:
                    return True
                if len(relay.holders) >= config.EXTERNAL_MAX_SUBSCRIBERS_PER_MOUNT:
                    log_warning(f'relay 订阅者超限: mount={mount}', module='external_caster')
                    return False
                # 新的订阅取消了上一次「最后一个订阅者离开」排定的宽限关闭。
                # 不清空的话，旧 grace 线程会看到 refs>0 而自行退出，
                # 后来再归零时又因 grace_thread 非空而不重排，relay 永久泄漏。
                relay.grace_thread = None
                relay.holders.add(holder)
                relay.refs += 1
                return True

            entry = database.get_external_mount(mount)
            if not entry:
                return False
            src = database.get_external_caster(entry['source_id'])
            if not src or not src['enabled']:
                return False
            per_source = self._source_relay_count.get(src['id'], 0)
            if per_source >= config.EXTERNAL_MAX_RELAYS_PER_SOURCE:
                log_warning(f'单源 relay 超限: source={src["name"]}',
                            module='external_caster')
                return False

            # 全局槽位在锁内非阻塞占用；失败不能留下任何本地状态
            if not hasattr(self, '_relay_slots'):
                self._relay_slots = threading.BoundedSemaphore(
                    config.EXTERNAL_MAX_RELAYS)
            if not self._relay_slots.acquire(blocking=False):
                log_warning('全局 relay 槽位耗尽', module='external_caster')
                return False

            account = None
            if config.EXTERNAL_ACCOUNT_POOL:
                account = database.take_account(
                    src['id'], f'relay:{mount}', config.EXTERNAL_ACCOUNT_LEASE_TTL)
            profile = self._choose_profile(account)
            relay = RelayState(mount, src, account, profile, None, None)
            relay.holders.add(holder)
            relay.refs = 1
            self._relays[mount] = relay
            self._source_relay_count[src['id']] = per_source + 1
            self._stats.setdefault(mount, {
                'data_count': 0, 'total_bytes': 0, 'data_rate': 0.0,
                'last_update': 0.0, 'window': deque()})
            new_relay = (relay, src, account)

        # 锁外建连，避免阻塞其他订阅
        relay, src, account = new_relay
        reason = 'upstream_error'
        try:
            sock, reader, status, _headers, profile = self._connect(src, mount, account)
            if status in (404, 410):
                reason = 'mount_missing'
                # 上游明确说没有这个挂载点：受控刷新一次 STR（带冷却/单飞）
                self.request_str_refresh(src['id'], mount)
                raise _FetchError(f'HTTP {status}', status)
            if status in (401, 403, 406):
                reason = 'auth_failed'
                if status in (403, 406):
                    self._on_agent_rejected(relay.profile_id)
                raise _FetchError(f'HTTP {status}', status)
            if status != 200:
                raise _FetchError(f'HTTP {status}', status)
        except Exception as e:
            relay.last_error = str(e)
            self._close_relay(mount, reason)
            if reason != 'mount_missing':
                log_warning(f'relay 建连失败: mount={mount} error={e}',
                            module='external_caster')
            return False

        with self._lock:
            relay.sock = sock
            relay.reader = reader
            relay.state = 'running'
            relay.last_data_at = time.time()
            if profile:
                relay.profile_id = profile['id']
                relay.profile_string = profile['agent_string']

        recv_t = threading.Thread(target=self._relay_recv_loop, args=(relay,),
                                  name=f'ext-relay-{mount[:32]}', daemon=True)
        relay.recv_thread = recv_t
        recv_t.start()
        if config.EXTERNAL_GGA_FORWARD:
            gga_t = threading.Thread(target=self._gga_loop, args=(relay,),
                                     name=f'ext-gga-{mount[:32]}', daemon=True)
            relay.gga_thread = gga_t
            gga_t.start()
        if relay.account_id:
            lease_t = threading.Thread(target=self._lease_loop, args=(relay,),
                                       name=f'ext-lease-{relay.account_id}', daemon=True)
            relay.lease_thread = lease_t
            lease_t.start()
        log_info(f'relay start: mount={mount} source={src["name"]} '
                 f'account={relay.account_id} agent={relay.profile_string}',
                 module='external_caster')
        return True

    def release(self, mount, holder):
        """最后一个订阅者离开后，宽限 EXTERNAL_RELAY_GRACE 秒再关闭上游"""
        with self._lock:
            relay = self._relays.get(mount)
            if not relay or holder not in relay.holders:
                return False
            relay.holders.discard(holder)
            relay.refs -= 1
            if relay.refs > 0:
                return True
            if relay.grace_thread is not None:
                return True

            def grace():
                time.sleep(max(0, config.EXTERNAL_RELAY_GRACE))
                with self._lock:
                    cur = self._relays.get(mount)
                    if cur is relay and relay.refs == 0:
                        pass
                    else:
                        return
                self._close_relay(mount, 'idle_release')

            t = threading.Thread(target=grace, name=f'ext-grace-{mount[:32]}',
                                 daemon=True)
            relay.grace_thread = t
            t.start()
            return True

    def _close_relay(self, mount, reason='closed'):
        with self._lock:
            relay = self._relays.pop(mount, None)
            if not relay:
                return
            count = max(0, self._source_relay_count.get(relay.source_id, 1) - 1)
            if count:
                self._source_relay_count[relay.source_id] = count
            else:
                self._source_relay_count.pop(relay.source_id, None)
        relay.stop_event.set()
        if relay.sock:
            try:
                relay.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                relay.sock.close()
            except OSError:
                pass
        for t in (relay.recv_thread, relay.gga_thread, relay.lease_thread):
            if t and t is not threading.current_thread():
                t.join(timeout=1)
        if relay.account_id and not relay.released:
            database.release_account(relay.account_id, f'relay:{mount}')
        if not relay.released:
            relay.released = True
            try:
                self._relay_slots.release()
            except ValueError:
                pass
        relay.state = 'closed'
        log_info(f'relay stop: mount={mount} reason={reason}', module='external_caster')

    def _relay_recv_loop(self, relay):
        mount = relay.mount
        try:
            # _SockReader 在读 HTTP 头时可能已预读到 RTCM 字节，先原样交出去
            initial = relay.reader.buf if relay.reader else b''
            if initial:
                relay.reader.buf = b''
                relay.last_data_at = time.time()
                self.record_data(mount, len(initial))
                forwarder.upload_data(mount, initial)
            while not relay.stop_event.is_set() and relay.sock:
                try:
                    chunk = relay.sock.recv(8192)
                except socket.timeout:
                    if time.time() - relay.last_data_at > config.EXTERNAL_RELAY_IDLE:
                        log_info(f'relay idle timeout: mount={mount}',
                                 module='external_caster')
                        break
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                relay.last_data_at = time.time()
                self.record_data(mount, len(chunk))
                # 唯一入料口：绝不在这里调用 forwarder.add_client()
                forwarder.upload_data(mount, chunk)
        finally:
            self._close_relay(mount, 'upstream_closed')

    def _lease_loop(self, relay):
        ttl = max(5, config.EXTERNAL_ACCOUNT_LEASE_TTL)
        period = max(2, ttl // 3)
        while not relay.stop_event.wait(timeout=period):
            if not database.renew_account(
                    relay.account_id, f'relay:{relay.mount}', ttl):
                log_warning(f'账号租约续期失败，关闭 relay: mount={relay.mount}',
                            module='external_caster')
                self._close_relay(relay.mount, 'lease_lost')
                return

    # ---------- GGA 上行（默认关闭） ----------

    def feed_gga(self, mount, holder, data):
        """由 ntrip.py 的读线程调用；只收 GGA，非 GGA 立即忽略，不滞留缓冲"""
        if not config.EXTERNAL_GGA_FORWARD or not data:
            return 0
        with self._lock:
            relay = self._relays.get(mount)
        if not relay or relay.state != 'running' or relay.stop_event.is_set():
            return 0
        accepted = 0
        for m in GGA_LINE_RE.finditer(data):
            payload, crc_hex = m.group(1), m.group(2)
            try:
                expected = int(crc_hex, 16)
            except ValueError:
                continue
            if _nmea_checksum_ok(payload) != expected:
                continue
            line = b'$' + payload + b'*' + crc_hex + b'\r\n'
            with self._lock:
                # 默认锁定首个有效 GGA 来源，避免后加入客户端污染上游选站；
                # 位置共享开启时允许显式切换（状态 API 可见 gga_source_tag）。
                if relay.gga_source_tag and relay.gga_source_tag != holder \
                        and not config.EXTERNAL_LOCATION_SHARE:
                    continue
                relay.gga_line = line
                relay.gga_source_tag = holder
            accepted += 1
        return accepted

    def _gga_loop(self, relay):
        interval = max(1, config.EXTERNAL_GGA_INTERVAL)
        while not relay.stop_event.is_set():
            time.sleep(interval)
            line = relay.gga_line
            if not line or not relay.sock:
                continue
            try:
                with relay.send_lock:
                    relay.sock.sendall(line)
                relay.gga_sent_at = time.time()
            except OSError:
                self._close_relay(relay.mount, 'gga_send_failed')
                return

    # ---------- 统计与状态 ----------

    def record_data(self, mount, size):
        with self._lock:
            s = self._stats.setdefault(mount, {
                'data_count': 0, 'total_bytes': 0, 'data_rate': 0.0,
                'last_update': 0.0, 'window': deque()})
            now = time.time()
            s['data_count'] += 1
            s['total_bytes'] += size
            s['last_update'] = now
            s['window'].append((now, size))
            while s['window'] and now - s['window'][0][0] > 10:
                s['window'].popleft()
            s['data_rate'] = sum(x[1] for x in s['window']) / 10.0

    def get_stats(self, mount):
        with self._lock:
            s = self._stats.get(mount)
            if not s:
                if self.is_mount_available(mount):
                    # 尚未 relay 过的外部站也要返回对齐的零值，避免前端显示空统计
                    return {'data_count': 0, 'total_bytes': 0,
                            'data_rate': 0.0, 'last_update': 0.0}
                return None
            return {'data_count': s['data_count'],
                    'total_bytes': s['total_bytes'],
                    'data_rate': round(s['data_rate'], 2),
                    'last_update': s['last_update']}

    def get_relay_status(self):
        with self._lock:
            result = {}
            for mount, relay in self._relays.items():
                result[mount] = {
                    'state': relay.state,
                    'source_id': relay.source_id,
                    'account_id': relay.account_id,
                    'agent_profile': relay.profile_string,
                    'gga_source_tag': relay.gga_source_tag,
                    'gga_time': relay.gga_sent_at,
                    'refs': relay.refs,
                    'holders': sorted(relay.holders),
                    'last_data_at': relay.last_data_at,
                    'last_error': relay.last_error,
                }
            return result

    def shutdown_all(self):
        with self._lock:
            mounts = list(self._relays.keys())
        for mount in mounts:
            self._close_relay(mount, 'shutdown')


class _FetchError(Exception):
    def __init__(self, message, http_status=None):
        super().__init__(message)
        self.http_status = http_status


# 单例（模块级，避免在 import 期启动任何线程）
_manager = None


def get_manager():
    global _manager
    if _manager is None:
        _manager = ExternalCasterManager()
    return _manager
