#!/bin/bash
#
# NTRIP Caster 一键安装脚本
# 适用于 Debian/Ubuntu 系统
# 作者: 2RTK
# 版本: 1.0.0
#

# ===== 非交互安装环境（避免 needrestart 等工具弹出交互窗口卡住安装）=====
# DEBIAN_FRONTEND=noninteractive : 让 apt/dpkg 不弹出任何交互提示（如 nginx 配置询问）
# NEEDRESTART_MODE=a            : 让 needrestart 自动重启需要重启的服务，而非交互式询问
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
# 兼容旧版 needrestart（v3.5 之前无 NEEDRESTART_MODE，用 dpkg 的配置禁用其交互）
if [ -d /etc/needrestart/conf.d ] && [ ! -f /etc/needrestart/conf.d/99-disable-interactive.conf ]; then
    mkdir -p /etc/needrestart/conf.d
    echo '$nrconf{restart} = "a";' > /etc/needrestart/conf.d/99-disable-interactive.conf
fi

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 检查是否以 root 权限运行
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}错误: 此脚本需要 root 权限运行${NC}"
  echo -e "${YELLOW}提示: 请使用以下任一方式重新运行${NC}"
  echo -e "  已是 root 用户:          ${GREEN}bash install.sh${NC}"
  echo -e "  普通用户(需要 sudo):     ${GREEN}sudo bash install.sh${NC}"
  exit 1
fi

# 显示欢迎信息
echo -e "${BLUE}=================================================${NC}"
echo -e "${BLUE}       2RTK NTRIP Caster 一键安装脚本         ${NC}"
echo -e "${BLUE}=================================================${NC}"
echo -e "${GREEN}此脚本将自动安装 2RTK NTRIP Caster 及其依赖，并设置开机自启动${NC}"
echo ""

# 检查系统类型与版本（仅支持 Debian/Ubuntu，且要求较新版本）
OS_ID="unknown"
OS_VERSION="0"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    case "$ID" in
        debian)
            OS_ID="debian"
            OS_VERSION="${VERSION_ID%%.*}"
            ;;
        ubuntu)
            OS_ID="ubuntu"
            OS_VERSION="${VERSION_ID%%.*}"
            ;;
    esac
fi

if [ "$OS_ID" = "unknown" ]; then
    echo -e "${RED}错误: 此脚本仅支持 Debian/Ubuntu 系统${NC}"
    echo -e "${YELLOW}当前系统未识别（ID=$ID）。如为其他发行版请参考文档手动安装。${NC}"
    exit 1
fi

# 版本下限：Debian >= 11，Ubuntu >= 22.04（需 python3 >= 3.9）
# 原因：本项目依赖 pyrtcm 1.1.8（要求 Python>=3.9）、pyproj 3.5.0（要求 Python>=3.8）、
# Flask 3.0.3（要求 Python>=3.8）。综合下来真实下限是 Python 3.9：
#   Debian 10(buster)=Py3.7 ❌   Debian 11(bullseye)=Py3.9 ✅
#   Debian 12(bookworm)=Py3.11 ✅  Debian 13(trixie)=Py3.13 ✅
#   Ubuntu 18.04=Py3.6 ❌  Ubuntu 20.04=Py3.8 ❌（pyrtcm 1.1.8 装不上）
#   Ubuntu 22.04=Py3.10 ✅  Ubuntu 24.04=Py3.12 ✅
# Ubuntu 20.04(Python 3.8) 虽满足 Flask/pyproj，但 pyrtcm 1.1.8 要求 3.9+，无法安装。
TOO_OLD=false
if [ "$OS_ID" = "debian" ] && [ "$OS_VERSION" -lt 11 ]; then
    TOO_OLD=true
fi
if [ "$OS_ID" = "ubuntu" ] && [ "$OS_VERSION" -lt 22 ]; then
    TOO_OLD=true
fi
if [ "$TOO_OLD" = true ]; then
    echo -e "${RED}错误: 检测到过旧的 ${OS_ID} ${VERSION_ID}${NC}"
    echo -e "${RED}此脚本要求 Debian 11+ / Ubuntu 22.04+（Python >= 3.9）${NC}"
    echo -e "${YELLOW}当前版本自带的 Python 过旧，无法安装本项目依赖（pyrtcm 需 Python>=3.9）。${NC}"
    echo -e "${YELLOW}请升级系统，或参考文档手动安装 Python 3.9+ 后重试。${NC}"
    exit 1
fi

echo -e "${GREEN}检测到 ${OS_ID} ${VERSION_ID}，继续安装...${NC}"

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
if ! DEBIAN_FRONTEND=noninteractive apt-get update; then
    echo -e "${RED}错误: apt-get update 失败，请检查网络或软件源配置${NC}"
    echo -e "${YELLOW}中国区服务器可尝试将 /etc/apt/sources.list 切换为国内镜像后重试${NC}"
    exit 1
