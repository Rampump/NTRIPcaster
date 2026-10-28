#!/usr/bin/env python3

import sqlite3
import hashlib
import hmac
import secrets
import logging
import threading
from contextlib import contextmanager
from threading import Lock
from . import config
from . import logger
from .logger import log_debug, log_info, log_warning, log_error, log_critical, log_database_operation, log_authentication

db_lock = Lock()


class _ConnectionPool:
    """有界 SQLite 连接池

    原来每次鉴权/查询都 sqlite3.connect() + close()，一次取流鉴权要开 2~3 条连接，
    在 2000+ 并发下 connect 本身（打开文件、读 WAL 头、解析 schema）就成了可观的开销。
    这里复用连接，并在放回池时 rollback，避免事务状态残留。

    池容量取自 config.DB_POOL_SIZE；库路径变化（测试里会改 config.DATABASE_PATH）
    时自动丢弃旧连接，避免连到已删除的库文件。

    并发上限同样是 DB_POOL_SIZE：名额用完时开临时连接（用完即关），
    既不会阻塞鉴权，也不会让连接数无限膨胀。
    """

    def __init__(self, max_size=None):
        self.max_size = max_size
        self._free = []
        self._lock = Lock()
        self._path = None
        # 并发借出上限。原来的"有界"只限制了空闲队列长度，acquire 在队列空时
        # 无条件 sqlite3.connect()：一次连接风暴（比如 2000 个客户端同时重连）
        # 会瞬间开出上千条连接，每条都带自己的页缓存，内存直接冲上去。
        self._slots = None
        self._slots_cap = None
        self._ephemeral = set()   # 名额用尽时临时开的连接（id(conn) → 归还时直接关闭）

    def _semaphore(self):
        with self._lock:
            cap = self._capacity()
            if self._slots is None or self._slots_cap != cap:
                self._slots = threading.BoundedSemaphore(cap)
                self._slots_cap = cap
            return self._slots

    def _capacity(self):
        return self.max_size or max(4, int(getattr(config, 'DB_POOL_SIZE', 10) or 10))

    def _new_connection(self, path):
        conn = sqlite3.connect(
            path,
            timeout=getattr(config, 'DB_TIMEOUT', 30),
            check_same_thread=False,   # 连接在池中被不同线程轮流复用
        )
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(f"PRAGMA busy_timeout={int(getattr(config, 'DB_TIMEOUT', 30)) * 1000}")
        except sqlite3.DatabaseError as e:
            log_warning(f"设置连接 PRAGMA 失败（可能是不支持 WAL 的文件系统）: {e}")
        return conn

    def acquire(self):
        slots = self._semaphore()
        if not slots.acquire(timeout=1.0):
            # 名额用尽（连接风暴）：开一条临时连接保证功能不降级，
            # 但它在 release 时立即关闭，不会长期占着页缓存
            conn = self._new_connection(config.DATABASE_PATH)
            with self._lock:
                self._ephemeral.add(id(conn))
            return conn

        try:
            path = config.DATABASE_PATH
            with self._lock:
                if self._path != path:
                    # 库路径变了：旧连接全部作废
                    for conn in self._free:
                        try:
                            conn.close()
                        except sqlite3.Error:
                            pass
                    self._free.clear()
                    self._path = path

                while self._free:
                    conn = self._free.pop()
                    try:
                        conn.execute("SELECT 1")
                    except sqlite3.Error:
                        try:
                            conn.close()
                        except sqlite3.Error:
                            pass
                        continue
                    return conn

            return self._new_connection(path)
        except Exception:
            slots.release()
            raise

    def release(self, conn):
        if conn is None:
            return
        slots = self._semaphore()

        with self._lock:
            ephemeral = id(conn) in self._ephemeral
            if ephemeral:
                self._ephemeral.discard(id(conn))

        if ephemeral:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._release_slot(slots)
            return

        keep = False
        try:
            # 丢弃未提交事务；否则下个线程可能继承半截事务
            if conn.in_transaction:
                conn.rollback()
            with self._lock:
                if len(self._free) < self._capacity():
                    self._free.append(conn)
                    keep = True
        except sqlite3.Error:
            keep = False

        if not keep:
            try:
                conn.close()
            except sqlite3.Error:
                pass

        self._release_slot(slots)

    @staticmethod
    def _release_slot(slots):
        try:
            if slots is not None:
                slots.release()
        except ValueError:
            # 双重归还：只记录，不影响功能
            log_warning("SQLite 连接池归还次数异常（重复 release）")

    def close_all(self):
        with self._lock:
            for conn in self._free:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._free.clear()
            self._path = None


