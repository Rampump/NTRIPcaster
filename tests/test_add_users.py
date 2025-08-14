#!/usr/bin/env python3
"""
用户批量添加测试脚本
功能：通过Web API添加500个测试用户
"""

import requests
import json
import time
import sys

# 服务器配置
WEB_SERVER_URL = "http://192.168.1.4:5757"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

def login_admin():
    """管理员登录"""
    login_url = f"{WEB_SERVER_URL}/api/login"
    login_data = {
        "username": ADMIN_USERNAME,
        "password": ADMIN_PASSWORD
    }
    
    try:
        response = requests.post(login_url, json=login_data, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                print(f"管理员登录成功: {ADMIN_USERNAME}")
                return response.cookies
            else:
                print(f"登录失败: {result.get('message', '未知错误')}")
                return None
        else:
            print(f"登录请求失败，状态码: {response.status_code}")
            return None
    except Exception as e:
        print(f"登录异常: {e}")
        return None

def add_user(cookies, username, password):
    """添加单个用户"""
    add_user_url = f"{WEB_SERVER_URL}/api/users"
    user_data = {
        "username": username,
        "password": password
    }
    
    try:
        response = requests.post(add_user_url, json=user_data, cookies=cookies, timeout=10)
        if response.status_code in [200, 201]:  # 接受200和201状态码
            result = response.json()
            return result.get('success', False), result.get('message', '')
        else:
            return False, f"HTTP {response.status_code}"
    except Exception as e:
        return False, str(e)

def main():
    """主函数"""
    print("开始批量添加用户测试...")
    print(f"目标服务器: {WEB_SERVER_URL}")
    print(f"管理员账户: {ADMIN_USERNAME}")
    print("="*50)
    
    # 管理员登录
    cookies = login_admin()
    if not cookies:
        print("管理员登录失败，退出程序")
        sys.exit(1)
    
    # 批量添加用户
    total_users = 500
    success_count = 0
    failed_count = 0
    
    print(f"开始添加 {total_users} 个用户...")
    start_time = time.time()
    
    for i in range(1, total_users + 1):
        # 生成有规律的用户名和密码
        username = f"testuser{i:03d}"  # testuser001, testuser002, ..., testuser500
        password = f"pass{i:03d}"      # pass001, pass002, ..., pass500
        
        success, message = add_user(cookies, username, password)
        
        if success:
            success_count += 1
            if i % 50 == 0:  # 每50个用户显示一次进度
                print(f"已成功添加 {success_count} 个用户 (进度: {i}/{total_users})")
        else:
            failed_count += 1
            print(f"添加用户 {username} 失败: {message}")
        
        # 避免请求过于频繁
        time.sleep(0.01)
    
    end_time = time.time()
    elapsed_time = end_time - start_time
    
    print("="*50)
    print("用户添加完成！")
    print(f"总用户数: {total_users}")
    print(f"成功添加: {success_count}")
    print(f"添加失败: {failed_count}")
    print(f"耗时: {elapsed_time:.2f} 秒")
    print(f"平均速度: {total_users/elapsed_time:.2f} 用户/秒")
    
    # 保存用户信息到文件，供NTRIP测试使用
    user_list = []
    for i in range(1, total_users + 1):
        user_list.append({
            "username": f"testuser{i:03d}",
            "password": f"pass{i:03d}"
        })
    
    with open("test_users.json", "w", encoding="utf-8") as f:
        json.dump(user_list, f, indent=2, ensure_ascii=False)
    
    print(f"用户信息已保存到 test_users.json 文件")

if __name__ == "__main__":
    main()