fi

# 安装依赖包（已移除 supervisor：代码未使用且 Debian 12/Ubuntu 24.04 源中已删除该包）
# 显式携带非交互环境变量，确保 apt 及 needrestart 全程不弹交互窗口
# 分两段安装：先装 python3/pip/nginx/git，再单独装 python3-venv。
# 原因：python3-venv 是版本绑定包（如 python3.9-venv），在 Debian 11 打安全补丁后
# 会出现 "python3.9-venv 依赖 python3.9=3.9.2-1 但系统是 3.9.2-1+deb11u7" 的依赖断裂，
# 单独安装便于失败时给出针对性修复提示，而不是整条命令一起失败难以定位。
echo -e "${YELLOW}安装基础依赖 (python3/pip/nginx/git)...${NC}"
if ! DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y python3 python3-pip nginx git; then
    echo -e "${RED}错误: 基础依赖安装失败，请检查软件源配置${NC}"
    exit 1
fi

echo -e "${YELLOW}安装 python3-venv...${NC}"
if ! DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y python3-venv; then
    echo -e "${YELLOW}python3-venv 直接安装失败，尝试先对齐 python3 版本...${NC}"
    # 常见根因：python3-venv 版本绑定包的依赖写死（如 python3.9=3.9.2-1），
    # 而系统 python3 已打安全补丁（3.9.2-1+deb11u7），版本号对不上导致依赖断裂。
    # 先 apt-get upgrade 把 python3 与 venv 包版本对齐，再重试。
    DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get upgrade -y python3 python3-venv 2>/dev/null || true
    if ! DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y python3-venv; then
        echo -e "${RED}错误: python3-venv 安装失败（依赖断裂）${NC}"
        echo -e "${YELLOW}常见原因：系统 python3 打了安全补丁（版本号带 +debXuY 后缀），${NC}"
        echo -e "${YELLOW}而 python3-venv 依赖写死不带后缀的版本号，apt 判定依赖断裂。${NC}"
        echo -e "${YELLOW}请手动执行以下命令修复后重试本脚本：${NC}"
        echo -e "${GREEN}  sudo apt-get update${NC}"
        echo -e "${GREEN}  sudo apt-get upgrade          # 关键：让 python3 与 python3-venv 版本对齐${NC}"
        echo -e "${GREEN}  sudo apt-get install -y python3-venv${NC}"
        echo -e "${YELLOW}若仍失败，可能是软件源未包含 security 源，请检查 /etc/apt/sources.list${NC}"
        echo -e "${YELLOW}Debian 11 的 security 源写法应为 'bullseye-security'（不是 bullseye/updates）。${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}python3-venv 安装成功${NC}"

# 校验 python3 版本（pyrtcm 1.1.8 需 >= 3.9，是当前依赖里的最高下限）
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo "0")
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo "0")
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    echo -e "${RED}错误: 需要 Python >= 3.9，当前为 $(python3 --version 2>&1)${NC}"
    echo -e "${YELLOW}依赖 pyrtcm 1.1.8 要求 Python >= 3.9。请升级系统或手动安装更高版本 Python 后重试${NC}"
    exit 1
fi
echo -e "${GREEN}Python 版本满足要求: $(python3 --version)${NC}"

# 创建 Python 虚拟环境
echo -e "${YELLOW}创建 Python 虚拟环境...${NC}"
if ! python3 -m venv $INSTALL_DIR/venv; then
    echo -e "${RED}错误: 创建虚拟环境失败${NC}"
    echo -e "${YELLOW}常见原因：python3-venv 与系统 python3 版本不匹配（依赖断裂）${NC}"
    echo -e "${YELLOW}或 ensurepip 未正确安装。请确认 python3-venv 已成功安装且与 python3 同版本：${NC}"
    echo -e "${GREEN}  python3 --version${NC}"
    echo -e "${GREEN}  dpkg -l | grep python3-venv${NC}"
    exit 1
fi
source $INSTALL_DIR/venv/bin/activate

# 下载项目文件
# 双源自动降级：优先 GitCode（中国大陆可直连），失败则回退 GitHub（国际线路）
echo -e "${YELLOW}下载项目文件...${NC}"

# 检测当前目录是否已经是项目源码（用户已 git clone 的场景），是则直接复用，跳过重复 clone
if [ -f "main.py" ] && [ -f "requirements.txt" ] && [ -d "src" ]; then
    echo -e "${GREEN}检测到当前目录已是项目源码，跳过重复下载${NC}"
    SRC_DIR="$(pwd)"
