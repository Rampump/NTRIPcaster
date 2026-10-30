# NTRIP Caster Linux 系统安装教程

在 Linux 系统上直接安装和部署 NTRIP Caster v2.2.0，支持 Debian、Ubuntu、CentOS、RHEL 等主流发行版。

## 目录
- [系统要求](#系统要求)
- [快速安装](#快速安装)
- [一键安装脚本](#一键安装脚本)
- [详细安装步骤](#详细安装步骤)
- [配置说明](#配置说明)
- [服务管理](#服务管理)
- [常见问题](#常见问题)
- [性能优化](#性能优化)
- [安全配置](#安全配置)

## 系统要求

### 最低要求
- **操作系统**: Linux (Debian 10+, Ubuntu 18.04+, CentOS 7+, RHEL 7+)
- **CPU**: 1 核心
- **内存**: 512MB RAM
- **存储**: 1GB 可用空间
- **Python**: 3.8+ (推荐 3.11)

### 推荐配置
- **CPU**: 2+ 核心
- **内存**: 2GB+ RAM
- **存储**: 10GB+ 可用空间
- **网络**: 稳定的网络连接

### 支持的发行版
- Debian 10/11/12 (Buster/Bullseye/Bookworm)
- Ubuntu 18.04/20.04/22.04/24.04 LTS
- CentOS 7/8/9
- RHEL 7/8/9
- Rocky Linux 8/9
- AlmaLinux 8/9
- openSUSE Leap 15.x
- Fedora 35+

### 手动快速安装

**中国区服务器**（从 GitCode 克隆）：

```bash
git clone https://gitcode.com/rampump/NTRIPcaster.git
cd NTRIPcaster
sudo bash install.sh
```

**国际服务器**（从 GitHub 克隆）：

```bash
git clone https://github.com/Rampump/NTRIPcaster.git
cd NTRIPcaster
sudo bash install.sh
```

安装完成后，服务会自动启动（systemd 服务名 `2rtk`）：

```bash
sudo systemctl start 2rtk
sudo systemctl enable 2rtk
```

## 一键安装脚本

### 自动安装脚本

我们提供了一个自动安装脚本，可以在大多数 Linux 发行版上一键安装 NTRIP Caster。

#### 使用方法

> **运行权限说明**：脚本需要 root 权限。**普通用户**请用 `sudo bash install.sh`；**已是 root 登录**（提示符为 `#`）的直接用 `bash install.sh`，无需 sudo。

**方法一：克隆后直接执行（推荐）**

中国区服务器（从 GitCode 克隆）：

```bash
git clone https://gitcode.com/rampump/NTRIPcaster.git && cd NTRIPcaster && sudo bash install.sh
```

国际服务器（从 GitHub 克隆）：

```bash
git clone https://github.com/Rampump/NTRIPcaster.git && cd NTRIPcaster && sudo bash install.sh
```

**方法二：仅下载脚本后执行（国际用户）**
```bash
# 下载脚本到本地（脚本内部会自动从 GitCode/GitHub 拉取源码）
wget https://raw.githubusercontent.com/Rampump/NTRIPcaster/main/install.sh
sudo bash install.sh
```

> **注意**：脚本会检测当前目录是否已是项目源码，是则直接复用；否则自动从 GitCode（优先）或 GitHub 拉取最新源码。

#### 脚本功能

`install.sh` 是**仅支持 Debian 10+ / Ubuntu 20.04+** 的一键安装脚本，会自动完成：

1. **检查系统环境**：识别 Debian/Ubuntu 及大版本号（其他发行版或过老版本会提示并退出），校验 Python >= 3.8，并要求以 root 权限运行。

2. **安装系统依赖**：`python3`、`python3-pip`、`python3-venv`、`nginx`、`git`（已移除 `supervisor`，代码未使用且新版系统源中已删除该包）。

3. **创建目录**：安装目录 `/opt/2rtk`、配置目录 `/etc/2rtk`、日志目录 `/var/log/2rtk`。

4. **下载并配置应用**：从 GitHub/GitCode 克隆源码到 `/opt/2rtk`，创建虚拟环境，安装依赖（**自动切换 pip 国内镜像**加速，失败回退官方源），复制 `config.ini.example` 到 `/etc/2rtk/config.ini`，并把数据库路径/日志目录写入配置、生成随机 `secret_key`。

5. **配置 systemd 服务**：创建 `/etc/systemd/system/2rtk.service`，`enable` 并 `start`；同时创建日志轮转 `/etc/logrotate.d/2rtk`。

6. **网络配置**：配置 UFW/firewalld 开放 2101/5757 端口；创建 Nginx 反向代理配置（`/etc/nginx/sites-available/2rtk`）。

> 注意：脚本以 **root 用户**运行服务（systemd `User=root`），目录/服务名固定为 `2rtk`，不通过环境变量自定义。

#### 验证安装

安装完成后，脚本会自动验证安装结果：

```bash
# 检查服务状态
sudo systemctl status 2rtk

# 检查端口监听
sudo ss -tlnp | grep :2101
sudo ss -tlnp | grep :5757

# 访问 Web 界面
curl -I http://localhost:5757
```

#### 卸载

如需卸载，可以使用卸载脚本：

```bash
# 下载并运行卸载脚本
wget -O - https://raw.githubusercontent.com/Rampump/NTRIPcaster/main/uninstall.sh | sudo bash
```

### 快速验证

安装完成后，您可以通过以下方式快速验证：

1. **检查服务状态**：
   ```bash
   sudo systemctl status 2rtk
   ```

2. **访问 Web 管理界面**：
   打开浏览器访问 `http://your-server-ip:5757`
   
   默认登录信息：
   - 用户名：`admin`
   - 密码：`admin123`

3. **测试 NTRIP 连接**：
   ```bash
   telnet localhost 2101
   ```

## 详细安装步骤

### 1. 系统准备

#### Debian/Ubuntu 系统

```bash
# 更新系统包
sudo apt update && sudo apt upgrade -y

# 安装必要的系统包
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    git \
    wget \
    curl \
    sqlite3 \
    build-essential \
    python3-dev \
    libssl-dev \
    libffi-dev \
    nginx

# 安装 systemd 开发包（如果需要）
sudo apt install -y libsystemd-dev
```

#### CentOS/RHEL 系统

```bash
# CentOS 7
sudo yum update -y
sudo yum install -y epel-release
sudo yum install -y \
    python3 \
    python3-pip \
    git \
    wget \
    curl \
    sqlite \
    gcc \
    python3-devel \
    openssl-devel \
    libffi-devel \
    nginx

# CentOS 8/9 或 RHEL 8/9
sudo dnf update -y
sudo dnf install -y epel-release
sudo dnf install -y \
    python3 \
    python3-pip \
    git \
    wget \
    curl \
    sqlite \
    gcc \
    python3-devel \
    openssl-devel \
    libffi-devel \
    nginx
```

#### openSUSE 系统

```bash
# 更新系统
sudo zypper refresh && sudo zypper update -y

# 安装必要包
sudo zypper install -y \
    python3 \
    python3-pip \
    git \
    wget \
    curl \
    sqlite3 \
    gcc \
    python3-devel \
    libopenssl-devel \
    libffi-devel \
    nginx
```

### 2. 创建系统用户

```bash
# 创建专用用户
sudo useradd -r -s /bin/false -d /opt/2rtk 2rtk

# 创建应用目录
sudo mkdir -p /opt/2rtk
sudo mkdir -p /var/log/2rtk
sudo mkdir -p /etc/2rtk

# 设置目录权限
sudo chown -R 2rtk:2rtk /opt/2rtk
sudo chown -R 2rtk:2rtk /var/log/2rtk
sudo chown -R 2rtk:2rtk /etc/2rtk
```

### 3. 下载和安装应用

```bash
# 切换到应用目录
cd /opt/2rtk

# 下载源码（中国区用 GitCode，国际用 GitHub）
sudo -u 2rtk git clone https://gitcode.com/rampump/NTRIPcaster.git
# sudo -u 2rtk git clone https://github.com/Rampump/NTRIPcaster.git

# 创建 Python 虚拟环境
sudo -u 2rtk python3 -m venv venv

# 激活虚拟环境并安装依赖
sudo -u 2rtk bash -c '
    source venv/bin/activate
    pip install --upgrade pip setuptools wheel
    pip install -r requirements.txt
'
```

### 4. 配置应用

```bash
# 复制配置文件
sudo -u 2rtk cp config.ini.example /etc/2rtk/config.ini

# 编辑配置文件
sudo nano /etc/2rtk/config.ini
```

#### 主要配置项：

```ini
[app]
name = 2RTK Ntrip Caster
version = 2.2.0
description = Ntrip Caster
author = 2rtk
contact = your-email@example.com
website = https://your-domain.com

[network]
host = 0.0.0.0
max_connections = 5000

[ntrip]
port = 2101

[web]
port = 5757

[database]
path = /opt/2rtk/data/2rtk.db

[logging]
log_dir = /var/log/2rtk
log_level = INFO
main_log_file = main.log
log_format = %%(asctime)s - %%(name)s - %%(levelname)s - %%(message)s
max_log_size = 10485760
backup_count = 5

[security]
secret_key = $(openssl rand -hex 32)
password_hash_rounds = 3
session_timeout = 3600
```

### 5. 生成密钥和初始化数据库

```bash
# 生成安全密钥（config.ini 的 secret_key 留空会自动生成，也可手动指定）
SECRET_KEY=$(openssl rand -hex 32)
sudo sed -i "s/^secret_key = *$/secret_key = $SECRET_KEY/" /etc/2rtk/config.ini

# 创建数据目录
sudo -u 2rtk mkdir -p /opt/2rtk/data

# 初始化数据库（首次启动 main.py 会自动建表，无需手动初始化）
sudo -u 2rtk bash -c '
    cd /opt/2rtk
    source venv/bin/activate
    python3 -c "from src.database import DatabaseManager; DatabaseManager().init_database()"
'
```

### 6. 创建 systemd 服务

```bash
# 创建服务文件
sudo tee /etc/systemd/system/2rtk.service > /dev/null <<EOF
[Unit]
Description=NTRIP Caster Service
After=network.target
Wants=network.target

[Service]
Type=simple
User=2rtk
Group=2rtk
WorkingDirectory=/opt/2rtk
Environment=NTRIP_CONFIG_FILE=/etc/2rtk/config.ini
Environment=PYTHONPATH=/opt/2rtk
ExecStart=/opt/2rtk/venv/bin/python /opt/2rtk/main.py
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=30

# 安全设置
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/2rtk/data /var/log/2rtk /etc/2rtk

# 资源限制
LimitNOFILE=65536
LimitNPROC=4096

[Install]
WantedBy=multi-user.target
EOF

# 重新加载 systemd 配置
sudo systemctl daemon-reload
```

### 7. 配置防火墙

#### UFW (Ubuntu/Debian)

```bash
# 启用 UFW
sudo ufw enable

# 开放必要端口
sudo ufw allow 2101/tcp comment 'NTRIP Service'
sudo ufw allow 5757/tcp comment 'NTRIP Web Interface'
sudo ufw allow ssh

# 查看状态
sudo ufw status
```

#### firewalld (CentOS/RHEL)

```bash
# 启动 firewalld
sudo systemctl start firewalld
sudo systemctl enable firewalld

# 开放端口
sudo firewall-cmd --permanent --add-port=2101/tcp
sudo firewall-cmd --permanent --add-port=5757/tcp
sudo firewall-cmd --reload

# 查看状态
sudo firewall-cmd --list-all
```

#### iptables (通用)

```bash
# 开放端口
sudo iptables -A INPUT -p tcp --dport 2101 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 5757 -j ACCEPT

# 保存规则 (Debian/Ubuntu)
sudo iptables-save > /etc/iptables/rules.v4

# 保存规则 (CentOS/RHEL)
sudo service iptables save
```

## 配置说明

### 环境变量

可以通过环境变量覆盖配置文件设置：

```bash
# 在 /etc/systemd/system/2rtk.service 中添加
Environment=NTRIP_PORT=2101
Environment=WEB_PORT=5757
Environment=DEBUG=false
Environment=DATABASE_PATH=/opt/2rtk/data/2rtk.db
Environment=LOG_DIR=/var/log/2rtk
```

### 日志配置

```bash
# 配置 logrotate
sudo tee /etc/logrotate.d/2rtk > /dev/null <<EOF
/var/log/2rtk/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 2rtk 2rtk
    postrotate
        systemctl reload 2rtk
    endscript
}
EOF
```

## 服务管理

### 基本操作

```bash
# 启动服务
sudo systemctl start 2rtk

# 停止服务
sudo systemctl stop 2rtk

# 重启服务
sudo systemctl restart 2rtk

# 重新加载配置
sudo systemctl reload 2rtk

# 查看服务状态
sudo systemctl status 2rtk

# 设置开机自启
sudo systemctl enable 2rtk

# 禁用开机自启
sudo systemctl disable 2rtk
```

### 日志查看

```bash
# 查看服务日志
sudo journalctl -u 2rtk

# 实时查看日志
sudo journalctl -u 2rtk -f

# 查看应用日志
sudo tail -f /var/log/2rtk/main.log

# 查看错误日志
sudo grep ERROR /var/log/2rtk/main.log
```

### 服务监控

```bash
# 检查服务是否运行
sudo systemctl is-active 2rtk

# 检查端口监听
sudo ss -tlnp | grep -E ':(2101|5757)'

# 检查进程
sudo ps aux | grep 2rtk
```

## 常见问题

### Q1: 服务启动失败

**排查步骤：**

```bash
# 查看详细错误信息
sudo journalctl -u 2rtk -n 50

# 检查配置文件语法
sudo -u 2rtk bash -c '
    cd /opt/2rtk
    source venv/bin/activate
    python3 -c "import configparser; c=configparser.ConfigParser(); c.read("/etc/2rtk/config.ini")"
'

# 检查文件权限
sudo ls -la /opt/2rtk/
sudo ls -la /etc/2rtk/
sudo ls -la /var/log/2rtk/

# 手动测试启动
sudo -u 2rtk bash -c '
    cd /opt/2rtk
    source venv/bin/activate
    NTRIP_CONFIG_FILE=/etc/2rtk/config.ini python3 main.py
'
```

### Q2: 端口被占用

```bash
# 查看端口占用
sudo lsof -i :2101
sudo lsof -i :5757

# 杀死占用进程
sudo kill -9 <PID>

# 或者修改配置文件中的端口
sudo nano /etc/2rtk/config.ini
```

### Q3: 权限问题

```bash
# 重新设置权限
sudo chown -R 2rtk:2rtk /opt/2rtk
sudo chown -R 2rtk:2rtk /var/log/2rtk
sudo chown -R 2rtk:2rtk /etc/2rtk

# 检查 SELinux (CentOS/RHEL)
sudo getenforce
sudo setsebool -P httpd_can_network_connect 1
```

### Q4: Python 依赖问题

```bash
# 重新安装依赖
sudo -u 2rtk bash -c '
    cd /opt/2rtk
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt --force-reinstall
'

# 检查 Python 版本
python3 --version

# 如果 Python 版本过低，安装新版本
# Ubuntu/Debian
sudo apt install software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.11 python3.11-venv python3.11-dev
```

### Q5: 数据库问题

```bash
# 检查数据库文件
sudo ls -la /opt/2rtk/data/

# 重新初始化数据库（删除后重启服务会自动重建）
sudo -u 2rtk bash -c '
    cd /opt/2rtk
    source venv/bin/activate
    rm -f data/2rtk.db
'
# 重启服务，main.py 会自动建表
sudo systemctl restart 2rtk

# 检查数据库完整性
sudo -u 2rtk sqlite3 /opt/2rtk/data/2rtk.db "PRAGMA integrity_check;"
```

## 性能优化

### 1. 系统优化

```bash
# 优化文件描述符限制
sudo tee -a /etc/security/limits.conf > /dev/null <<EOF
2rtk soft nofile 65536
2rtk hard nofile 65536
2rtk soft nproc 4096
2rtk hard nproc 4096
EOF

# 优化网络参数
sudo tee -a /etc/sysctl.conf > /dev/null <<EOF
# NTRIP Caster 网络优化
net.core.somaxconn = 1024
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_max_syn_backlog = 1024
net.ipv4.tcp_keepalive_time = 600
net.ipv4.tcp_keepalive_intvl = 60
net.ipv4.tcp_keepalive_probes = 3
EOF

# 应用系统参数
sudo sysctl -p
```

### 2. 应用优化

编辑配置文件 `/etc/2rtk/config.ini`：

```ini
[performance]
# 线程池大小
thread_pool_size = 10

# 最大工作线程数
max_workers = 20

# 连接队列大小
connection_queue_size = 100

# 最大内存使用 (MB)
max_memory_usage = 512

# CPU 使用警告阈值 (%)
cpu_warning_threshold = 80

# 内存使用警告阈值 (%)
memory_warning_threshold = 85

[network]
# 最大连接数
max_connections = 1000

# 缓冲区大小
buffer_size = 8192

[tcp]
# TCP Keep-Alive 设置
keepalive_enabled = true
keepalive_idle = 600
keepalive_interval = 60
keepalive_count = 3

# Socket 超时
socket_timeout = 30
```

### 3. 监控脚本

创建监控脚本 `/opt/2rtk/monitor.sh`：

```bash
#!/bin/bash

# NTRIP Caster 监控脚本

LOG_FILE="/var/log/2rtk/monitor.log"
PID_FILE="/var/run/2rtk.pid"

# 检查服务状态
check_service() {
    if ! systemctl is-active --quiet 2rtk; then
        echo "$(date): NTRIP Caster service is not running, attempting to restart..." >> $LOG_FILE
        systemctl start 2rtk
        sleep 5
        if systemctl is-active --quiet 2rtk; then
            echo "$(date): NTRIP Caster service restarted successfully" >> $LOG_FILE
        else
            echo "$(date): Failed to restart NTRIP Caster service" >> $LOG_FILE
        fi
    fi
}

# 检查端口
check_ports() {
    if ! ss -tlnp | grep -q ":2101"; then
        echo "$(date): NTRIP port 2101 is not listening" >> $LOG_FILE
    fi
    
    if ! ss -tlnp | grep -q ":5757"; then
        echo "$(date): Web port 5757 is not listening" >> $LOG_FILE
    fi
}

# 检查内存使用
check_memory() {
    MEMORY_USAGE=$(ps -o pid,ppid,cmd,%mem --sort=-%mem -C python3 | grep 2rtk | awk '{print $4}' | head -1)
    if [ ! -z "$MEMORY_USAGE" ] && (( $(echo "$MEMORY_USAGE > 80" | bc -l) )); then
        echo "$(date): High memory usage detected: ${MEMORY_USAGE}%" >> $LOG_FILE
    fi
}

# 执行检查
check_service
check_ports
check_memory

echo "$(date): Monitor check completed" >> $LOG_FILE
```

设置定时任务：

```bash
# 设置执行权限
sudo chmod +x /opt/2rtk/monitor.sh

# 添加到 crontab
sudo crontab -e
# 添加以下行（每5分钟检查一次）
*/5 * * * * /opt/2rtk/monitor.sh
```

## 安全配置

### 1. SSL/TLS 配置

#### 生成自签名证书

```bash
# 创建证书目录
sudo mkdir -p /etc/2rtk/ssl

# 生成私钥
sudo openssl genrsa -out /etc/2rtk/ssl/server.key 2048

# 生成证书签名请求
sudo openssl req -new -key /etc/2rtk/ssl/server.key -out /etc/2rtk/ssl/server.csr

# 生成自签名证书
sudo openssl x509 -req -days 365 -in /etc/2rtk/ssl/server.csr -signkey /etc/2rtk/ssl/server.key -out /etc/2rtk/ssl/server.crt

# 设置权限
sudo chown -R 2rtk:2rtk /etc/2rtk/ssl
sudo chmod 600 /etc/2rtk/ssl/server.key
sudo chmod 644 /etc/2rtk/ssl/server.crt
```

#### 配置 Nginx 反向代理

```bash
# 创建 Nginx 配置
sudo tee /etc/nginx/sites-available/2rtk > /dev/null <<EOF
server {
    listen 80;
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL 配置
    ssl_certificate /etc/2rtk/ssl/server.crt;
    ssl_certificate_key /etc/2rtk/ssl/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # HTTP 重定向到 HTTPS
    if (\$scheme != "https") {
        return 301 https://\$host\$request_uri;
    }

    # Web 界面代理
    location / {
        proxy_pass http://127.0.0.1:5757;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # NTRIP 服务代理
    location /ntrip {
        proxy_pass http://127.0.0.1:2101;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

# 启用站点
sudo ln -s /etc/nginx/sites-available/2rtk /etc/nginx/sites-enabled/

# 测试配置
sudo nginx -t

# 重启 Nginx
sudo systemctl restart nginx
```

### 2. 访问控制

```bash
# 配置 fail2ban
sudo apt install fail2ban  # Debian/Ubuntu
sudo yum install fail2ban  # CentOS/RHEL

# 创建 fail2ban 配置
sudo tee /etc/fail2ban/jail.d/2rtk.conf > /dev/null <<EOF
[2rtk]
enabled = true
port = 2101,5757
filter = 2rtk
logpath = /var/log/2rtk/main.log
maxretry = 5
bantime = 3600
findtime = 600
EOF

# 创建过滤规则
sudo tee /etc/fail2ban/filter.d/2rtk.conf > /dev/null <<EOF
[Definition]
failregex = ^.*Authentication failed.*from <HOST>.*$
            ^.*Invalid credentials.*from <HOST>.*$
            ^.*Connection refused.*from <HOST>.*$
ignoreregex =
EOF

# 重启 fail2ban
sudo systemctl restart fail2ban
```

### 3. 定期备份

创建备份脚本 `/opt/2rtk/backup.sh`：

```bash
#!/bin/bash

# NTRIP Caster 备份脚本

BACKUP_DIR="/opt/backups/2rtk"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="2rtk_backup_$DATE.tar.gz"

# 创建备份目录
mkdir -p $BACKUP_DIR

# 停止服务
systemctl stop 2rtk

# 创建备份
tar -czf $BACKUP_DIR/$BACKUP_FILE \
    /opt/2rtk \
    /etc/2rtk \
    /var/log/2rtk

# 启动服务
systemctl start 2rtk

# 删除30天前的备份
find $BACKUP_DIR -name "2rtk_backup_*.tar.gz" -mtime +30 -delete

echo "Backup completed: $BACKUP_DIR/$BACKUP_FILE"
```

设置定时备份：

```bash
# 设置执行权限
sudo chmod +x /opt/2rtk/backup.sh

# 添加到 crontab（每天凌晨2点备份）
sudo crontab -e
# 添加以下行
0 2 * * * /opt/2rtk/backup.sh
```

## 卸载

如果需要完全卸载 NTRIP Caster：

```bash
# 停止并禁用服务
sudo systemctl stop 2rtk
sudo systemctl disable 2rtk

# 删除服务文件
sudo rm /etc/systemd/system/2rtk.service
sudo systemctl daemon-reload

# 删除应用文件
sudo rm -rf /opt/2rtk
sudo rm -rf /etc/2rtk
sudo rm -rf /var/log/2rtk

# 删除用户
sudo userdel 2rtk

# 删除 Nginx 配置（如果配置了）
sudo rm /etc/nginx/sites-enabled/2rtk
sudo rm /etc/nginx/sites-available/2rtk
sudo systemctl restart nginx

# 删除防火墙规则
sudo ufw delete allow 2101/tcp
sudo ufw delete allow 5757/tcp
```

## 技术支持

如果您在安装或使用过程中遇到问题，可以：

1. **查看日志**: `sudo journalctl -u 2rtk -f`
2. **检查状态**: `sudo systemctl status 2rtk`
3. **查看文档**: [GitHub 仓库](https://github.com/Rampump/NTRIPcaster) / [GitCode 仓库](https://gitcode.com/rampump/NTRIPcaster)
4. **提交问题**: [GitHub Issues](https://github.com/Rampump/NTRIPcaster/issues)
5. **联系作者**: i@jia.by
6. **访问官网**: https://2rtk.com

## 更新日志

- **v2.2.0**: 最新版本，支持更多 Linux 发行版
- **v2.1.8**: 性能优化和安全增强
- **v2.1.7**: 添加监控和日志功能

---

**版本信息：** NTRIP Caster v2.2.0  
**更新时间：** 2024年12月  
**作者：** 2RTK Team  
**许可证：** Apache 2.0