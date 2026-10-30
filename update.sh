#!/bin/bash
#
# NTRIP Caster 一键无损更新脚本 v1.2.0
#
# 功能：自动检测部署形态（源码+systemd / Docker），从可用源拉取最新版本，
#       更新前自动备份配置与数据库，更新后做健康检查，失败自动回滚。
#
# 用法：
#   sudo bash update.sh              # 自动判定源，GitCode/腾讯云优先，失败回退 GitHub/Docker Hub
#   sudo bash update.sh --force      # 跳过"是否已有新版本"的判定，强制更新
#   sudo REPO_SOURCE=github bash update.sh   # 源码形态：强制指定 GitHub 源
#   sudo REPO_SOURCE=gitcode bash update.sh  # 源码形态：强制指定 GitCode 源
#
# 注意：脚本必须在 root 下运行（源码形态需要写 /opt/2rtk、重启 systemd；Docker 形态需要操作 docker）。

set -uo pipefail

# ==================== 颜色定义 ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $1"; }
log_ok()    { echo -e "${CYAN}[OK]${NC} $1"; }

# ==================== 常量 ====================
SCRIPT_VERSION="1.4.0"
INSTALL_DIR="/opt/2rtk"
CONFIG_DIR="/etc/2rtk"
LOG_DIR="/var/log/2rtk"
SERVICE_NAME="2rtk"

# 双源仓库地址
GITCODE_REPO="https://gitcode.com/rampump/NTRIPcaster.git"
GITHUB_REPO="https://github.com/Rampump/NTRIPcaster.git"

# 双镜像源
TCR_IMAGE="ccr.ccs.tencentyun.com/2rtk/ntripcaster:latest"
DOCKERHUB_IMAGE="2rtk/ntripcaster:latest"

# 备份目录
BACKUP_ROOT="/var/backups/ntripcaster"
BACKUP_DIR="${BACKUP_ROOT}/$(date +%Y%m%d_%H%M%S)"

FORCE_UPDATE=0

# 可选环境变量默认值：本脚本开启了 `set -u`，未定义变量被引用会直接报
# "unbound variable" 并中断更新。REPO_SOURCE 用于强制指定更新源（gitcode/github/tcr/dockerhub），
# 未显式传入时必须先给出空值，否则 `[ -n "$REPO_SOURCE" ]` 会触发 unbound variable。
REPO_SOURCE="${REPO_SOURCE:-}"

# ==================== 参数解析 ====================
for arg in "$@"; do
    case "$arg" in
        --force|-f)
            FORCE_UPDATE=1
            ;;
        --source=*)
            REPO_SOURCE="${arg#*=}"
            ;;
        --help|-h)
            echo "用法: sudo bash update.sh [--force] [--source=gitcode|github|tcr|dockerhub]"
            echo "  --force             跳过版本判定，强制执行更新"
            echo "  --source=<源>       强制指定更新源"
            echo "    source 形态: gitcode | github"
            echo "    docker 形态: tcr | dockerhub"
            exit 0
            ;;
    esac
done

# ==================== 权限检查 ====================
if [ "$EUID" -ne 0 ]; then
    log_error "请以 root 运行本脚本"
    log_warn "普通用户请用: sudo bash update.sh"
    exit 1
fi

# ==================== 通用工具函数 ====================
# 判断是否存在指定名称的 docker 容器。
# 注意：本脚本开启了 `set -o pipefail`，若写成 `docker ps ... | grep -q xxx`，
# grep -q 命中后会立刻退出，导致上游 docker 收到 SIGPIPE 异常终止（退出码 141），
# pipefail 会让整个管道判定为失败 —— 明明存在容器却被判为不存在。
# 因此这里改为纯 bash 字符串精确匹配，不产生管道。
has_container() {
    local target="$1" mode="${2:-all}" list
    if [ "$mode" = "running" ]; then
        list=$(docker ps --format '{{.Names}}' 2>/dev/null || true)
    else
        list=$(docker ps -a --format '{{.Names}}' 2>/dev/null || true)
    fi
    case $'\n'"$list"$'\n' in
        *$'\n'"$target"$'\n'*) return 0 ;;
    esac
    return 1
}

# ==================== 部署形态检测 ====================
# 在多个 systemd 单元目录中查找服务文件（不同发行版可能放在 /etc 或 /lib 或 /usr/lib）
find_unit_file() {
    local d
    for d in /etc/systemd/system /lib/systemd/system /usr/lib/systemd/system /run/systemd/system; do
        if [ -f "$d/${SERVICE_NAME}.service" ]; then
            echo "$d/${SERVICE_NAME}.service"
            return 0
        fi
    done
    return 1
}

detect_deploy_mode() {
    # 1. 源码 + systemd 形态：/opt/2rtk/main.py 存在 + systemd 服务文件存在。
    #    注意：install.sh 用 `cp -r "$SRC_DIR"/*` 复制源码，`*` 不匹配隐藏的 .git，
    #    所以实际部署后 /opt/2rtk 通常没有 .git。这里以 main.py + 服务文件为准，更可靠。
    #    服务文件用文件存在性判断，避免依赖 `systemctl list-unit-files | grep -q`：
    #    本脚本开启了 pipefail，grep -q 提前退出会让上游 systemctl 收到 SIGPIPE（141），
    #    导致管道整体判定为失败，明明已安装却被误判为 unknown。
    if [ -f "$INSTALL_DIR/main.py" ] && find_unit_file >/dev/null 2>&1; then
        echo "source"
        return
    fi

    # 2. Docker 形态：存在 ntrip-caster 容器（docker run 或 docker compose 均会创建该容器名）
    if command -v docker >/dev/null 2>&1; then
        if has_container ntrip-caster; then
            echo "docker"
            return
        fi
    fi

    # 3. 都无法确认
    echo "unknown"
}