_pool = _ConnectionPool()


def _acquire():
    """从池中取一条连接；调用方负责 _release()"""
    return _pool.acquire()


def _release(conn):
    """归还连接（不会真正关闭，除非池已满/连接损坏）"""
    _pool.release(conn)


@contextmanager
def _connect():
    """with _connect() as conn: —— 成功提交、异常回滚、结束归还"""
    conn = _pool.acquire()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        _pool.release(conn)


def close_pool():
    """关闭池内所有空闲连接（测试/退出时用）"""
    _pool.close_all()

# 哨兵值：表示"本次更新不改变用户的挂载点绑定关系"。
# 之前用魔法字符串 'keep_current' 判断，调用方传 None 时会被当成"解绑"写入 NULL，
# 导致管理员只改个密码就把挂载点变成未绑定（进而任何注册用户都能推流）。
_UNSET = object()
KEEP_USER_BINDING = _UNSET


def hash_password(password, salt=None):
    """使用PBKDF2和SHA256哈希密码"""
    if salt is None:
        salt = secrets.token_hex(16)  
    
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 10000)
    return f"{salt}${key.hex()}"

def verify_password(stored_password, provided_password):
    """验证密码是否匹配（常量时间比较，避免时序侧信道）"""
    if not stored_password or provided_password is None:
        return False
    
    if '$' not in stored_password:
        # 兼容遗留明文密码；匹配成功时调用方可据此触发一次哈希迁移
        return hmac.compare_digest(stored_password, str(provided_password))
        
    salt, hash_value = stored_password.split('$', 1)
    
    key = hashlib.pbkdf2_hmac('sha256', provided_password.encode(), salt.encode(), 10000)
    
    return hmac.compare_digest(key.hex(), hash_value)

def init_db():
    """初始化SQLite数据库表结构"""
    with db_lock:
        conn = _acquire()
        c = conn.cursor()

        # 开启 WAL：读不阻塞写，显著缓解"每连接一次建连 + 全局串行锁"下的延迟尖刺
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute(f"PRAGMA busy_timeout={int(getattr(config, 'DB_TIMEOUT', 30)) * 1000}")
        except sqlite3.DatabaseError as e:
            log_warning(f"启用 WAL 失败（可能是不支持的文件系统）: {e}")

        # 管理员表
        c.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
        ''')
        
        # 用户表（NTRIP客户端用户）
        c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
        ''')
        
        # 挂载点表
        c.execute('''
        CREATE TABLE IF NOT EXISTS mounts (
            id INTEGER PRIMARY KEY,
            mount TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            user_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
                ON DELETE SET NULL
                ON UPDATE CASCADE
        )
        ''')
        
        c.execute("SELECT * FROM admins")
        if not c.fetchone():
            # 使用哈希密码存储默认管理员密码
            admin_username = config.DEFAULT_ADMIN['username']
            admin_password = config.DEFAULT_ADMIN['password']
            hashed_password = hash_password(admin_password)
            c.execute("INSERT INTO admins (username, password) VALUES (?, ?)", (admin_username, hashed_password))
            print(f"已创建默认管理员: {admin_username}/{admin_password}（请首次登录后立即修改）")
            log_warning("检测到使用默认管理员口令，请登录后立刻修改密码，并在 config.ini 中设置强口令")
        
        conn.commit()
        _release(conn)
        log_info('数据库初始化完成')

