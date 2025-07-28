#!/bin/bash

# 检查是否以root权限运行
if [ "$EUID" -ne 0 ]; then 
    echo "请使用root权限运行此脚本"
    exit 1
fi

# 检查是否为Debian系统
if ! grep -q "Debian" /etc/os-release; then
    echo "此脚本仅适用于Debian系统"
    exit 1
fi

# 检查Debian版本是否为12
DEBIAN_VERSION=$(grep -oP 'VERSION_ID="\K[^"]+' /etc/os-release)
if [ "$DEBIAN_VERSION" != "12" ]; then
    echo "此脚本仅测试于Debian 12，当前系统版本为Debian $DEBIAN_VERSION"
    read -p "是否继续安装？(y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 设置颜色输出
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}开始安装2RTK NTRIP Caster服务...${NC}"

# 1. 更新系统并安装依赖
echo -e "${GREEN}1. 更新系统并安装依赖...${NC}"
apt update
apt install -y python3 python3-pip git

# 2. 创建服务用户和目录
echo -e "${GREEN}2. 创建服务用户和目录...${NC}"
# 创建用户（如果不存在）
if ! id "2rtk" &>/dev/null; then
    useradd -r -s /bin/false 2rtk
fi

# 创建必要的目录
mkdir -p /opt/2rtk
mkdir -p /var/log/2rtk

# 复制项目文件
cp -r ./* /opt/2rtk/

# 设置权限
chown -R 2rtk:2rtk /opt/2rtk
chown -R 2rtk:2rtk /var/log/2rtk
chmod 755 /opt/2rtk
chmod 755 /var/log/2rtk

# 3. 安装Python依赖
echo -e "${GREEN}3. 安装Python依赖...${NC}"
pip3 install -r /opt/2rtk/requirements.txt

# 4. 配置systemd服务
echo -e "${GREEN}4. 配置systemd服务...${NC}"
cp /opt/2rtk/2rtk.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable 2rtk

# 5. 配置日志轮转
echo -e "${GREEN}5. 配置日志轮转...${NC}"
cat > /etc/logrotate.d/2rtk << EOF
/var/log/2rtk/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 644 2rtk 2rtk
}
EOF

# 6. 配置防火墙
echo -e "${GREEN}6. 配置防火墙...${NC}"
if command -v ufw >/dev/null; then
    ufw allow 2101/tcp
    ufw allow 5757/tcp
    echo "防火墙规则已添加"
else
    echo "未检测到ufw，请手动配置防火墙规则"
fi

# 7. 启动服务
echo -e "${GREEN}7. 启动服务...${NC}"
systemctl start 2rtk

# 检查服务状态
if systemctl is-active --quiet 2rtk; then
    echo -e "${GREEN}2RTK服务安装成功并已启动！${NC}"
    echo "您可以使用以下命令管理服务："
    echo "  启动服务: sudo systemctl start 2rtk"
    echo "  停止服务: sudo systemctl stop 2rtk"
    echo "  重启服务: sudo systemctl restart 2rtk"
    echo "  查看状态: sudo systemctl status 2rtk"
    echo "  启用自启: sudo systemctl enable 2rtk"
    echo "  禁用自启: sudo systemctl disable 2rtk"
else
    echo -e "${RED}服务启动失败，请检查日志文件：/var/log/2rtk/2rtk.log${NC}"
    exit 1
fi

# 设置脚本为可执行
chmod +x /opt/2rtk/2rtk.py

echo -e "${GREEN}安装完成！${NC}"