DEPLOY_MODE=$(detect_deploy_mode)

if [ "$DEPLOY_MODE" = "unknown" ]; then
    log_error "无法识别部署形态"
    echo ""
    echo "  检测依据："
    echo "    - 源码形态：$INSTALL_DIR/main.py 存在 + 系统服务文件 ${SERVICE_NAME}.service 存在"
    echo "    - Docker 形态：存在名为 ntrip-caster 的容器"
    echo ""
    echo "  当前环境诊断："
    if [ -f "$INSTALL_DIR/main.py" ]; then
        echo "    [√] $INSTALL_DIR/main.py 存在"
    else
        echo "    [×] $INSTALL_DIR/main.py 不存在（安装目录不对或未安装）"
    fi
    _unit=""
    if _unit=$(find_unit_file); then
        echo "    [√] 系统服务文件存在：$_unit"
    else
        echo "    [×] 未找到 ${SERVICE_NAME}.service 服务文件（未用 install.sh 安装 systemd 服务）"
    fi
    if command -v docker >/dev/null 2>&1 && has_container ntrip-caster; then
        echo "    [√] Docker 容器 ntrip-caster 存在"
    else
        echo "    [×] 未检测到 Docker 容器 ntrip-caster"
    fi
    echo ""
    log_warn "请确认你已通过 install.sh 或 docker-deploy.sh 正确部署后再更新"
    log_warn "如确已用 install.sh 安装，请检查：1) 是否以 root 执行；2) 服务名是否为 $SERVICE_NAME；3) 安装目录是否为 $INSTALL_DIR"
    exit 1
fi

log_info "检测到部署形态: $DEPLOY_MODE"

# ==================== 备份函数 ====================
do_backup() {
    log_step "创建备份..."
    mkdir -p "$BACKUP_DIR"

    # 备份配置文件
    if [ -f "$CONFIG_DIR/config.ini" ]; then
        cp -a "$CONFIG_DIR/config.ini" "$BACKUP_DIR/config.ini" 2>/dev/null
        log_ok "已备份配置文件: $CONFIG_DIR/config.ini"
    fi

    # 备份数据库
    DB_PATH="${INSTALL_DIR}/data/2rtk.db"
    if [ -f "$DB_PATH" ]; then
        cp -a "$DB_PATH" "$BACKUP_DIR/2rtk.db" 2>/dev/null
        log_ok "已备份数据库: $DB_PATH"
    fi

    # Docker 形态额外记录当前镜像 ID（用于回滚）
    if [ "$DEPLOY_MODE" = "docker" ]; then
        docker inspect --format '{{.Image}}' ntrip-caster 2>/dev/null > "$BACKUP_DIR/old_image_id.txt" || true
    fi

    log_ok "备份完成，目录: $BACKUP_DIR"
}

# ==================== 回滚函数 ====================
do_rollback() {
    log_warn "开始回滚..."
    if [ "$DEPLOY_MODE" = "source" ]; then
        # 源码形态：优先回滚代码（若有备份），再恢复数据库和配置
        if [ -d "$BACKUP_DIR/opt_2rtk_bak" ]; then
            log_step "回滚代码..."
            if command -v rsync >/dev/null 2>&1; then
                rsync -a --delete \
                    --exclude 'venv/' \
                    --exclude 'data/' \
                    --exclude 'config.ini' \
                    --exclude '.git/' \
                    "$BACKUP_DIR/opt_2rtk_bak/" "$INSTALL_DIR/" 2>/dev/null
            else
                cp -a "$BACKUP_DIR/opt_2rtk_bak/." "$INSTALL_DIR/" 2>/dev/null
            fi
            log_ok "代码已回滚"
        fi
        # 恢复数据库
        if [ -f "$BACKUP_DIR/2rtk.db" ]; then
            cp -a "$BACKUP_DIR/2rtk.db" "${INSTALL_DIR}/data/2rtk.db" 2>/dev/null
            log_ok "已回滚数据库"
        fi
        # 恢复配置文件
        if [ -f "$BACKUP_DIR/config.ini" ]; then
            cp -a "$BACKUP_DIR/config.ini" "$CONFIG_DIR/config.ini" 2>/dev/null
            log_ok "已回滚配置文件"
        fi
        # 恢复 config.ini 软链
        if [ ! -L "$INSTALL_DIR/config.ini" ] && [ -f "$CONFIG_DIR/config.ini" ]; then
            ln -sf "$CONFIG_DIR/config.ini" "$INSTALL_DIR/config.ini" 2>/dev/null
        fi
        # 重启服务使其回到旧版本运行状态。
        # 用同一套「彻底停止 + 确认端口释放」逻辑：更新失败时新进程可能仍占着端口
        # 或在 auto-restart 循环中，直接 start 会继续因端口冲突起不来。
        if stop_service_fully; then
            start_service
            log_ok "已重启服务（旧版本）"
        else
            log_warn "端口仍被占用，回滚后服务未能自动启动"
            log_warn "请手动处理后执行: systemctl start $SERVICE_NAME"
        fi
    fi
    log_warn "已回滚，备份保留在 $BACKUP_DIR"
}