def verify_mount_and_user(mount, username=None, password=None, mount_password=None, protocol_version="1.0"):
    """验证挂载点和用户信息是否合法
    
    Args:
        mount: 挂载点名称
        username: 用户名（可选）
        password: 用户密码（可选）
        mount_password: 挂载点密码（可选）
    """
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        
        try:
            # 检查挂载点是否存在并获取相关信息
            c.execute("SELECT id, password, user_id FROM mounts WHERE mount = ?", (mount,))
            mount_result = c.fetchone()
            
            if not mount_result:
                log_authentication(username or 'unknown', mount, False, 'database', '挂载点不存在')
                return False, "挂载点不存在"
            
            mount_id, stored_mount_password, bound_user_id = mount_result
            
            # 根据协议版本进行不同的验证逻辑
            if protocol_version == "2.0":
                
                if not username or not password:
                    log_authentication(username or 'unknown', mount, False, 'database', 'NTRIP 2.0需要用户名和密码')
                    return False, "NTRIP 2.0协议需要提供用户名和密码"
                
                # 验证用户是否存在
                c.execute("SELECT id, password FROM users WHERE username = ?", (username,))
                user_result = c.fetchone()
                if not user_result:
                    log_authentication(username, mount, False, 'database', '用户不存在')
                    return False, "用户不存在"
                
                user_id, stored_user_password = user_result
                
                # 验证用户密码
                if not verify_password(stored_user_password, password):
                    log_authentication(username, mount, False, 'database', '用户密码错误')
                    return False, "用户密码错误"
                
                # 验证挂载点是否绑定到该用户
                if bound_user_id is not None and bound_user_id != user_id:
                    log_authentication(username, mount, False, 'database', '用户无权限访问该挂载点')
                    return False, "用户无权限访问该挂载点"
                
                # NTRIP 2.0 不验证挂载点密码，只验证用户名和密码以及挂着的所属权限
                log_authentication(username, mount, True, 'database', 'NTRIP 2.0认证成功')
                return True, "NTRIP 2.0认证成功"
            
            else:
                # NTRIP 1.0 及以下版本验证逻辑
                if not mount_password:
                    log_authentication(username or 'unknown', mount, False, 'database', 'NTRIP 1.0需要挂载点密码')
                    return False, "NTRIP 1.0协议需要提供挂载点密码"
                
                # 验证挂载点密码（恒定时间比较，兼容历史明文）
                if not verify_password(stored_mount_password, mount_password):
                    log_authentication(username or 'unknown', mount, False, 'database', '挂载点密码错误')
                    return False, "挂载点密码错误"
                
                # NTRIP 1.0 只验证挂载点和挂载点密码，不验证用户
                log_authentication(username or 'unknown', mount, True, 'database', 'NTRIP 1.0认证成功')
                return True, "NTRIP 1.0认证成功"
            
        except Exception as e:
            log_error(f"用户认证异常: {e}", exc_info=True)
            return False, f"认证异常: {e}"
        finally:
            _release(conn)



def add_user(username, password):
    """添加新用户到数据库"""
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
            # 检查用户是否已存在
            c.execute("SELECT * FROM users WHERE username = ?", (username,))
            if c.fetchone():
                return False, "用户名已存在"
            
            # 哈希密码并添加用户
            hashed_password = hash_password(password)
            c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
            conn.commit()
            log_database_operation('add_user', 'users', True, f'用户: {username}')
            return True, "用户添加成功"
        except Exception as e:
            log_database_operation('add_user', 'users', False, str(e))
            return False, f"添加用户失败: {e}"
        finally:
            _release(conn)

def update_user(user_id, username, password):
    """更新用户信息

    password=None 表示"只改用户名、保留原口令"。
    原来调用方传的是库里的哈希串，verify_password(哈希, 哈希) 必然为 False，
    于是又对哈希串做了一次 hash_password，用户改名后口令就直接失效了。
    """
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
            # 检查用户名是否与其他用户冲突
            c.execute("SELECT * FROM users WHERE username = ? AND id != ?", (username, user_id))
            if c.fetchone():
                return False, "用户名已存在"
            
            c.execute("SELECT password FROM users WHERE id = ?", (user_id,))
            row = c.fetchone()
            if not row:
                return False, "用户不存在"
            old_password = row[0]

            if password is None:
                new_password = old_password
            elif '$' in old_password and verify_password(old_password, password):
                new_password = old_password
            else:
                new_password = hash_password(password)
            
            c.execute("UPDATE users SET username = ?, password = ? WHERE id = ?", (username, new_password, user_id))
            conn.commit()
            log_database_operation('update_user', 'users', True, f'用户: {username}')
            return True, "用户更新成功"
        except Exception as e:
            log_database_operation('update_user', 'users', False, str(e))
            return False, f"更新用户失败: {e}"
        finally:
            _release(conn)