else
    # 需要从远端拉取源码
    cd /tmp
    rm -rf NTRIPcaster

    # 可通过环境变量指定仓库源：REPO_SOURCE=github 或 REPO_SOURCE=gitcode
    if [ -z "$REPO_SOURCE" ]; then
        # 自动探测：先尝试 GitCode，再回退 GitHub
        echo -e "${BLUE}尝试从 GitCode 拉取源码（中国大陆线路）...${NC}"
        if git clone https://gitcode.com/rampump/NTRIPcaster.git 2>/dev/null; then
            echo -e "${GREEN}源码拉取成功（GitCode）${NC}"
        else
            echo -e "${YELLOW}GitCode 拉取失败，回退到 GitHub（国际线路）...${NC}"
            if git clone https://github.com/Rampump/NTRIPcaster.git 2>/dev/null; then
                echo -e "${GREEN}源码拉取成功（GitHub）${NC}"
            else
                echo -e "${RED}错误: 无法从 GitCode 或 GitHub 拉取源码，请检查网络连接${NC}"
                exit 1
            fi
        fi
    else
        # 用户显式指定了源
        case "$REPO_SOURCE" in
            github)
                echo -e "${BLUE}从 GitHub 拉取源码...${NC}"
                git clone https://github.com/Rampump/NTRIPcaster.git || { echo -e "${RED}错误: GitHub 拉取失败${NC}"; exit 1; }
                ;;
            gitcode)
                echo -e "${BLUE}从 GitCode 拉取源码...${NC}"
                git clone https://gitcode.com/rampump/NTRIPcaster.git || { echo -e "${RED}错误: GitCode 拉取失败${NC}"; exit 1; }
                ;;
            *)
                echo -e "${RED}错误: REPO_SOURCE 仅支持 github 或 gitcode${NC}"
                exit 1
                ;;
        esac
    fi
    SRC_DIR="/tmp/NTRIPcaster"
fi

cp -r "$SRC_DIR"/* $INSTALL_DIR/

# 复制并配置 config.ini
echo -e "${YELLOW}配置 config.ini...${NC}"
if [ -f $INSTALL_DIR/config.ini.example ]; then
    # 备份原始配置文件
    cp $INSTALL_DIR/config.ini.example $CONFIG_DIR/config.ini.original
    
    # 复制并修改配置文件
    cp $INSTALL_DIR/config.ini.example $CONFIG_DIR/config.ini
    
    # 更新配置文件中的路径（config.ini.example 里的实际字段名）
    sed -i "s|^path = .*|path = $INSTALL_DIR/data/2rtk.db|g" $CONFIG_DIR/config.ini
    sed -i "s|^log_dir = .*|log_dir = $LOG_DIR|g" $CONFIG_DIR/config.ini
    
    # 生成随机密钥（example 里 secret_key 为空，替换空值行）
    RANDOM_KEY=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 32 | head -n 1)
    sed -i "s|^secret_key = *$|secret_key = $RANDOM_KEY|g" $CONFIG_DIR/config.ini
    
    echo -e "${GREEN}配置文件已更新${NC}"
else
    echo -e "${RED}错误: 未找到 config.ini.example 文件${NC}"
    exit 1
fi

# 安装 Python 依赖
echo -e "${YELLOW}安装 Python 依赖...${NC}"

# 选择 pip 源：中国区自动切换清华镜像，失败回退官方 PyPI
PIP_INDEX_ARGS=""
PIP_TRUST_HOST=""
# 优先检测是否在中国大陆网络（通过连接性快速判断，超时 2 秒）
if curl -fsS --connect-timeout 2 https://mirrors.tuna.tsinghua.edu.cn >/dev/null 2>&1; then
    echo -e "${BLUE}检测到可用的清华镜像，将使用国内加速源${NC}"
    PIP_INDEX_ARGS="-i https://pypi.tuna.tsinghua.edu.cn/simple"
    PIP_TRUST_HOST="--trusted-host pypi.tuna.tsinghua.edu.cn"
else
    echo -e "${YELLOW}使用官方 PyPI 源${NC}"
fi

if ! $INSTALL_DIR/venv/bin/pip install --upgrade pip $PIP_INDEX_ARGS $PIP_TRUST_HOST; then
    echo -e "${YELLOW}pip 升级失败，尝试继续安装依赖...${NC}"
fi

if ! $INSTALL_DIR/venv/bin/pip install -r $INSTALL_DIR/requirements.txt $PIP_INDEX_ARGS $PIP_TRUST_HOST; then
    # 若指定镜像失败，回退官方源重试一次
    if [ -n "$PIP_INDEX_ARGS" ]; then
        echo -e "${YELLOW}清华镜像安装失败，回退官方 PyPI 重试...${NC}"
        if ! $INSTALL_DIR/venv/bin/pip install -r $INSTALL_DIR/requirements.txt; then
            echo -e "${RED}错误: Python 依赖安装失败${NC}"
            exit 1
        fi
    else
        echo -e "${RED}错误: Python 依赖安装失败${NC}"
        exit 1
    fi
fi

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