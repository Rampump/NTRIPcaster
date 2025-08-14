#!/bin/bash
#
# NTRIP Caster 一键安装脚本
# 适用于 Debian/Ubuntu 系统
# 作者: 2RTK
# 版本: 1.0.0
#

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 检查是否以 root 权限运行
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}错误: 请使用 root 权限运行此脚本 (sudo ./install.sh)${NC}"
  exit 1
fi

# 显示欢迎信息
echo -e "${BLUE}=================================================${NC}"
echo -e "${BLUE}       2RTK NTRIP Caster 一键安装脚本         ${NC}"
echo -e "${BLUE}=================================================${NC}"
echo -e "${GREEN}此脚本将自动安装 2RTK NTRIP Caster 及其依赖，并设置开机自启动${NC}"
echo ""

# 检查系统类型
if [ -f /etc/debian_version ]; then
    echo -e "${GREEN}检测到 Debian/Ubuntu 系统，继续安装...${NC}"
else
    echo -e "${RED}错误: 此脚本仅支持 Debian/Ubuntu 系统${NC}"
    exit 1
fi

# 设置安装目录
INSTALL_DIR="/opt/2rtk"
CONFIG_DIR="/etc/2rtk"
LOG_DIR="/var/log/2rtk"
SERVICE_NAME="2rtk"

# 创建安装目录
echo -e "${YELLOW}创建安装目录...${NC}"
mkdir -p $INSTALL_DIR
mkdir -p $CONFIG_DIR
mkdir -p $LOG_DIR

# 创建日志子目录
echo -e "${YELLOW}创建日志目录...${NC}"


# 更新系统并安装依赖
echo -e "${YELLOW}更新系统并安装依赖...${NC}"
apt-get update
apt-get install -y python3 python3-pip python3-venv supervisor nginx git

# 创建 Python 虚拟环境
echo -e "${YELLOW}创建 Python 虚拟环境...${NC}"
python3 -m venv $INSTALL_DIR/venv
source $INSTALL_DIR/venv/bin/activate