def delete_user(user_id):
    """删除用户"""
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
            
            c.execute("SELECT username FROM users WHERE id = ?", (user_id,))
            result = c.fetchone()
            if not result:
                return False, "用户不存在"
            
            username = result[0]
            
            # 先清除所有绑定到该用户的挂载点的user_id
            c.execute("UPDATE mounts SET user_id = NULL WHERE user_id = ?", (user_id,))
            affected_mounts = c.rowcount
            
            # 删除用户
            c.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            
            log_message = f'用户: {username}'
            if affected_mounts > 0:
                log_message += f', 同时清除了 {affected_mounts} 个挂载点的用户绑定'
            
            log_database_operation('delete_user', 'users', True, log_message)
            return True, username
        except Exception as e:
            log_database_operation('delete_user', 'users', False, str(e))
            return False, f"删除用户失败: {e}"
        finally:
            _release(conn)

def get_all_users():
    """获取所有用户列表"""
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
            c.execute("SELECT id, username, password FROM users")
            return c.fetchall()
        finally:
            _release(conn)

def update_user_password(username, new_password):
    """更新用户密码"""
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
            
            c.execute("SELECT id FROM users WHERE username = ?", (username,))
            result = c.fetchone()
            if not result:
                return False, "用户不存在"
            
            
            hashed_password = hash_password(new_password)
            
            c.execute("UPDATE users SET password = ? WHERE username = ?", (hashed_password, username))
            conn.commit()
            log_info(f"用户 {username} 密码更新成功")
            return True, "密码更新成功"
        except Exception as e:
            log_error(f"更新用户密码失败: {e}")
            return False, f"更新密码失败: {e}"
        finally:
            _release(conn)

def add_mount(mount, password, user_id=None):
    """添加新挂载点"""
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
           
            c.execute("SELECT * FROM mounts WHERE mount = ?", (mount,))
            if c.fetchone():
                return False, "挂载点名称已存在"
            
            # 如果指定了用户ID，验证用户是否存在
            if user_id is not None:
                c.execute("SELECT id FROM users WHERE id = ?", (user_id,))
                if not c.fetchone():
                    return False, "指定的用户不存在"
            
            # 挂载点口令与用户口令同样按 PBKDF2 存储；
            # 校验端使用 verify_password()，兼容历史明文行（无 '$' 时走常量时间比较）
            c.execute("INSERT INTO mounts (mount, password, user_id) VALUES (?, ?, ?)",
                      (mount, hash_password(password), user_id))
            conn.commit()
            log_database_operation('add_mount', 'mounts', True, f'挂载点: {mount}, 用户ID: {user_id}')
            return True, "挂载点添加成功"
        except Exception as e:
            log_database_operation('add_mount', 'mounts', False, str(e))
            return False, f"添加挂载点失败: {e}"
        finally:
            _release(conn)

