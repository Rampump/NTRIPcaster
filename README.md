# 2RTK NTRIP Caster

2RTK是一个基于Python开发的NTRIP Caster服务器实现，支持RTK基准站数据的转发和管理。

## 功能特点

- NTRIP协议支持
  - 基准站数据上传
  - 用户数据下载
  - 挂载点管理
  - 用户认证

- Web管理界面
  - 用户管理
  - 挂载点管理
  - 系统监控
  - 管理员密码修改

- 系统集成
  - Systemd服务支持
  - 开机自启动
  - 日志管理
  - 权限控制

## 系统要求

- Ubuntu 18.04 或更高版本
- Python 3.6 或更高版本
- 2101端口（NTRIP服务）
- 5757端口（Web管理界面）

## 快速安装

1. 克隆仓库：
```bash
git clone https://gitcode.com/your-username/2rtk.git
cd 2rtk
```

2. 运行安装脚本：
```bash
sudo chmod +x install.sh
sudo ./install.sh
```

安装脚本会自动完成以下操作：
- 安装系统依赖
- 创建服务用户和必要目录
- 安装Python依赖
- 配置系统服务
- 设置防火墙规则
- 启动服务

## 手动安装

如果你不想使用自动安装脚本，也可以按照以下步骤手动安装：

1. 安装系统依赖：
```bash
sudo apt update
sudo apt install python3 python3-pip git
```

2. 创建服务用户和目录：
```bash
sudo useradd -r -s /bin/false 2rtk
sudo mkdir -p /opt/2rtk
sudo mkdir -p /var/log/2rtk
```

3. 复制项目文件：
```bash
sudo cp -r ./* /opt/2rtk/
```

4. 设置权限：
```bash
sudo chown -R 2rtk:2rtk /opt/2rtk
sudo chown -R 2rtk:2rtk /var/log/2rtk
sudo chmod 755 /opt/2rtk
sudo chmod 755 /var/log/2rtk
```

5. 安装Python依赖：
```bash
sudo pip3 install -r requirements.txt
```

6. 配置系统服务：
```bash
sudo cp 2rtk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable 2rtk
sudo systemctl start 2rtk
```

## 服务管理

使用以下命令管理2RTK服务：

```bash
# 启动服务
sudo systemctl start 2rtk

# 停止服务
sudo systemctl stop 2rtk

# 重启服务
sudo systemctl restart 2rtk

# 查看服务状态
sudo systemctl status 2rtk

# 启用开机自启
sudo systemctl enable 2rtk

# 禁用开机自启
sudo systemctl disable 2rtk
```

## 配置说明

### 端口配置
- NTRIP服务端口：2101
- Web管理界面端口：5757

### 防火墙配置
确保以下端口已开放：
```bash
sudo ufw allow 2101/tcp
sudo ufw allow 5757/tcp
```

### 日志文件
- 服务日志：/var/log/2rtk/2rtk.log
- 日志自动轮转：每日轮转，保留7天

## Web管理界面

访问 http://your-server-ip:5757 进入Web管理界面。

默认管理员账户：
- 用户名：admin
- 密码：admin

首次登录后请立即修改密码。

## 故障排除

1. 服务无法启动
   - 检查日志文件：`sudo journalctl -u 2rtk`
   - 确认Python依赖已正确安装
   - 验证端口未被占用：`sudo lsof -i :2101` 和 `sudo lsof -i :5757`

2. 无法访问Web界面
   - 检查防火墙配置
   - 确认服务正在运行：`sudo systemctl status 2rtk`
   - 验证IP和端口配置正确

3. 基准站无法连接
   - 确认NTRIP端口(2101)已开放
   - 检查用户名和密码配置
   - 查看服务日志中的连接错误

4. 权限问题
   - 检查目录权限：`ls -l /opt/2rtk /var/log/2rtk`
   - 确认服务用户(2rtk)对相关目录有正确权限
   - 验证日志文件权限

## 安全建议

1. 更改默认管理员密码
2. 使用强密码策略
3. 定期更新系统和依赖包
4. 限制管理界面访问IP
5. 配置SSL/TLS（如需要）

## 贡献指南

欢迎提交问题报告和改进建议。请遵循以下步骤：

1. Fork 项目
2. 创建特性分支
3. 提交更改
4. 推送到分支
5. 创建 Pull Request

## 许可证

[MIT License](LICENSE)

## 联系方式

如有问题或建议，请提交 Issue 或联系项目维护者。