# ==================== 健康检查 ====================
# 读取 config.ini 中指定 section 的 port 值
_get_cfg_port() {
    local section="$1" default="$2" port=""
    if [ -f "$CONFIG_DIR/config.ini" ]; then
        port=$(awk -F= -v sec="$section" '
            $0 ~ "^[[:space:]]*\\[" sec "\\]" { ins=1; next }
            /^[[:space:]]*\[/                 { ins=0 }
            ins && /^[[:space:]]*port[[:space:]]*=/ { gsub(/[[:space:]]/, "", $2); print $2; exit }
        ' "$CONFIG_DIR/config.ini" 2>/dev/null)
    fi
    echo "${port:-$default}"
}

# 读取 config.ini 中 [web] port 的实际值（默认 5757），避免用户自定义端口后探活误判
get_web_port() {
    _get_cfg_port "web" "5757"
}

# 读取 config.ini 中 [ntrip] port 的实际值（默认 2101）
get_ntrip_port() {
    _get_cfg_port "ntrip" "2101"
}

# 查询监听指定端口的进程 PID（无占用则输出空）。
# ss / lsof / fuser 三重探测，任一可用即可，避免单一工具缺失或权限问题导致漏判。
port_holder() {
    local port="$1" pid=""

    if command -v ss >/dev/null 2>&1; then
        pid=$(ss -lntp 2>/dev/null \
            | awk -v p=":${port}\$" '$4 ~ p' \
            | awk 'match($0, /pid=[0-9]+/) {print substr($0, RSTART+4, RLENGTH-4); exit}')
    fi
    if [ -z "$pid" ] && command -v lsof >/dev/null 2>&1; then
        pid=$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==1{print}')
    fi
    if [ -z "$pid" ] && command -v fuser >/dev/null 2>&1; then
        pid=$(fuser "$port/tcp" 2>/dev/null | tr -s ' ' '\n' | awk '/^[0-9]+$/{print; exit}')
    fi

    echo "$pid"
}

# 端口是否真的被占用。
# 直接用 python 尝试 bind 判断，比解析 ss/lsof 输出可靠得多：
# 不受工具缺失、输出格式变化、权限不足影响（SO_REUSEADDR 让 TIME_WAIT 不算占用）。
port_in_use() {
    local port="$1" py=""
    [ -z "$port" ] && return 1

    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        py="$INSTALL_DIR/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="python3"
    else
        # 没有任何 python 可用时退化为 ss/lsof 探测
        if [ -n "$(port_holder "$port")" ]; then
            return 0
        fi
        return 1
    fi

    "$py" - "$port" >/dev/null 2>&1 <<'PY'
import socket, sys

port = int(sys.argv[1])

# 1) 优先尝试连接：能连上就说明有进程在 LISTEN。
#    这个判断跨平台语义一致，不受 SO_REUSEADDR 差异影响
#    （Windows 的 SO_REUSEADDR 允许重复绑定监听端口，Linux 不允许）。
c = socket.socket()
c.settimeout(0.6)
try:
    c.connect(("127.0.0.1", port))
    sys.exit(0)          # 0 = 被占用
except OSError:
    pass
finally:
    c.close()

# 2) 再尝试 bind 兜底（TIME_WAIT 不算占用，故设 SO_REUSEADDR）
b = socket.socket()
try:
    b.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    b.bind(("0.0.0.0", port))
except OSError:
    sys.exit(0)          # 被占用
finally:
    b.close()

sys.exit(1)              # 1 = 空闲
PY
}

# 直接打印指定端口的监听明细（含进程名/PID），用于失败时给用户看
print_port_detail() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -lntp 2>/dev/null | awk -v pat=":${port}\$" '$4 ~ pat' | sed 's/^/      /'
    elif command -v netstat >/dev/null 2>&1; then
        netstat -lntp 2>/dev/null | awk -v pat=":${port}\$" '$4 ~ pat' | sed 's/^/      /'
    fi
}

# 列出所有仍被占用的端口（输出的每一项形如 "5757:12345"，无占用则为空）
busy_ports() {
    local p h out=""
    for p in $(get_ntrip_port) $(get_web_port); do
        [ -z "$p" ] && continue
        if port_in_use "$p"; then
            h=$(port_holder "$p")
            out="$out $p:${h:-unknown}"
        fi
    done
    echo "$out"
}

# 两阶段探活：
#   1) 服务进程层面：systemd 服务 active / docker 容器 running
#   2) 应用层面：Web 管理接口 HTTP 可访问（仅当 curl 可用时；避免服务被 Restart 反复拉起仍判为健康）
health_check() {
    local tries=0
    local max_tries=15
    local active_count=0
    local port
    port=$(get_web_port)

    while [ "$tries" -lt "$max_tries" ]; do
        tries=$((tries + 1))

        # Docker 形态：容器处于 running 即视为启动成功
        if [ "$DEPLOY_MODE" = "docker" ]; then
            if has_container ntrip-caster running; then
                return 0
            fi
            sleep 2
            continue
        fi

        # 源码形态：服务必须处于 active
        if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            # 有 curl 时优先做 HTTP 探活（最严格）
            if command -v curl >/dev/null 2>&1; then
                if curl -fsS --connect-timeout 2 "http://127.0.0.1:${port}/" >/dev/null 2>&1; then
                    return 0
                fi
            fi
            # HTTP 未响应：累计"持续 active"次数，连续 5 次（约 10 秒）稳定运行也判定为启动成功。
            # 这样即使用户自定义了 Web 端口、或关闭了 Web 界面，也不会把成功的更新误判为失败并回滚。
            active_count=$((active_count + 1))
            if [ "$active_count" -ge 5 ]; then
                log_warn "服务已持续稳定运行约 $((active_count * 2)) 秒（Web 端口 $port 未响应），判定为启动成功"
                return 0
            fi
        else
            # 服务不是 active（崩了或正在重启），重置连续计数
            active_count=0
        fi

        sleep 2
    done
    return 1
}

# 重启服务：显式 stop -> 等旧进程真正退出（超时兜底 SIGKILL）-> 确认端口已释放 -> start
#
# 不直接 systemctl restart 的原因：
#   1) 旧进程优雅关闭要依次 join 转发器、线程池、推送线程，耗时可能十几秒；
#      systemd 默认 TimeoutStopSec=90s，期间 restart 一直阻塞，看起来像卡死。
#   2) 旧进程若没干净退出，2101/5757 端口会一直被占用，新进程 bind 失败起不来，
#      表现就是「服务重启后未正常运行」。
# ==================== 停止 / 启动 ====================
# 彻底停止 NTRIP Caster，并确保 2101 / 5757 真正释放。
#
# 为什么必须这么麻烦：
#   systemctl stop 只保证「systemd 托管的主进程」退出。实际环境中端口仍被占用的情况很常见：
#     - 曾手动 `python main.py` / `bash start.sh` 启动过，进程不在 systemd 托管下；
#     - 从 Web 界面点「重启程序」时进程脱离 systemd 重新拉起；
#     - 旧进程优雅关闭较慢，主进程已退但监听 socket 尚未释放。
#   只要端口没释放，新进程启动时 check_environment 就会报「Web端口 5757 已被占用」
#   然后 status=1 退出，systemd 反复 auto-restart，表现为「服务重启后未正常运行」。
stop_service_fully() {
    log_step "停止 NTRIP Caster..."

    # 先告诉用户当前状态（服务是否在跑、端口被谁占）
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        log_info "检测到 systemd 服务正在运行，开始停止"
    else
        log_info "systemd 服务当前未处于运行状态"
    fi

    local pre_busy
    pre_busy=$(busy_ports)
    [ -n "$pre_busy" ] && log_info "当前端口占用:$pre_busy"

    # 1) 停止 systemd 服务
    systemctl stop "$SERVICE_NAME" >/dev/null 2>&1

    local waited=0
    while systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; do
        if [ "$waited" -ge 20 ]; then
            log_warn "服务 20 秒内未退出，强制结束（SIGKILL）"
            systemctl kill -s SIGKILL "$SERVICE_NAME" >/dev/null 2>&1
            sleep 2
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done

    # 2) 清理所有「未受 systemd 托管」的本程序残留进程。
    #    典型来源：曾手动 `python main.py` 启动、或从 Web 界面重启后脱离 systemd。
    #    这类进程会一直占着 2101/5757，是「新进程启动即退出」的最常见原因。
    #    只按入口文件完整路径匹配，不会误杀其他 python 进程。
    #    不依赖 pkill，用 ps + kill，避免精简系统缺少 procps 工具。
    local pids="" pid args still=""
    pids=$(ps -eo pid=,args= 2>/dev/null | while read -r pid args; do
        case "$args" in
            *"$INSTALL_DIR/main.py"*) echo "$pid" ;;
        esac
    done)

    if [ -n "$pids" ]; then
        log_warn "发现未受 systemd 托管的残留进程（PID: $(echo "$pids" | tr '\n' ' ')），正在结束..."
        kill -TERM $pids 2>/dev/null
        sleep 2
        for pid in $pids; do
            if kill -0 "$pid" 2>/dev/null; then
                still="$still $pid"
            fi
        done
        if [ -n "$still" ]; then
            log_warn "残留进程未响应 SIGTERM，强制结束:$still"
            kill -9 $still 2>/dev/null
            sleep 1
        fi
    fi

    # 3) 等待端口真正释放（bind 探测，最多 15 秒）
    local left=15 p busy
    while [ "$left" -gt 0 ]; do
        busy=0
        for p in $(get_ntrip_port) $(get_web_port); do
            port_in_use "$p" && busy=1
        done
        [ "$busy" -eq 0 ] && break
        sleep 1
        left=$((left - 1))
    done

    # 4) 最终确认：还有占用就报出进程并判定失败
    local remaining
    remaining=$(busy_ports)
    if [ -n "$remaining" ]; then
        log_error "端口未能释放:$remaining"
        echo ""
        echo "  占用详情（PID / 进程名）："
        for p in $(get_ntrip_port) $(get_web_port); do
            if port_in_use "$p"; then
                print_port_detail "$p"
                local h
                h=$(port_holder "$p")
                if [ -n "$h" ]; then
                    ps -fp "$h" 2>/dev/null | tail -1 | sed 's/^/      /'
                fi
            fi
        done
        echo ""
        log_warn "请手动结束上述进程后重试，例如："
        echo "    ss -lntp | grep -E ':(2101|5757)'"
        echo "    kill -9 <PID>"
        echo ""
        return 1
    fi

    if [ "$waited" -gt 0 ] && [ "$waited" -lt 20 ]; then
        log_info "旧进程已退出（等待 ${waited}s）"
    fi
    log_ok "服务已完全停止，端口已释放"
    return 0
}

