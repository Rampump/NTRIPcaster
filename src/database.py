#!/usr/bin/env python3

import sqlite3
import hashlib
import secrets
import logging
from threading import Lock
from . import config
from . import logger
from .logger import log_debug, log_info, log_warning, log_error, log_critical, log_database_operation, log_authentication

db_lock = Lock()


def hash_password(password, salt=None):
    """使用PBKDF2和SHA256哈希密码"""
    if salt is None:
        salt = secrets.token_hex(16)  
    
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 10000)
    return f"{salt}${key.hex()}"

def verify_password(stored_password, provided_password):
    """验证密码是否匹配"""
    
    if '$' not in stored_password:
       
        return stored_password == provided_password
        
    salt, hash_value = stored_password.split('$', 1)
    
    key = hashlib.pbkdf2_hmac('sha256', provided_password.encode(), salt.encode(), 10000)
    
    return key.hex() == hash_value

def init_db():
    """初始化SQLite数据库表结构"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()

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
            print(f"已创建默认管理员: {admin_username}/{admin_password}（请首次登录后修改）")
        
        conn.commit()
        conn.close()
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
        conn = sqlite3.connect(config.DATABASE_PATH)
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
                
                # 验证挂载点密码
                if stored_mount_password != mount_password:
                    log_authentication(username or 'unknown', mount, False, 'database', '挂载点密码错误')
                    return False, "挂载点密码错误"
                
                # NTRIP 1.0 只验证挂载点和挂载点密码，不验证用户
                log_authentication(username or 'unknown', mount, True, 'database', 'NTRIP 1.0认证成功')
                return True, "NTRIP 1.0认证成功"
            
        except Exception as e:
            log_error(f"用户认证异常: {e}", exc_info=True)
            return False, f"认证异常: {e}"
        finally:
            conn.close()



def add_user(username, password):
    """添加新用户到数据库"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
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
            conn.close()

def update_user(user_id, username, password):
    """更新用户信息"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            # 检查用户名是否与其他用户冲突
            c.execute("SELECT * FROM users WHERE username = ? AND id != ?", (username, user_id))
            if c.fetchone():
                return False, "用户名已存在"
            
            c.execute("SELECT password FROM users WHERE id = ?", (user_id,))
            old_password = c.fetchone()[0]
            
            if '$' in old_password and verify_password(old_password, password):
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
            conn.close()

def delete_user(user_id):
    """删除用户"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
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
            conn.close()

def get_all_users():
    """获取所有用户列表"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            c.execute("SELECT id, username, password FROM users")
            return c.fetchall()
        finally:
            conn.close()

def update_user_password(username, new_password):
    """更新用户密码"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
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
            conn.close()

def add_mount(mount, password, user_id=None):
    """添加新挂载点"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
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
            
            c.execute("INSERT INTO mounts (mount, password, user_id) VALUES (?, ?, ?)", (mount, password, user_id))
            conn.commit()
            log_database_operation('add_mount', 'mounts', True, f'挂载点: {mount}, 用户ID: {user_id}')
            return True, "挂载点添加成功"
        except Exception as e:
            log_database_operation('add_mount', 'mounts', False, str(e))
            return False, f"添加挂载点失败: {e}"
        finally:
            conn.close()

def update_mount(mount_id, mount=None, password=None, user_id=None):
    """更新挂载点信息"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            
            c.execute("SELECT mount, password, user_id FROM mounts WHERE id = ?", (mount_id,))
            result = c.fetchone()
            if not result:
                return False, "挂载点不存在"
            
            old_mount, old_password, old_user_id = result
            
            
            new_mount = mount if mount is not None else old_mount
            new_password = password if password is not None else old_password
            new_user_id = user_id if user_id != 'keep_current' else old_user_id
            
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
            conn.close()

def delete_mount(mount_id):
    """删除挂载点"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
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
            conn.close()

def get_all_mounts():
    """获取所有挂载点列表"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
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
            conn.close()


def verify_admin(username, password):
    """验证管理员账号密码"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            c.execute("SELECT password FROM admins WHERE username = ?", (username,))
            result = c.fetchone()
            if result and verify_password(result[0], password):
                return True
            return False
        finally:
            conn.close()

def update_admin_password(username, new_password):
    """更新管理员密码"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            hashed_password = hash_password(new_password)
            c.execute("UPDATE admins SET password = ? WHERE username = ?", (hashed_password, username))
            conn.commit()
            log_database_operation('update_admin_password', 'admins', True, f'管理员: {username}')
            return True
        except Exception as e:
            log_database_operation('update_admin_password', 'admins', False, str(e))
            return False
        finally:
            conn.close()


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
        """获取用户密码，用于Digest认证"""
        with sqlite3.connect(config.DATABASE_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT password FROM users WHERE username = ?", (username,))
            result = c.fetchone()
            return result[0] if result else None
    
    def check_mount_exists_in_db(self, mount):
        """检查挂载点是否在数据库中存在"""
        with sqlite3.connect(config.DATABASE_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM mounts WHERE mount = ?", (mount,))
            return c.fetchone() is not None
    
    def verify_download_user(self, mount, username, password):
        """验证下载用户，只验证用户名密码，不验证挂载点绑定关系"""
        with sqlite3.connect(config.DATABASE_PATH) as conn:
            c = conn.cursor()
            
            c.execute("SELECT id FROM mounts WHERE mount = ?", (mount,))
            mount_result = c.fetchone()
            if not mount_result:
                logger.log_authentication(username, mount, False, 'database', '挂载点不存在')
                return False, "挂载点不存在"
            
            c.execute("SELECT id, password FROM users WHERE username = ?", (username,))
            user_result = c.fetchone()
            if not user_result:
                logger.log_authentication(username, mount, False, 'database', '用户不存在')
                return False, "用户不存在"
            
            user_id, stored_password = user_result
            
            if not verify_password(stored_password, password):
                logger.log_authentication(username, mount, False, 'database', '用户密码错误')
                return False, "用户密码错误"
            
           
            logger.log_authentication(username, mount, True, 'database', '下载认证成功')
            return True, "下载认证成功"
    
    def add_mount(self, mount, password=None, user_id=None):
        """添加挂载点"""
        return add_mount(mount, password, user_id)
    
    def update_mount_password(self, mount, new_password):
        """更新挂载点密码"""
        with db_lock:
            conn = sqlite3.connect(config.DATABASE_PATH)
            c = conn.cursor()
            try:
                c.execute("UPDATE mounts SET password = ? WHERE mount = ?", (new_password, mount))
                if c.rowcount > 0:
                    conn.commit()
                    return True, "挂载点密码更新成功"
                else:
                    return False, "挂载点不存在"
            except Exception as e:
                return False, f"更新挂载点密码失败: {str(e)}"
            finally:
                conn.close()
    
    def update_user(self, user_id, username, password):
        """更新用户信息"""
        return update_user(user_id, username, password)
    
    def update_mount(self, mount_id, mount=None, password=None, user_id=None):
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
    