def update_mount(mount_id, mount=None, password=None, user_id=KEEP_USER_BINDING):
    """更新挂载点信息

    user_id 语义：
      - 不传 / 传 KEEP_USER_BINDING → 保持原绑定不变
      - 传 None                     → 明确解绑
      - 传整数                       → 绑定到该用户
    """
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
            
            c.execute("SELECT mount, password, user_id FROM mounts WHERE id = ?", (mount_id,))
            result = c.fetchone()
            if not result:
                return False, "挂载点不存在"
            
            old_mount, old_password, old_user_id = result
            
            
            new_mount = mount if mount is not None else old_mount
            new_password = hash_password(password) if password is not None else old_password
            new_user_id = old_user_id if user_id is KEEP_USER_BINDING else user_id
            
            # 检查挂载点名称是否与其他挂载点冲突
            if mount is not None and mount != old_mount:
                c.execute("SELECT * FROM mounts WHERE mount = ? AND id != ?", (mount, mount_id))
                if c.fetchone():
                    return False, "挂载点名称已存在"
            # 如果指定了用户ID，验证用户是否存在
            if new_user_id is not None:
                c.execute("SELECT id FROM users WHERE id = ?", (new_user_id,))
                if not c.fetchone():
                    return False, "指定的用户不存在"
            
            c.execute("UPDATE mounts SET mount = ?, password = ?, user_id = ? WHERE id = ?", (new_mount, new_password, new_user_id, mount_id))
            conn.commit()
            log_database_operation('update_mount', 'mounts', True, f'挂载点: {old_mount} -> {new_mount}')
            return True, old_mount
        except Exception as e:
            log_database_operation('update_mount', 'mounts', False, str(e))
            return False, f"更新挂载点失败: {e}"
        finally:
            _release(conn)

def delete_mount(mount_id):
    """删除挂载点"""
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
            
            c.execute("SELECT mount FROM mounts WHERE id = ?", (mount_id,))
            result = c.fetchone()
            if not result:
                return False, "挂载点不存在"
            
            mount = result[0]
            c.execute("DELETE FROM mounts WHERE id = ?", (mount_id,))
            conn.commit()
            log_database_operation('delete_mount', 'mounts', True, f'挂载点: {mount}')
            return True, mount
        except Exception as e:
            logger.log_database_operation('delete_mount', 'mounts', False, str(e))
            return False, f"删除挂载点失败: {e}"
        finally:
            _release(conn)

def get_all_mounts():
    """获取所有挂载点列表"""
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
            c.execute("PRAGMA table_info(mounts)")
            columns = [column[1] for column in c.fetchall()]
            
            if 'lat' in columns and 'lon' in columns:
                c.execute("""SELECT m.id, m.mount, m.password, m.user_id, u.username, m.lat, m.lon
                             FROM mounts m 
                             LEFT JOIN users u ON m.user_id = u.id""")
            else:
                c.execute("""SELECT m.id, m.mount, m.password, m.user_id, u.username, NULL as lat, NULL as lon
                             FROM mounts m 
                             LEFT JOIN users u ON m.user_id = u.id""")
            return c.fetchall()
        finally:
            _release(conn)


def verify_admin(username, password):
    """验证管理员账号密码"""
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
            c.execute("SELECT password FROM admins WHERE username = ?", (username,))
            result = c.fetchone()
            if result and verify_password(result[0], password):
                return True
            return False
        finally:
            _release(conn)

def update_admin_password(username, new_password):
    """更新管理员密码"""
    with db_lock:
        conn = _acquire()
        c = conn.cursor()
        try:
            hashed_password = hash_password(new_password)
            c.execute("UPDATE admins SET password = ? WHERE username = ?", (hashed_password, username))
            if c.rowcount == 0:
                # 原来不检查 rowcount：账号不存在也会返回成功，设置页显示"修改成功"但什么都没写
                return False
            conn.commit()
            log_database_operation('update_admin_password', 'admins', True, f'管理员: {username}')
            return True
        except Exception as e:
            log_database_operation('update_admin_password', 'admins', False, str(e))
            return False
        finally:
            _release(conn)