# 启动服务（前置条件：stop_service_fully 已确认端口空闲）
start_service() {
    log_step "启动服务..."
    systemctl start "$SERVICE_NAME" >/dev/null 2>&1
}

# 启动失败时输出诊断信息，直接暴露真实原因（而不是只说「未正常运行」）
print_diagnostics() {
    echo ""
    echo "  ---- 诊断信息 ----"

    echo "  [systemctl status]"
    systemctl status "$SERVICE_NAME" --no-pager -l 2>&1 | head -14 | sed 's/^/    /'

    echo ""
    echo "  [journalctl 最近日志]"
    journalctl -u "$SERVICE_NAME" -n 15 --no-pager 2>/dev/null | sed 's/^/    /'

    if [ -f "$LOG_DIR/errors.log" ]; then
        echo ""
        echo "  [errors.log 尾部]"
        tail -12 "$LOG_DIR/errors.log" 2>/dev/null | sed 's/^/    /'
    fi

    local p holder
    for p in $(get_ntrip_port) $(get_web_port); do
        [ -z "$p" ] && continue
        if port_in_use "$p"; then
            holder=$(port_holder "$p")
            echo ""
            echo "  [端口 $p 被占用，进程 PID: ${holder:-未知}]"
            print_port_detail "$p"
            if [ -n "$holder" ]; then
                ps -fp "$holder" 2>/dev/null | tail -1 | sed 's/^/      /'
            fi
        fi
    done
    echo "  ------------------"
    echo ""
}