# 下载项目文件
echo -e "${YELLOW}下载项目文件...${NC}"
cd /tmp
git clone https://github.com/Rampump/NTRIPcaster.git
cp -r NTRIPcaster/* $INSTALL_DIR/

# 复制并配置 config.ini
echo -e "${YELLOW}配置 config.ini...${NC}"
if [ -f $INSTALL_DIR/config.ini.example ]; then
    # 备份原始配置文件
    cp $INSTALL_DIR/config.ini.example $CONFIG_DIR/config.ini.original
    
    # 复制并修改配置文件
    cp $INSTALL_DIR/config.ini.example $CONFIG_DIR/config.ini
    
    # 更新配置文件中的路径
    sed -i "s|path = /app/data/2rtk.db|path = $INSTALL_DIR/data/2rtk.db|g" $CONFIG_DIR/config.ini
    sed -i "s|main_log = /app/logs/main.log|main_log = $LOG_DIR/main.log|g" $CONFIG_DIR/config.ini
    sed -i "s|ntrip_log = /app/logs/ntrip.log|ntrip_log = $LOG_DIR/ntrip.log|g" $CONFIG_DIR/config.ini
    sed -i "s|error_log = /app/logs/errors.log|error_log = $LOG_DIR/errors.log|g" $CONFIG_DIR/config.ini
    
    # 设置生产环境配置
    sed -i "s|debug = true|debug = false|g" $CONFIG_DIR/config.ini
    
    # 生成随机密钥
    RANDOM_KEY=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 32 | head -n 1)
    sed -i "s|secret_key = your-secret-key-change-this-in-production|secret_key = $RANDOM_KEY|g" $CONFIG_DIR/config.ini
    
    echo -e "${GREEN}配置文件已更新${NC}"
else
    echo -e "${RED}错误: 未找到 config.ini.example 文件${NC}"
    exit 1
fi

# 安装 Python 依赖
echo -e "${YELLOW}安装 Python 依赖...${NC}"
$INSTALL_DIR/venv/bin/pip install --upgrade pip
$INSTALL_DIR/venv/bin/pip install -r $INSTALL_DIR/requirements.txt

# 创建 systemd 服务文件
echo -e "${YELLOW}创建 systemd 服务文件...${NC}"
cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=NTRIP Caster Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin"
Environment="NTRIP_CONFIG_FILE=$CONFIG_DIR/config.ini"
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/main.py
Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/main.log
StandardError=append:$LOG_DIR/errors.log

[Install]
WantedBy=multi-user.target
EOF

# 创建日志轮转配置
echo -e "${YELLOW}创建日志轮转配置...${NC}"
cat > /etc/logrotate.d/2rtk << EOF
$LOG_DIR/main.log $LOG_DIR/ntrip.log $LOG_DIR/errors.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 root root
    sharedscripts
    postrotate
        systemctl reload 2rtk.service > /dev/null 2>&1 || true
    endscript
}
EOF

# 设置文件权限
echo -e "${YELLOW}设置文件权限...${NC}"
chmod +x $INSTALL_DIR/main.py
chown -R root:root $INSTALL_DIR
chown -R root:root $CONFIG_DIR
chown -R root:root $LOG_DIR

# 设置日志目录权限
echo -e "${YELLOW}设置日志目录权限...${NC}"
chmod -R 755 $LOG_DIR
find $LOG_DIR -type d -exec chmod 755 {} \;
find $LOG_DIR -type f -exec chmod 644 {} \;

# 创建数据库目录
echo -e "${YELLOW}创建数据库目录...${NC}"
mkdir -p $INSTALL_DIR/data
chmod 755 $INSTALL_DIR/data

# 创建符号链接，方便访问配置文件
ln -sf $CONFIG_DIR/config.ini $INSTALL_DIR/config.ini

# 启用并启动服务
echo -e "${YELLOW}启用并启动服务...${NC}"
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl start $SERVICE_NAME

# 配置防火墙（如果存在）
echo -e "${YELLOW}配置防火墙...${NC}"
if command -v ufw > /dev/null; then
    ufw allow 2101/tcp  # NTRIP 端口
    ufw allow 5757/tcp  # Web 管理界面端口
    echo -e "${GREEN}已配置 UFW 防火墙规则${NC}"
elif command -v firewall-cmd > /dev/null; then
    firewall-cmd --permanent --add-port=2101/tcp
    firewall-cmd --permanent --add-port=5757/tcp
    firewall-cmd --reload
    echo -e "${GREEN}已配置 firewalld 防火墙规则${NC}"
else
    echo -e "${YELLOW}未检测到支持的防火墙，请手动配置防火墙规则${NC}"
fi

# 创建 Nginx 配置（可选，用于反向代理 Web 管理界面）
echo -e "${YELLOW}创建 Nginx 配置...${NC}"
cat > /etc/nginx/sites-available/2rtk << EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5757;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

# 启用 Nginx 配置
ln -sf /etc/nginx/sites-available/2rtk /etc/nginx/sites-enabled/
systemctl restart nginx

# 检查服务状态
echo -e "${YELLOW}检查服务状态...${NC}"
sleep 3
if systemctl is-active --quiet $SERVICE_NAME; then
    echo -e "${GREEN}NTRIP Caster 服务已成功启动！${NC}"
else
    echo -e "${RED}NTRIP Caster 服务启动失败，请检查日志: $LOG_DIR/errors.log${NC}"
fi

# 显示安装信息
echo -e "${BLUE}=================================================${NC}"
echo -e "${GREEN}2RTK NTRIP Caster 安装完成！${NC}"
echo -e "${BLUE}------------------------------------------------${NC}"
echo -e "${YELLOW}安装目录:${NC} $INSTALL_DIR"
echo -e "${YELLOW}配置目录:${NC} $CONFIG_DIR"
echo -e "${YELLOW}日志目录:${NC} $LOG_DIR"
echo -e "${YELLOW}NTRIP 端口:${NC} 2101"
echo -e "${YELLOW}Web 管理界面:${NC} http://服务器IP:5757"
echo -e "${YELLOW}Nginx 代理:${NC} http://服务器IP"
echo -e "${BLUE}------------------------------------------------${NC}"
echo -e "${YELLOW}服务管理命令:${NC}"
echo -e "  启动服务: ${GREEN}systemctl start $SERVICE_NAME${NC}"
echo -e "  停止服务: ${GREEN}systemctl stop $SERVICE_NAME${NC}"
echo -e "  重启服务: ${GREEN}systemctl restart $SERVICE_NAME${NC}"
echo -e "  查看状态: ${GREEN}systemctl status $SERVICE_NAME${NC}"
echo -e "  查看日志: ${GREEN}journalctl -u $SERVICE_NAME${NC}"
echo -e "${BLUE}=================================================${NC}"

# 提示修改默认密码
echo -e "${RED}安全提示: 请尽快修改默认管理员密码!${NC}"
echo -e "默认管理员账号: admin"
echo -e "默认管理员密码: admin123"

exit 0