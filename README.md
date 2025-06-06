# 2RTK NTRIP Caster

2RTK是一个基于Python实现的NTRIP Caster服务器，提供RTK差分数据的转发服务和Web管理界面。

## 功能特性

### NTRIP服务
- 支持NTRIP v1.0和v2.0协议
- 支持多挂载点管理
- 实时数据流转发
- 用户认证和访问控制
- 支持多客户端并发连接

### Web管理界面
- 系统状态监控（CPU、内存使用率）
- 用户管理（添加/删除/修改）
- 挂载点管理（添加/删除/修改）
- 在线用户和活动挂载点监控
- 管理员密码修改

## 系统要求

- Python 3.x
- 依赖包：
  - Flask
  - psutil
  - sqlite3

## 安装说明

1. 克隆项目代码
2. 安装依赖：
```bash
pip install flask psutil
```

## 配置说明

主要配置参数（在2rtk.py中）：
- `HOST`: 服务器监听地址（默认：0.0.0.0）
- `NTRIP_PORT`: NTRIP服务端口（默认：2101）
- `WEB_PORT`: Web管理界面端口（默认：5757）
- `DEBUG`: 调试模式开关

## 使用说明

### 启动服务器
```bash
python 2rtk.py
```

### Web管理界面
1. 访问 `http://your-server-ip:5757`
2. 默认管理员账号：
   - 用户名：admin
   - 密码：admin
   
### NTRIP服务
- 基准站连接：`your-server-ip:2101`
- 移动站连接：`your-server-ip:2101/mount-point`

## 安全建议

1. 首次登录后立即修改默认管理员密码
2. 定期更新用户密码
3. 使用强密码策略
4. 及时更新系统和依赖包

## 版本信息

当前版本：1.9.8

## 联系方式

邮箱：i@jia.by

## 许可证

MIT License

Copyright (c) 2024 2RTK

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.