# ==================== 源码 + systemd 更新 ====================
update_source() {
    log_step "更新源码（systemd 形态）..."

    if [ ! -d "$INSTALL_DIR" ]; then
        log_error "未找到安装目录 $INSTALL_DIR"
        exit 1
    fi

    # 记录旧版本号（用于判定是否需要更新）
    local old_version=""
    if [ -f "$CONFIG_DIR/config.ini" ]; then
        old_version=$(grep -E '^\s*version\s*=' "$CONFIG_DIR/config.ini" | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]')
    fi
    [ -z "$old_version" ] && old_version=$(grep -E '^\s*version\s*=' "$INSTALL_DIR/config.ini.example" 2>/dev/null | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]')

    # 下载最新源码到临时目录（双源自动降级）
    local clone_url="$GITCODE_REPO"
    local tmp_clone="/tmp/ntripcaster_update"
    rm -rf "$tmp_clone"

    log_info "尝试从 GitCode 拉取源码（中国大陆线路）..."
    if [ -n "$REPO_SOURCE" ]; then
        case "$REPO_SOURCE" in
            gitcode) clone_url="$GITCODE_REPO" ;;
            github)  clone_url="$GITHUB_REPO" ;;
            *) log_error "REPO_SOURCE 仅支持 gitcode 或 github"; exit 1 ;;
        esac
        log_info "从 $(echo "$clone_url" | sed 's|https://||') 拉取..."
        if ! git clone --depth 1 "$clone_url" "$tmp_clone" 2>/dev/null; then
            log_error "拉取失败，请检查网络"
            exit 1
        fi
    else
        # 自动判定：GitCode 优先，失败回退 GitHub
        if ! git clone --depth 1 "$GITCODE_REPO" "$tmp_clone" 2>/dev/null; then
            log_warn "GitCode 拉取失败，回退 GitHub（国际线路）..."
            clone_url="$GITHUB_REPO"
            if ! git clone --depth 1 "$GITHUB_REPO" "$tmp_clone" 2>/dev/null; then
                log_error "无法从 GitCode 或 GitHub 拉取源码，请检查网络"
                exit 1
            fi
        fi
    fi
    log_ok "源码拉取成功（$(echo "$clone_url" | sed 's|https://||')）"

    # 读新版本号
    local new_version=""
    new_version=$(grep -E '^\s*version\s*=' "$tmp_clone/config.ini.example" 2>/dev/null | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]')
    [ -z "$new_version" ] && new_version=$(grep -E '^APP_VERSION' "$tmp_clone/src/config.py" 2>/dev/null | head -1 | sed "s/.*'\([^']*\)'.*/\1/")

    # 判定是否有更新
    if [ "$FORCE_UPDATE" -eq 0 ] && [ -n "$old_version" ] && [ -n "$new_version" ] && [ "$old_version" = "$new_version" ]; then
        log_ok "已是最新版本 v$old_version，无需更新"
        rm -rf "$tmp_clone" "$BACKUP_DIR" 2>/dev/null
        exit 0
    fi

    if [ -n "$old_version" ] && [ -n "$new_version" ]; then
        log_info "发现新版本: v$old_version -> v$new_version"
    else
        log_info "开始更新（版本号无法比对，按需强制更新）"
    fi

    # 升级前必须先彻底停止正在运行的服务并释放端口。
    # 否则新进程启动时 check_environment 会发现 2101/5757 仍被旧实例占用，
    # 直接 status=1 退出，systemd 反复 auto-restart，表现为「服务重启后未正常运行」。
    # 放在备份/同步代码之前：停不下来就直接中止，此时还没改动任何文件。
    if ! stop_service_fully; then
        log_error "无法停止旧服务或释放端口，已中止更新（未做任何修改）"
        rm -rf "$tmp_clone"
        exit 1
    fi

    # 服务已静止，重新备份一次数据库：do_backup 是在服务运行期间做的，
    # 这里覆盖为静止状态的一致快照，回滚时更安全。
    if [ -f "$INSTALL_DIR/data/2rtk.db" ] && [ -d "$BACKUP_DIR" ]; then
        cp -a "$INSTALL_DIR/data/2rtk.db" "$BACKUP_DIR/2rtk.db" 2>/dev/null \
            && log_ok "已刷新数据库备份（服务静止状态）"
    fi

    # 备份旧代码（用于失败回滚）
    log_step "备份旧代码..."
    cp -a "$INSTALL_DIR" "$BACKUP_DIR/opt_2rtk_bak" 2>/dev/null
    log_ok "旧代码已备份"

    # 同步新代码到安装目录（排除运行时目录：venv、data、config.ini 软链、日志）
    log_step "同步新代码..."

    # rsync 的 --delete/--exclude 语义最可靠；install.sh 默认不装 rsync，
    # 这里尝试就地补齐（失败则退回 cp 兜底，不影响更新）。
    if ! command -v rsync >/dev/null 2>&1; then
        log_info "未检测到 rsync，尝试安装以更可靠地同步代码..."
        if command -v apt-get >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y rsync >/dev/null 2>&1 || true
        elif command -v dnf >/dev/null 2>&1; then
            dnf install -y rsync >/dev/null 2>&1 || true
        elif command -v yum >/dev/null 2>&1; then
            yum install -y rsync >/dev/null 2>&1 || true
        fi
    fi

    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete \
            --exclude 'venv/' \
            --exclude 'data/' \
            --exclude 'config.ini' \
            --exclude '.git/' \
            "$tmp_clone/" "$INSTALL_DIR/" 2>/dev/null || {
                log_error "rsync 同步失败"
                rm -rf "$tmp_clone"
                do_rollback
                exit 1
            }
    else
        # 无 rsync 时用 cp 兜底：先清空旧代码（保留 venv/data/config.ini），再整体复制。
        # 注意用 "$tmp_clone/." 而非 "$tmp_clone"/*，否则会漏掉 .env.example 等点文件。
        log_warn "未找到 rsync，使用 cp 兜底同步"
        find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 \
            ! -name 'venv' ! -name 'data' ! -name 'config.ini' \
            -exec rm -rf {} + 2>/dev/null
        if ! cp -a "$tmp_clone/." "$INSTALL_DIR/" 2>/dev/null; then
            log_error "cp 同步失败"
            rm -rf "$tmp_clone"
            do_rollback
            exit 1
        fi
        # 兜底方案不会自动删除「新版已移除的旧文件」，这里清掉 .git 以外的残留不受影响，故不额外处理
    fi
    rm -rf "$tmp_clone"
    log_ok "代码同步完成"

    # 恢复 config.ini 软链（rsync --exclude config.ini 会保留原软链，此处兜底重建）
    if [ ! -L "$INSTALL_DIR/config.ini" ] && [ -f "$CONFIG_DIR/config.ini" ]; then
        ln -sf "$CONFIG_DIR/config.ini" "$INSTALL_DIR/config.ini" 2>/dev/null
    fi

    # 同步 systemd 服务文件：若仓库带了服务文件模板则复用，否则按 install.sh 同款方式重建，
    # 确保服务启动方式（WorkingDirectory/Environment/ExecStart）与最新版本一致，不落后。
    log_step "同步 systemd 服务文件..."
    if [ -f "$INSTALL_DIR/2rtk.service" ]; then
        cp -a "$INSTALL_DIR/2rtk.service" "/etc/systemd/system/${SERVICE_NAME}.service" 2>/dev/null
        log_ok "已从仓库同步服务文件"
    else
        cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
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
TimeoutStopSec=20
KillMode=mixed
StandardOutput=append:$LOG_DIR/main.log
StandardError=append:$LOG_DIR/errors.log

