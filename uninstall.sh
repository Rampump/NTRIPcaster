#!/bin/bash
#
# NTRIP Caster 一键卸载脚本
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
  echo -e "${RED}错误: 请使用 root 权限运行此脚本 (sudo ./uninstall.sh)${NC}"
  exit 1
fi

# 显示欢迎信息
echo -e "${BLUE}=================================================${NC}"
echo -e "${BLUE}       2RTK NTRIP Caster 一键卸载脚本         ${NC}"
echo -e "${BLUE}=================================================${NC}"
echo -e "${RED}警告: 此脚本将完全卸载 2RTK NTRIP Caster 及其所有数据${NC}"
echo ""

# 确认卸载
read -p "确定要卸载 2RTK NTRIP Caster 吗? (y/n): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo -e "${GREEN}卸载已取消${NC}"
  exit 0
fi

# 设置安装目录（与安装脚本中的相同）
INSTALL_DIR="/opt/2rtk"
CONFIG_DIR="/etc/2rtk"
LOG_DIR="/var/log/2rtk"
SERVICE_NAME="2rtk"

# 停止并禁用服务
echo -e "${YELLOW}停止并禁用服务...${NC}"
systemctl stop $SERVICE_NAME
systemctl disable $SERVICE_NAME
systemctl daemon-reload

# 删除 systemd 服务文件
echo -e "${YELLOW}删除 systemd 服务文件...${NC}"
rm -f /etc/systemd/system/$SERVICE_NAME.service

# 删除 Nginx 配置
echo -e "${YELLOW}删除 Nginx 配置...${NC}"
rm -f /etc/nginx/sites-enabled/2rtk
rm -f /etc/nginx/sites-available/2rtk
systemctl restart nginx

# 删除日志轮转配置
echo -e "${YELLOW}删除日志轮转配置...${NC}"
rm -f /etc/logrotate.d/2rtk

# 删除防火墙规则（如果存在）
echo -e "${YELLOW}删除防火墙规则...${NC}"
if command -v ufw > /dev/null; then
    ufw delete allow 2101/tcp
    ufw delete allow 5757/tcp
    echo -e "${GREEN}已删除 UFW 防火墙规则${NC}"
elif command -v firewall-cmd > /dev/null; then
    firewall-cmd --permanent --remove-port=2101/tcp
    firewall-cmd --permanent --remove-port=5757/tcp
    firewall-cmd --reload
    echo -e "${GREEN}已删除 firewalld 防火墙规则${NC}"
else
    echo -e "${YELLOW}未检测到支持的防火墙，请手动删除防火墙规则${NC}"
fi

# 备份数据（可选）
echo -e "${YELLOW}是否需要备份数据? (y/n): ${NC}"
read backup_choice
if [[ "$backup_choice" == "y" || "$backup_choice" == "Y" ]]; then
    BACKUP_DIR="/root/2rtk_backup_$(date +%Y%m%d_%H%M%S)"
    echo -e "${YELLOW}创建备份目录: $BACKUP_DIR${NC}"
    mkdir -p $BACKUP_DIR
    
    # 备份配置文件
    if [ -d "$CONFIG_DIR" ]; then
        cp -r $CONFIG_DIR $BACKUP_DIR/
        echo -e "${GREEN}配置文件已备份到 $BACKUP_DIR/$(basename $CONFIG_DIR)${NC}"
    fi
    
    # 备份数据库
    if [ -f "$INSTALL_DIR/2rtk.db" ]; then
        cp $INSTALL_DIR/2rtk.db $BACKUP_DIR/
        echo -e "${GREEN}数据库已备份到 $BACKUP_DIR/2rtk.db${NC}"
    fi
    
    # 备份日志
    if [ -d "$LOG_DIR" ]; then
        cp -r $LOG_DIR $BACKUP_DIR/
        echo -e "${GREEN}日志文件已备份到 $BACKUP_DIR/$(basename $LOG_DIR)${NC}"
    fi
    
    echo -e "${GREEN}数据备份完成: $BACKUP_DIR${NC}"
fi

# 删除安装目录
echo -e "${YELLOW}删除安装目录...${NC}"
rm -rf $INSTALL_DIR

# 删除配置目录
echo -e "${YELLOW}删除配置目录...${NC}"
rm -rf $CONFIG_DIR

# 删除日志目录
echo -e "${YELLOW}删除日志目录...${NC}"
rm -rf $LOG_DIR

# 询问是否卸载依赖包
echo -e "${YELLOW}是否卸载安装的依赖包? (y/n): ${NC}"
read deps_choice
if [[ "$deps_choice" == "y" || "$deps_choice" == "Y" ]]; then
    echo -e "${YELLOW}卸载依赖包...${NC}"
    # 注意：这里只卸载安装脚本中明确安装的包，不包括其依赖
    apt-get remove -y supervisor nginx
    echo -e "${GREEN}依赖包已卸载${NC}"
else
    echo -e "${YELLOW}保留依赖包${NC}"
fi

# 显示卸载完成信息
echo -e "${BLUE}=================================================${NC}"
echo -e "${GREEN}2RTK NTRIP Caster 卸载完成！${NC}"
echo -e "${BLUE}------------------------------------------------${NC}"
echo -e "${YELLOW}已删除以下内容:${NC}"
echo -e "  - 服务文件: /etc/systemd/system/$SERVICE_NAME.service"
echo -e "  - 安装目录: $INSTALL_DIR"
echo -e "  - 配置目录: $CONFIG_DIR"
echo -e "  - 日志目录: $LOG_DIR"
echo -e "  - Nginx 配置: /etc/nginx/sites-available/2rtk"
echo -e "  - 日志轮转配置: /etc/logrotate.d/2rtk"

if [[ "$backup_choice" == "y" || "$backup_choice" == "Y" ]]; then
    echo -e "${BLUE}------------------------------------------------${NC}"
    echo -e "${GREEN}数据已备份到: $BACKUP_DIR${NC}"
fi

echo -e "${BLUE}=================================================${NC}"

exit 0