class DatabaseManager:
    """数据库管理器类，包装数据库操作函数"""
    
    def __init__(self):
        """初始化数据库管理器"""
        pass
    
    def init_database(self):
        """初始化数据库"""
        return init_db()
    
    def verify_mount_and_user(self, mount, username=None, password=None, mount_password=None, protocol_version="1.0"):
        """验证挂载点和用户"""
        return verify_mount_and_user(mount, username, password, mount_password, protocol_version)
    
    def add_user(self, username, password):
        """添加用户"""
        return add_user(username, password)
    
    def update_user_password(self, username, new_password):
        """更新用户密码"""
        return update_user_password(username, new_password)
    
    def delete_user(self, username):
        """删除用户"""
        users = get_all_users()
        user_id = None
        for user in users:
            if user[1] == username:  # user[1] 是 username
                user_id = user[0]    # user[0] 是 id
                break
        
        if user_id is None:
            return False, "用户不存在"
        
        return delete_user(user_id)
    
    def get_all_users(self):
        """获取所有用户"""
        return get_all_users()
    
    def get_user_password(self, username):
        """获取用户密码（仅用于内部校验流程，切勿返回给前端）"""
        with _connect() as conn:
            c = conn.cursor()
            c.execute("SELECT password FROM users WHERE username = ?", (username,))
            result = c.fetchone()
            return result[0] if result else None
    
    def check_mount_exists_in_db(self, mount):
        """检查挂载点是否在数据库中存在"""
        with _connect() as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM mounts WHERE mount = ?", (mount,))
            return c.fetchone() is not None
    
    def verify_download_user(self, mount, username, password):
        """验证下载用户

        默认只校验用户名密码（兼容旧行为：所有有效用户可拉取全部挂载点）。
        若 config.DOWNLOAD_ENFORCE_USER_BINDING 为 True，则额外校验挂载点绑定关系，
        避免"绑定到 A 站的用户直接拉 B 站数据"。
        """
        with _connect() as conn:
            c = conn.cursor()
            
            c.execute("SELECT id, user_id FROM mounts WHERE mount = ?", (mount,))
            mount_result = c.fetchone()
            if not mount_result:
                logger.log_authentication(username, mount, False, 'database', '挂载点不存在')
                return False, "挂载点不存在"
            
            bound_user_id = mount_result[1]
            
            c.execute("SELECT id, password FROM users WHERE username = ?", (username,))
            user_result = c.fetchone()
            if not user_result:
                logger.log_authentication(username, mount, False, 'database', '用户不存在')
                return False, "用户不存在"
            
            user_id, stored_password = user_result
            
            if not verify_password(stored_password, password):
                logger.log_authentication(username, mount, False, 'database', '用户密码错误')
                return False, "用户密码错误"
            
            if getattr(config, 'DOWNLOAD_ENFORCE_USER_BINDING', False) \
                    and bound_user_id is not None and bound_user_id != user_id:
                logger.log_authentication(username, mount, False, 'database', '用户无权限访问该挂载点')
                return False, "用户无权限访问该挂载点"
            
            logger.log_authentication(username, mount, True, 'database', '下载认证成功')
            return True, "下载认证成功"
    
    def add_mount(self, mount, password=None, user_id=None):
        """添加挂载点"""
        return add_mount(mount, password, user_id)
    
    def update_mount_password(self, mount, new_password):
        """更新挂载点密码"""
        with db_lock:
            conn = _acquire()
            c = conn.cursor()
            try:
                c.execute("UPDATE mounts SET password = ? WHERE mount = ?", (hash_password(new_password), mount))
                if c.rowcount > 0:
                    conn.commit()
                    return True, "挂载点密码更新成功"
                else:
                    return False, "挂载点不存在"
            except Exception as e:
                return False, f"更新挂载点密码失败: {str(e)}"
            finally:
                _release(conn)
    
    def update_user(self, user_id, username, password):
        """更新用户信息"""
        return update_user(user_id, username, password)
    
    def update_mount(self, mount_id, mount=None, password=None, user_id=KEEP_USER_BINDING):
        """更新挂载点信息"""
        return update_mount(mount_id, mount, password, user_id)
    
    def delete_mount(self, mount):
        """删除挂载点"""
        mounts = self.get_all_mounts()
        mount_id = None
        for m in mounts:
            if m[1] == mount:  # m[1] 是挂载点名称
                mount_id = m[0]  # m[0] 是ID
                break
        
        if mount_id is None:
            return False, "挂载点不存在"
        
        return delete_mount(mount_id)
    
    def get_all_mounts(self):
        """获取所有挂载点"""
        return get_all_mounts()
       
    def verify_admin(self, username, password):
        """验证管理员"""
        return verify_admin(username, password)
    
    def update_admin_password(self, username, new_password):
        """更新管理员密码"""
        return update_admin_password(username, new_password)
    