[Install]
WantedBy=multi-user.target
EOF
        log_ok "已按 install.sh 同款方式重建服务文件"
    fi

    # 兜底：确保 TimeoutStopSec 存在。
    # 老版本 install.sh 生成的服务文件没有这一项，systemd 会退化为默认 90 秒：
    # 重启时旧进程迟迟不退，restart 长时间阻塞，2101/5757 端口也迟迟不释放。
    local _unit="/etc/systemd/system/${SERVICE_NAME}.service"
    if [ -f "$_unit" ] && ! grep -q '^TimeoutStopSec=' "$_unit" 2>/dev/null; then
        if awk '
            /^\[Service\]/ && !done { print; print "TimeoutStopSec=20"; print "KillMode=mixed"; done=1; next }
            { print }
        ' "$_unit" > "${_unit}.new" 2>/dev/null && [ -s "${_unit}.new" ]; then
            cat "${_unit}.new" > "$_unit"
            log_info "已为服务文件补充 TimeoutStopSec=20 / KillMode=mixed"
        fi
        rm -f "${_unit}.new"
    fi
    systemctl daemon-reload 2>/dev/null

    # 同步运行时版本号：config.ini 里 [app] version 属于程序元数据（不是用户配置），
    # 安装时从 example 复制一次后就固定了，不会随代码更新而变。若不更新会导致两个问题：
    #   1) Web 界面/日志显示的版本永远停留在旧版本；
    #   2) 之后每次跑 update.sh 都会重复判定「发现新版本」并重复下载同步。
    # 只替换 [app] 段内的 version 键，不碰其他段与其他键；用 cat 覆写以保留原文件权限。
    if [ -n "$new_version" ] && [ -f "$CONFIG_DIR/config.ini" ]; then
        local _cfg_tmp="${CONFIG_DIR}/.config.ini.ver.tmp"
        if awk -v v="$new_version" '
            /^[[:space:]]*\[/ { sec=$0; gsub(/[\[\][:space:]]/, "", sec) }
            sec=="app" && !done && /^[[:space:]]*version[[:space:]]*=/ { print "version = " v; done=1; next }
            { print }
        ' "$CONFIG_DIR/config.ini" > "$_cfg_tmp" 2>/dev/null && [ -s "$_cfg_tmp" ]; then
            cat "$_cfg_tmp" > "$CONFIG_DIR/config.ini"
            log_ok "配置版本号已同步为 v$new_version"
        else
            log_warn "配置版本号同步失败（不影响程序运行）"
        fi
        rm -f "$_cfg_tmp"
    fi

    # 重新安装依赖（requirements.txt 可能变化，用清华镜像加速 + 回退）
    # 必须带 --upgrade：
    #   requirements.txt 只 pin 了直接依赖，bidict / python-engineio 等是间接依赖。
    #   不带 --upgrade 时 pip 认为 python-socketio==5.8.0「已安装且满足」，直接跳过，
    #   于是旧 venv 里 vestigial 的 bidict 0.16.0（要求 >=0.21.0）不会被修正，
    #   新进程 import socketio 就炸，表现为「服务重启后未正常运行」并回滚。
    log_step "更新 Python 依赖..."
    local PIP_ARGS=""
    if curl -fsS --connect-timeout 2 https://mirrors.tuna.tsinghua.edu.cn >/dev/null 2>&1; then
        PIP_ARGS="-i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn"
        log_info "使用清华镜像加速"
    fi

    local pip_ok=0
    if "$INSTALL_DIR/venv/bin/pip" install --upgrade -r "$INSTALL_DIR/requirements.txt" $PIP_ARGS; then
        pip_ok=1
    elif [ -n "$PIP_ARGS" ]; then
        log_warn "清华镜像安装失败，回退官方源..."
        if "$INSTALL_DIR/venv/bin/pip" install --upgrade -r "$INSTALL_DIR/requirements.txt"; then
            pip_ok=1
        fi
    fi
    if [ "$pip_ok" -ne 1 ]; then
        log_error "依赖安装失败"
        do_rollback
        exit 1
    fi

    # 依赖一致性校验：pip check 能发现「间接依赖版本不满足」这类静默冲突
    if ! "$INSTALL_DIR/venv/bin/pip" check >/dev/null 2>&1; then
        log_warn "检测到依赖版本冲突，尝试修复："
        "$INSTALL_DIR/venv/bin/pip" check 2>&1 | awk 'NF' | head -8 | sed 's/^/    /'
        "$INSTALL_DIR/venv/bin/pip" install --upgrade -r "$INSTALL_DIR/requirements.txt" $PIP_ARGS >/dev/null 2>&1 \
            || "$INSTALL_DIR/venv/bin/pip" install --upgrade -r "$INSTALL_DIR/requirements.txt" >/dev/null 2>&1
    fi
    log_ok "依赖更新完成"

    # 启动前自检：真实导入一次核心模块。
    # 否则依赖坏了只能看到「服务未正常运行」，看不到真正的 ImportError 原因。
    log_step "启动前自检（导入核心模块）..."
    local import_err import_rc
    import_err=$("$INSTALL_DIR/venv/bin/python" -c "
import sys
mods = ['flask', 'flask_socketio', 'socketio', 'engineio', 'werkzeug',
        'psutil', 'pyrtcm', 'pyproj', 'reverse_geocode']
bad = []
for m in mods:
    try:
        __import__(m)
    except Exception as e:
        bad.append('  %s -> %s: %s' % (m, type(e).__name__, e))
if bad:
    print('\n'.join(bad))
    sys.exit(1)
" 2>&1)
    import_rc=$?

    if [ "$import_rc" -ne 0 ]; then
        log_warn "核心依赖导入失败，尝试强制重装依赖..."
        echo "$import_err" | head -12 | sed 's/^/    /'
        "$INSTALL_DIR/venv/bin/pip" install --upgrade --force-reinstall \
            -r "$INSTALL_DIR/requirements.txt" $PIP_ARGS >/dev/null 2>&1 \
            || "$INSTALL_DIR/venv/bin/pip" install --upgrade --force-reinstall \
            -r "$INSTALL_DIR/requirements.txt" >/dev/null 2>&1

        import_err=$("$INSTALL_DIR/venv/bin/python" -c "
import sys
mods = ['flask', 'flask_socketio', 'socketio', 'engineio', 'werkzeug',
        'psutil', 'pyrtcm', 'pyproj', 'reverse_geocode']
bad = []
for m in mods:
    try:
        __import__(m)
    except Exception as e:
        bad.append('  %s -> %s: %s' % (m, type(e).__name__, e))
if bad:
    print('\n'.join(bad))
    sys.exit(1)
" 2>&1)
        import_rc=$?
        if [ "$import_rc" -ne 0 ]; then
            log_error "依赖无法导入，新版本无法启动，回滚到旧版本"
            echo "$import_err" | head -12 | sed 's/^/    /'
            echo ""
            echo "  可手动排查：$INSTALL_DIR/venv/bin/pip check"
            do_rollback
            exit 1
        fi
    fi
    log_ok "核心依赖导入正常"

    # 启动服务（端口已在 stop_service_fully 中确认释放）
    start_service

    # 健康检查
    if ! health_check; then
        log_error "服务重启后未正常运行"
        print_diagnostics
        do_rollback
        exit 1
    fi
    log_ok "服务已正常运行"
}

# ==================== Docker 更新 ====================
update_docker() {
    log_step "更新 Docker 镜像..."

    if ! command -v docker >/dev/null 2>&1; then
        log_error "未找到 docker 命令"
        exit 1
    fi

    # 读取当前容器的镜像 ID 和卷挂载情况
    local old_image_id
    old_image_id=$(docker inspect --format '{{.Image}}' ntrip-caster 2>/dev/null || echo "")

    # 判断容器是否挂了持久化卷（决定能否安全重建）
    # 本项目数据库/配置/日志都在容器内 /app/ 下，有任一 /app/ 子目录的挂载即视为有持久化卷
    # 同样避免 `... | grep -q` 在 pipefail 下因 SIGPIPE 误判，改用字符串匹配
    local has_volume
    local _mounts
    _mounts=$(docker inspect --format '{{range .Mounts}}{{.Destination}} {{end}}' ntrip-caster 2>/dev/null || true)
    case " $_mounts " in
        *" /app/"*) has_volume="yes" ;;
        *)          has_volume="no"  ;;
    esac

    # 确定镜像源
    local image="$TCR_IMAGE"
    if [ -n "$REPO_SOURCE" ]; then
        case "$REPO_SOURCE" in
            dockerhub) image="$DOCKERHUB_IMAGE" ;;
            tcr)       image="$TCR_IMAGE" ;;
            *) log_error "Docker 形态 REPO_SOURCE 仅支持 tcr 或 dockerhub"; exit 1 ;;
        esac
    fi

    # 拉取镜像（腾讯云 TCR 优先，失败回退 Docker Hub）
    log_info "从 $image 拉取镜像..."
    if ! docker pull "$image" 2>/dev/null; then
        if [ -z "$REPO_SOURCE" ] && [ "$image" = "$TCR_IMAGE" ]; then
            log_warn "腾讯云镜像拉取失败，回退 Docker Hub..."
            image="$DOCKERHUB_IMAGE"
            if ! docker pull "$image" 2>/dev/null; then
                log_error "Docker Hub 也拉取失败，请检查网络"
                exit 1
            fi
        else
            log_error "镜像拉取失败，请检查网络"
            exit 1
        fi
    fi
    log_ok "镜像拉取成功"

    # 判定是否有更新（对比新旧镜像 ID）
    local new_image_id
    new_image_id=$(docker inspect --format '{{.Id}}' "$image" 2>/dev/null || echo "")
    if [ "$FORCE_UPDATE" -eq 0 ] && [ -n "$old_image_id" ] && [ "$old_image_id" = "$new_image_id" ]; then
        log_ok "已是最新镜像，无需更新"
        rm -rf "$BACKUP_DIR" 2>/dev/null
        exit 0
    fi

    log_info "发现新镜像，开始更新..."

    # 根据数据持久化方式，采取不同更新策略
    if [ "$has_volume" = "yes" ]; then
        # 有持久化卷：安全重建容器（数据在卷里，无损）
        log_step "检测到持久化卷，安全重建容器..."
        local compose_dir=""
        for d in "/opt/ntripcaster" "/opt/2rtk" "$(pwd)"; do
            if [ -f "$d/docker-compose.yml" ]; then
                compose_dir="$d"
                break
            fi
        done

        if [ -n "$compose_dir" ]; then
            cd "$compose_dir" || { log_error "无法进入 $compose_dir"; exit 1; }
            # 自动探测 compose 中的应用服务名（默认 ntrip-caster，兼容用户自定义命名）
            local compose_svc=""
            compose_svc=$(docker compose config --services 2>/dev/null | grep -iE 'caster|ntrip|2rtk' | head -1)
            [ -z "$compose_svc" ] && compose_svc="ntrip-caster"
            log_info "重建 compose 服务: $compose_svc"
            if ! docker compose up -d --no-deps "$compose_svc" 2>/dev/null; then
                # 服务名探测失败时退化为整体 up（仍会 pull 新镜像并重建）
                log_warn "指定服务重建失败，尝试整体重建..."
                docker compose up -d 2>/dev/null || {
                    log_error "compose 重建失败"
                    do_rollback
                    exit 1
                }
            fi
        else
            # docker run + volume 形态：无法安全自动重建，提示手动操作
            log_warn "未找到 compose 文件，无法自动重建容器"
            log_warn "已拉取新镜像，请手动重建（保留原 -v 挂载参数即可，数据在卷中无损）："
            log_warn "  docker stop ntrip-caster && docker rm ntrip-caster"
            log_warn "  然后用你原来的 docker run 命令（含 -v 挂载）重新启动，镜像已更新为:"
            log_warn "  $image"
            exit 0
        fi
    else
        # 无持久化卷：数据在容器内，重建会丢数据，提示用户
        log_error "检测到容器未挂载持久化卷（数据存储在容器内部）"
        echo ""
        log_warn "当前容器数据在容器内部，直接重建会【丢失所有数据】。"
        log_warn "已为你拉取新镜像到本地，但未做破坏性操作。"
        echo ""
        echo "  安全升级步骤（请手动执行）："
        echo "    1. 导出当前数据:"
        echo "       docker cp ntrip-caster:/app/data ./ntrip-data-backup"
        echo "       docker cp ntrip-caster:/app/config ./ntrip-config-backup"
        echo "    2. 用持久化卷重新部署（一次性，以后就能无损更新）:"
        echo "       mkdir -p ./ntrip-data/{logs,data,config}"
        echo "       docker run --rm $image cat /app/config.ini.example > ./ntrip-data/config/config.ini"
        echo "       docker stop ntrip-caster && docker rm ntrip-caster"
        echo "       docker run -d --name ntrip-caster -p 2101:2101 -p 5757:5757 \\"
        echo "         -v \$(pwd)/ntrip-data/logs:/app/logs \\"
        echo "         -v \$(pwd)/ntrip-data/data:/app/data \\"
        echo "         -v \$(pwd)/ntrip-data/config:/app/config \\"
        echo "         $image"
        echo "    3. 恢复数据（如需要）:"
        echo "       docker cp ./ntrip-data-backup/. ntrip-caster:/app/data/"
        exit 1
    fi

    # 健康检查
    if ! health_check; then
        log_error "容器重启后未正常运行"
        do_rollback
        exit 1
    fi
    log_ok "容器已正常运行"

    # 清理旧镜像
    if [ -n "$old_image_id" ] && [ "$old_image_id" != "$new_image_id" ]; then
        docker image prune -f >/dev/null 2>&1 || true
        log_ok "已清理未使用的旧镜像"
    fi
}

# ==================== 主流程 ====================
echo -e "${BLUE}=================================================${NC}"
echo -e "${BLUE}   NTRIP Caster 一键无损更新 v${SCRIPT_VERSION}           ${NC}"
echo -e "${BLUE}=================================================${NC}"
echo ""

log_info "部署形态: $DEPLOY_MODE"
log_info "当前时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# 备份
do_backup

# 执行更新
case "$DEPLOY_MODE" in
    source)
        update_source
        ;;
    docker)
        update_docker
        ;;
esac

echo ""
log_ok "================================================="
log_ok "  更新完成，数据无损！"
log_ok "  备份位置: $BACKUP_DIR"
log_ok "================================================="
echo ""

if [ "$DEPLOY_MODE" = "source" ]; then
    log_info "查看服务状态: systemctl status $SERVICE_NAME"
    log_info "查看最新日志: journalctl -u $SERVICE_NAME -f"
else
    log_info "查看容器状态: docker ps | grep ntrip-caster"
    log_info "查看容器日志: docker logs -f ntrip-caster"
fi
