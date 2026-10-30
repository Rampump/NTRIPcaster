#!/bin/bash
#
# NTRIP Caster 一键无损更新脚本 v2.3.2
#
# 功能：自动检测部署形态（源码+systemd / Docker），从可用源拉取最新版本，
#       升级前先检查并停止正在运行的旧版本（systemd 服务 + 游离进程 + 端口），
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
SCRIPT_VERSION="2.3.2"
INSTALL_DIR="/opt/2rtk"
CONFIG_DIR="/etc/2rtk"
LOG_DIR="/var/log/2rtk"

# install.sh 创建的服务名是 2rtk.service，ExecStart=/opt/2rtk/venv/bin/python3 /opt/2rtk/main.py，
# 停止命令：systemctl stop 2rtk
# 但这只是「默认值」。实际环境里服务名可能不同：
#   - 早期版本 install.sh / 用户自行改过单元名（ntripcaster、ntrip-caster…）；
#   - 一台机器上装过多份（旧目录 + 新目录），要停的是「正在用的那个」；
#   - 非 systemd 系统（sysvinit / openrc）根本没有 .service 单元，只有 /etc/init.d 脚本。
# 所以下面不依赖它，而是运行时动态解析出「本机实际在用的服务」，写回 SERVICE_NAME。
SERVICE_NAME="2rtk"
# 历史/别名的常见单元名，用于安装目录被自定义时的兜底发现
SERVICE_ALIASES="2rtk ntripcaster ntrip-caster strcaster ntrip_caster"
# 实际使用的单元文件绝对路径（resolve_service 填充；找不到时退回默认 2rtk.service）
SERVICE_UNIT_PATH=""
# 初始化系统：systemd / sysvinit / none。install.sh 只装 systemd，
# 但迁移过来的机器可能只有 init.d 脚本，停止/启动方式必须跟着变。
INIT_SYSTEM="none"

# Docker 主容器名（discover_container 动态探测后覆盖）及其候选/排除名单。
# 不写死的理由同上：docker run 自定义 --name、老版本容器名、compose 项目名变化都会不同。
CONTAINER_NAME="ntrip-caster"
CONTAINER_CANDIDATES="ntrip-caster 2rtk ntripcaster ntrip_caster strcaster"
# compose 里的辅助容器，探测主容器时必须排除（否则会去重启 adminer/nginx）
CONTAINER_EXCLUDE="adminer dozzle cadvisor nginx grafana prometheus redis promtail loki postgres mysql"

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

# 读取容器的某个字段（取不到输出空，不会因 set -e/pipefail 中断）
ci() {
    docker inspect --format "$2" "$1" 2>/dev/null || true
}

# 探测本程序的主容器名（输出名字；找不到输出空）。
# 顺序：
#   1) 候选名单精确匹配（ntrip-caster / 2rtk / ntripcaster …）；
#   2) 兜底：容器镜像名里含 ntripcaster 或 2rtk，且容器名不在辅助容器排除名单里。
# 不写死容器名的原因：用户 docker run --name 自定义、老版本老名字、compose 项目改名都会不同，
# 写死的结果就是「明明容器在跑，脚本却说找不到」。
discover_container() {
    local list="" name="" img=""
    command -v docker >/dev/null 2>&1 || return 0

    list=$(docker ps -a --format '{{.Names}}' 2>/dev/null || true)
    [ -z "$list" ] && return 0

    for name in $CONTAINER_CANDIDATES; do
        case $'\n'"$list"$'\n' in
            *$'\n'"$name"$'\n'*) echo "$name"; return 0 ;;
        esac
    done

    while IFS= read -r name; do
        [ -z "$name" ] && continue
        case " $CONTAINER_EXCLUDE " in
            *" $name "*) continue ;;
        esac
        img=$(ci "$name" '{{.Config.Image}}')
        case "$img" in
            *ntripcaster*|*2rtk*) echo "$name"; return 0 ;;
        esac
    done <<< "$list"

    return 0
}

# ==================== 部署形态检测 ====================
# systemd 是否真正可用（容器/精简系统里可能有 systemctl 命令但连不上 systemd）
SYSTEMD_OK=0
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-system-running >/dev/null 2>&1 || systemctl list-units --type=service >/dev/null 2>&1; then
        SYSTEMD_OK=1
    fi
fi

# 判断某个单元文件是否属于本程序
#   1) 单元内容里出现本安装目录（最可靠：ExecStart/WorkingDirectory 都含 $INSTALL_DIR）
#   2) 安装目录被用户自定义时，退回按单元名匹配，但仍要求单元在启动 main.py
_unit_matches() {
    local f="$1" name="$2"
    [ -f "$f" ] || return 1
    if grep -qF "$INSTALL_DIR" "$f" 2>/dev/null; then
        return 0
    fi
    case " $SERVICE_ALIASES " in
        *" $name "*) grep -qF "main.py" "$f" 2>/dev/null && return 0 ;;
    esac
    return 1
}

# 输出所有与本程序相关的 systemd 服务名（每行一个，已去重）。
# 为什么不能写死 2rtk：
#   - 早期版本 / 用户手动改过单元名，服务可能叫别的；
#   - 一台机器上可能装过多份，全部都要停，漏一个端口照样被占；
#   - 只按端口判断更不可靠：端口被谁占、是不是本程序，都看不出来。
# 所以以「单元内容指向本安装目录」为准反查。
discover_services() {
    local d f name seen=" "
    for d in /etc/systemd/system /run/systemd/system /lib/systemd/system /usr/lib/systemd/system; do
        [ -d "$d" ] || continue
        for f in "$d"/*.service; do
            [ -f "$f" ] || continue
            name=$(basename "$f" .service)
            case "$seen" in
                *" $name "*) continue ;;   # /lib 与 /usr/lib 常为同一目录，去重
            esac
            if _unit_matches "$f" "$name"; then
                seen="$seen$name "
                echo "$name"
            fi
        done
    done
}

# 兼容旧调用：返回第一个匹配到的单元文件路径（找不到返回非 0）
# 注意：不能用 `discover_services | head -1`。本脚本开着 pipefail，
# head 取到第一行就退出，会让上游 SIGPIPE（退出码 141），管道整体判失败。
find_unit_file() {
    local d all name
    all=$(discover_services)
    [ -z "$all" ] && return 1
    name="${all%%$'\n'*}"
    for d in /etc/systemd/system /run/systemd/system /lib/systemd/system /usr/lib/systemd/system; do
        if [ -f "$d/${name}.service" ]; then
            echo "$d/${name}.service"
            return 0
        fi
    done
    return 1
}

# 按服务名查单元文件的绝对路径（找不到返回非 0）
unit_path_of() {
    local name="$1" d
    for d in /etc/systemd/system /run/systemd/system /lib/systemd/system /usr/lib/systemd/system; do
        if [ -f "$d/${name}.service" ]; then
            echo "$d/${name}.service"
            return 0
        fi
    done
    return 1
}

# 探测本机初始化系统。决定「用什么命令停/起服务」。
detect_init_system() {
    if [ "$SYSTEMD_OK" -eq 1 ]; then
        echo "systemd"
    elif [ -x "/etc/init.d/${SERVICE_NAME}" ] || { [ -d /etc/init.d ] && ls /etc/init.d/*2rtk* >/dev/null 2>&1; }; then
        echo "sysvinit"
    else
        echo "none"
    fi
}

# 解析「本机实际在用的服务」，结果写入 SERVICE_NAME / SERVICE_UNIT_PATH / INIT_SYSTEM。
#
# 选择顺序：
#   1) 正在运行的那个单元（装过多份时只动在用的实例，避免误停别的目录）；
#   2) 否则取第一个指向本安装目录的单元；
#   3) 都没有 → 保持默认 2rtk（说明还没装 systemd 服务，update 会按 install.sh 同款重建）。
resolve_service() {
    local all="" name="" running="" first="" path=""

    all=$(discover_services)
    if [ -n "$all" ]; then
        while IFS= read -r name; do
            [ -z "$name" ] && continue
            [ -z "$first" ] && first="$name"
            if unit_is_active "$name"; then
                running="$name"
                break
            fi
        done <<< "$all"

        if [ -n "$running" ]; then
            SERVICE_NAME="$running"
        elif [ -n "$first" ]; then
            SERVICE_NAME="$first"
        fi
    fi

    if [ "$SYSTEMD_OK" -eq 1 ] && path=$(unit_path_of "$SERVICE_NAME"); then
        SERVICE_UNIT_PATH="$path"
    else
        SERVICE_UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
    fi

    INIT_SYSTEM=$(detect_init_system)
}

# 服务当前是否处于 running/active 状态
unit_is_active() {
    [ "$SYSTEMD_OK" -eq 1 ] || return 1
    systemctl is-active --quiet "$1" 2>/dev/null
}

# 服务主进程 PID（未运行则输出空；MainPID=0 表示 systemd 没有主进程，等同未知）
unit_pid() {
    [ "$SYSTEMD_OK" -eq 1 ] || { echo ""; return 0; }
    local p=""
    p=$(systemctl show -p MainPID "$1" 2>/dev/null | sed -n 's/^MainPID=//p' | tr -d '[:space:]')
    [ "$p" = "0" ] && p=""
    echo "$p"
}

# 服务最近一次进入 active 的时间（未运行或取不到则输出空）
unit_uptime() {
    [ "$SYSTEMD_OK" -eq 1 ] || { echo ""; return 0; }
    systemctl show -p ActiveEnterTimestamp "$1" 2>/dev/null \
        | sed -n 's/^ActiveEnterTimestamp=//p' | sed 's/[[:space:]]*$//'
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

    # 2. Docker 形态：存在本程序主容器（docker run 或 docker compose 均会创建）
    if command -v docker >/dev/null 2>&1; then
        if has_container "$CONTAINER_NAME"; then
            echo "docker"
            return
        fi
    fi

    # 3. 都无法确认
    echo "unknown"
}

# 先探测 docker 主容器名，再判定部署形态（顺序不能反，否则自定义容器名会被漏掉）
if command -v docker >/dev/null 2>&1; then
    _found_container=$(discover_container)
    if [ -n "$_found_container" ]; then
        CONTAINER_NAME="$_found_container"
    fi
fi

DEPLOY_MODE=$(detect_deploy_mode)

if [ "$DEPLOY_MODE" = "unknown" ]; then
    log_error "无法识别部署形态"
    echo ""
    echo "  检测依据："
    echo "    - 源码形态：$INSTALL_DIR/main.py 存在 + 指向该目录的 systemd 单元（/etc/init.d 脚本亦可）"
    echo "    - Docker 形态：存在本程序容器（候选名：$CONTAINER_CANDIDATES）"
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
    elif [ -x "/etc/init.d/${SERVICE_NAME}" ]; then
        echo "    [√] 检测到 SysV 初始化脚本：/etc/init.d/${SERVICE_NAME}"
    else
        echo "    [×] 未找到 ${SERVICE_NAME}.service 服务文件（未用 install.sh 安装 systemd 服务）"
    fi
    if command -v docker >/dev/null 2>&1 && has_container "$CONTAINER_NAME"; then
        echo "    [√] Docker 容器 $CONTAINER_NAME 存在"
    else
        echo "    [×] 未检测到 Docker 容器 $CONTAINER_NAME"
    fi
    echo ""
    log_warn "请确认你已通过 install.sh 或 docker-deploy.sh 正确部署后再更新"
    log_warn "如确已用 install.sh 安装，请检查：1) 是否以 root 执行；2) 服务名是否为 $SERVICE_NAME；3) 安装目录是否为 $INSTALL_DIR"
    exit 1
fi

log_info "检测到部署形态: $DEPLOY_MODE"

# 源码形态：解析「本机实际在用的服务单元」，后续停止/启动/诊断/服务文件同步全部以它为准。
# 只做判断不写文件，所以无更新时提前退出也不会影响运行中的服务。
if [ "$DEPLOY_MODE" = "source" ]; then
    resolve_service
    log_info "初始化系统: $INIT_SYSTEM"
    log_info "实际服务单元: ${SERVICE_NAME}(${SERVICE_UNIT_PATH})"
else
    log_info "主容器: $CONTAINER_NAME"
fi

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
        ci "$CONTAINER_NAME" '{{.Image}}' > "$BACKUP_DIR/old_image_id.txt" 2>/dev/null || true
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
    elif [ "$DEPLOY_MODE" = "docker" ]; then
        # Docker 形态：用旧镜像重建容器（compose 归属信息由 update_docker 记录）
        docker_rollback || log_warn "容器未能自动回滚，请用备份目录中的 recreate_container.sh 手动重建"
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

# 统计某端口的 TCP 连接状态数量
#   用法: count_tcp_state <端口> <listen|time-wait|established>
# 作用：区分「真的有进程在监听」和「只剩 TIME_WAIT 残留」。
#   TIME_WAIT 是上一轮连接正常关闭后的收尾状态，不响应新连接、也不持有监听，
#   带 SO_REUSEADDR 的 bind 完全不受影响；只有不带该选项的旧代码才会误判成「端口被占用」。
# 优先 ss，没有 ss 就直接读 /proc/net/tcp（不依赖 iproute2）。
count_tcp_state() {
    local port="$1" state="$2" n=""

    if command -v ss >/dev/null 2>&1; then
        n=$(ss -tanH "state $state" "( sport = :$port )" 2>/dev/null | wc -l)
        n=$(echo "$n" | tr -d '[:space:]')
        [ -n "$n" ] && { echo "$n"; return 0; }
    fi

    local code=""
    case "$state" in
        established) code="01" ;;
        time-wait)   code="06" ;;
        listen)      code="0A" ;;
        *)           echo "0"; return 0 ;;
    esac

    [ -r /proc/net/tcp ] || { echo "0"; return 0; }
    local hexport
    hexport=$(printf '%04X' "$port" 2>/dev/null) || { echo "0"; return 0; }
    awk -v c="$code" -v p="$hexport" '
        NR > 1 {
            n = split($2, a, ":")
            if (n == 2 && a[2] == p && $4 == c) cnt++
        }
        END { print cnt + 0 }
    ' /proc/net/tcp 2>/dev/null
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

# 本程序此刻是否「活着」——兼容 systemd / SysV / 无初始化系统三种情况。
service_alive() {
    if [ "$SYSTEMD_OK" -eq 1 ]; then
        unit_is_active "$SERVICE_NAME"
        return $?
    fi

    if [ -x "/etc/init.d/${SERVICE_NAME}" ] && command -v service >/dev/null 2>&1; then
        if service "$SERVICE_NAME" status >/dev/null 2>&1; then
            return 0
        fi
    fi

    # 兜底：端口在监听或进程存在，就认为是活的
    if port_in_use "$(get_web_port)" || port_in_use "$(get_ntrip_port)"; then
        return 0
    fi
    [ -n "$(_find_stray_pids)" ] && return 0
    return 1
}

# 两阶段探活：
#   1) 进程/服务层面：systemd active、容器 running、SysV 存活
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
            if has_container "$CONTAINER_NAME" running; then
                return 0
            fi
            sleep 2
            continue
        fi

        # 源码形态：服务必须活着
        if service_alive; then
            # 有 curl 时优先做 HTTP 探活（最严格）
            if command -v curl >/dev/null 2>&1; then
                if curl -fsS --connect-timeout 2 "http://127.0.0.1:${port}/" >/dev/null 2>&1; then
                    return 0
                fi
            fi
            # HTTP 未响应：累计"持续活着"次数，连续 5 次（约 10 秒）稳定运行也判定为启动成功。
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

# 等待端口的 TIME_WAIT 残留消退（无监听进程时才需要）。
# 用途：给「旧代码」兜底。2.3.1 之前的 main.py 端口检查没设 SO_REUSEADDR，
# 会把 TIME_WAIT 误判成「端口被占用」然后 sys.exit(1)；新版本已修复，正常不会走到这里。
# 触发条件严格限定为「端口无监听者 + 存在 TIME_WAIT」，因此不会掩盖真正起不来的问题。
wait_time_wait_drain() {
    local left="${1:-70}" p tw busy
    while [ "${left:-0}" -gt 0 ]; do
        busy=0
        for p in $(get_ntrip_port) $(get_web_port); do
            [ -z "$p" ] && continue
            tw=$(count_tcp_state "$p" time-wait)
            [ "${tw:-0}" -gt 0 ] 2>/dev/null && busy=1
        done
        [ "$busy" -eq 0 ] && return 0
        sleep 2
        left=$((left - 2))
    done
    return 1
}

# ==================== 旧版本检查 / 停止 ====================
# 找出所有「命令行指向本程序入口、但不受 systemd 托管」的残留进程 PID。
# 典型来源：
#   - 曾手动 `python main.py` / `bash start.sh` 启动；
#   - 从 Web 界面点「重启程序」后脱离 systemd 重新拉起；
#   - 旧版 install.sh 没配 systemd 时用 nohup 起的后台进程。
# 这类进程 systemctl stop 管不到，会一直占着 2101/5757。
_find_stray_pids() {
    local pid args names cg skip n

    # 本程序可能对应多个单元名（多份安装 / 改过名），先取全量列表
    names=$(discover_services)

    while read -r pid args; do
        [ -z "$pid" ] && continue
        case "$args" in
            *"$INSTALL_DIR/main.py"*) ;;
            *) continue ;;
        esac

        # 排除 systemd 托管的进程：它们由 systemctl stop 负责，不该在这里
        # 被当成「游离进程」重复处理。否则日志会自相矛盾——
        # 先说「发现游离进程 PID 28658」，紧接着说「停止服务，主进程 PID 28658」，
        # 让用户以为还要手动 kill 一遍。
        # 依据 /proc/PID/cgroup：systemd 托管的进程形如 /system.slice/2rtk.service。
        skip=0
        if [ -r "/proc/$pid/cgroup" ]; then
            cg=$(cat "/proc/$pid/cgroup" 2>/dev/null)
            for n in $names; do
                case "$cg" in
                    *"/${n}.service"*) skip=1; break ;;
                esac
            done
        fi
        [ "$skip" -eq 1 ] && continue

        echo "$pid"
    done < <(ps -eo pid=,args= 2>/dev/null)
}

# 停止单个 systemd 服务，并等到它真正退出（超时 SIGKILL 兜底）。
# 最后 reset-failed：服务若曾因端口被占反复重启，会进入 failed 且 restart
# counter 打满，此时 systemctl start 会直接报 "start request repeated too quickly"，
# 必须先清掉失败计数，否则后面启动必然失败（这也是「重启后未正常运行」的常见诱因）。
stop_unit() {
    local name="$1" waited=0

    systemctl stop "$name" >/dev/null 2>&1

    while systemctl is-active --quiet "$name" 2>/dev/null; do
        if [ "$waited" -ge 25 ]; then
            log_warn "服务 ${name} 25 秒未退出，发送 SIGKILL 强制结束"
            systemctl kill -s SIGKILL "$name" >/dev/null 2>&1
            sleep 2
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done

    systemctl reset-failed "$name" >/dev/null 2>&1 || true

    if systemctl is-active --quiet "$name" 2>/dev/null; then
        return 1
    fi
    return 0
}

# 只检查、不改动：把「旧版本此刻是否在运行」明确打印出来。
# 放在主流程最前面执行，让升级前先亮明现状（服务名 / 状态 / PID / 端口占用）。
report_running_state() {
    local svc="" name="" pid="" stray="" busy="" since=""

    log_step "检查本机旧版本运行状态..."

    # Docker 形态
    if [ "$DEPLOY_MODE" = "docker" ]; then
        if has_container "$CONTAINER_NAME" running; then
            log_warn "Docker 容器 $CONTAINER_NAME 正在运行（拉取到新镜像后会先停旧容器、删除后重建）"
            docker ps --filter "name=^${CONTAINER_NAME}$" --format '      {{.Names}}  {{.Status}}  {{.Ports}}' 2>/dev/null
        else
            log_info "Docker 容器 $CONTAINER_NAME 当前未运行（升级后会重新创建）"
        fi
        return 0
    fi

    # ---------- 1) systemd 服务（最关键：这才是本程序的标准运行方式） ----------
    svc=$(discover_services)
    if [ -n "$svc" ]; then
        for name in $svc; do
            pid=$(unit_pid "$name")
            if unit_is_active "$name"; then
                log_warn "systemd 服务 ${name}.service 正在运行（主进程 PID ${pid:-未知}）"
                since=$(unit_uptime "$name")
                [ -n "$since" ] && log_info "  启动时间: $since"
                log_info "  升级前将执行: systemctl stop ${name}"
            elif [ "$SYSTEMD_OK" -eq 1 ] && systemctl is-failed --quiet "$name" 2>/dev/null; then
                # 上次升级失败后残留的状态：failed + restart counter 打满。
                # 这种状态下直接 systemctl start 会报 "start request repeated too quickly"，
                # 必须先 reset-failed，所以这里明确提示，停止阶段会自动清理。
                log_warn "systemd 服务 ${name}.service 处于 failed 状态（可能在反复重启）"
                log_info "  升级前将执行: systemctl stop + reset-failed ${name}"
            else
                log_info "systemd 服务 ${name}.service 已安装，当前未运行"
            fi
        done
    elif [ "$SYSTEMD_OK" -eq 1 ]; then
        log_warn "未发现指向 $INSTALL_DIR 的 systemd 服务单元"
        log_info "  若服务名非标准，可用 systemctl list-units --type=service 确认"
    else
        log_warn "systemd 不可用，无法检查服务状态（仅做进程/端口检查）"
    fi

    # ---------- 2) 未受 systemd 托管的残留进程 ----------
    stray=$(_find_stray_pids | tr '\n' ' ' | tr -s ' ' | sed 's/^ //; s/ $//')
    if [ -n "$stray" ]; then
        log_warn "发现未受 systemd 托管的残留进程（PID: $stray），升级前将一并结束"
    fi

    # ---------- 3) 端口占用 ----------
    busy=$(busy_ports)
    if [ -n "$busy" ]; then
        log_info "当前端口占用:$busy"
    else
        log_info "端口 $(get_ntrip_port) / $(get_web_port) 当前空闲"
    fi
}

# 彻底停止 NTRIP Caster，并确认 2101 / 5757 真正释放。
# 返回 0 = 已确认没有实例在运行且端口空闲（可以安全升级）；1 = 停不掉（应中止升级）。
#
# 为什么必须这么麻烦：
#   只想「停一下服务」是不够的，实际环境里端口仍被占用的情况很常见：
#     - 服务名不是默认的 2rtk，systemctl stop 2rtk 停了个寂寞；
#     - 曾手动 `python main.py` / `bash start.sh` 起过，进程不在 systemd 托管下；
#     - 从 Web 界面点「重启程序」时进程脱离 systemd 重新拉起；
#     - 旧进程优雅关闭较慢（要 join 转发器/线程池/推送线程），socket 尚未释放。
#   只要端口没释放，新进程启动时 check_environment 就会报「Web端口 5757 已被占用」
#   并 status=1 退出，systemd 再按 Restart=always 无限重启，
#   表现就是「服务重启后未正常运行」。
stop_service_fully() {
    # 显式初始化：本脚本开着 `set -u`，而 `local x` 不赋值时变量仍处于 unset 状态，
    # 读取会报 "unbound variable"（bash 5.3 实测如此，不能想当然认为 local 就等于空串）。
    local svc="" name="" pid="" stray="" still="" p="" busy="" left="" remaining=""

    log_step "停止旧版本（升级期间必须完全静默）..."

    # ---------- 1) 停 systemd 服务（动态发现，可能不止一个） ----------
    svc=$(discover_services)
    if [ -n "$svc" ]; then
        for name in $svc; do
            if unit_is_active "$name"; then
                pid=$(unit_pid "$name")
                log_info "正在停止 systemd 服务 ${name}.service（主进程 PID ${pid:-未知}）..."
                if stop_unit "$name"; then
                    log_ok "服务 ${name}.service 已停止（systemctl stop ${name}）"
                else
                    log_error "服务 ${name}.service 停止失败"
                    log_warn "请手动执行后重试: systemctl stop ${name}"
                    return 1
                fi
                # 若旧单元名不是标准名，顺手 disable：否则下次开机它会和
                # 本次重建的 ${SERVICE_NAME}.service 同时启动，两个实例抢 2101/5757。
                if [ "$name" != "$SERVICE_NAME" ]; then
                    systemctl disable "$name" >/dev/null 2>&1 || true
                    log_info "已禁用旧别名服务 ${name}.service（统一用 ${SERVICE_NAME}.service 托管）"
                fi
            else
                # 未在运行也要 stop 一次：清掉 activating / deactivating 等中间态
                systemctl stop "$name" >/dev/null 2>&1 || true
                systemctl reset-failed "$name" >/dev/null 2>&1 || true
                log_info "服务 ${name}.service 未在运行，已清理状态"
            fi
        done
    elif [ "$SYSTEMD_OK" -eq 1 ]; then
        log_warn "未发现指向 $INSTALL_DIR 的 systemd 服务，跳过服务停止"
    else
        # 非 systemd 系统：有 init.d 脚本就用它，没有就靠下面的「残留进程清理」兜底
        if [ -x "/etc/init.d/${SERVICE_NAME}" ]; then
            log_info "正在停止 SysV 服务 /etc/init.d/${SERVICE_NAME}..."
            if command -v service >/dev/null 2>&1; then
                service "$SERVICE_NAME" stop >/dev/null 2>&1 || true
            else
                "/etc/init.d/${SERVICE_NAME}" stop >/dev/null 2>&1 || true
            fi
            sleep 2
        else
            log_info "本机既无 systemd 也无 init.d 脚本，靠清理进程 + 端口确认来保证静默"
        fi
    fi

    # ---------- 2) 清掉未受 systemd 托管的残留进程 ----------
    stray=$(_find_stray_pids | tr '\n' ' ' | tr -s ' ' | sed 's/^ //; s/ $//')
    if [ -n "$stray" ]; then
        log_warn "发现未受 systemd 托管的残留进程（PID: $stray），正在结束..."
        # shellcheck disable=SC2086
        kill -TERM $stray 2>/dev/null || true
        sleep 2
        for pid in $stray; do
            if kill -0 "$pid" 2>/dev/null; then
                still="$still $pid"
            fi
        done
        if [ -n "$still" ]; then
            log_warn "残留进程未响应 SIGTERM，强制结束:$still"
            # shellcheck disable=SC2086
            kill -9 $still 2>/dev/null || true
            sleep 1
        fi
        log_ok "残留进程已清理"
    fi

    # ---------- 3) 等端口真正释放（最多 15 秒） ----------
    left=15
    while [ "$left" -gt 0 ]; do
        busy=0
        for p in $(get_ntrip_port) $(get_web_port); do
            port_in_use "$p" && busy=1
        done
        [ "$busy" -eq 0 ] && break
        sleep 1
        left=$((left - 1))
    done

    # ---------- 4) 最终确认 ----------
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
        echo "    ss -lntp | grep -E ':($(get_ntrip_port)|$(get_web_port))'"
        echo "    kill -9 <PID>"
        echo ""
        return 1
    fi

    log_ok "旧版本已完全停止，端口 $(get_ntrip_port) / $(get_web_port) 已释放"
    return 0
}

# 启动服务（前置条件：stop_service_fully 已确认端口空闲）
# reset-failed 再兜一次：若服务曾因端口冲突反复重启，restart counter 打满后
# systemctl start 会直接报 "start request repeated too quickly" 而拒绝启动。
start_service() {
    log_step "启动服务..."

    # systemd：最常见路径
    if [ "$SYSTEMD_OK" -eq 1 ]; then
        systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
        systemctl daemon-reload >/dev/null 2>&1 || true
        systemctl start "$SERVICE_NAME" >/dev/null 2>&1
        return 0
    fi

    # SysV / OpenRC：用 init.d 脚本或 service 命令
    if [ -x "/etc/init.d/${SERVICE_NAME}" ]; then
        if command -v service >/dev/null 2>&1; then
            service "$SERVICE_NAME" start >/dev/null 2>&1
        else
            "/etc/init.d/${SERVICE_NAME}" start >/dev/null 2>&1
        fi
        return 0
    fi

    # 两者都没有（Docker 容器内的精简系统、纯手工启动环境）：
    # 按 install.sh 同款的命令直接拉起，日志仍写到 $LOG_DIR，保证升级后服务确实在跑。
    log_warn "未检测到 systemd 或 init.d，按 install.sh 同款命令直接启动"
    if [ -x "$INSTALL_DIR/venv/bin/python3" ] && [ -f "$INSTALL_DIR/main.py" ]; then
        mkdir -p "$LOG_DIR" 2>/dev/null || true
        ( cd "$INSTALL_DIR" && setsid "$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/main.py" \
            >>"$LOG_DIR/main.log" 2>>"$LOG_DIR/errors.log" </dev/null & ) 2>/dev/null || true
        sleep 2
    else
        log_error "找不到 $INSTALL_DIR/venv/bin/python3 或 main.py，无法启动"
        return 1
    fi
    return 0
}

# 启动失败时输出诊断信息，直接暴露真实原因（而不是只说「未正常运行」）
print_diagnostics() {
    echo ""
    echo "  ---- 诊断信息 ----"

    if [ "$DEPLOY_MODE" = "docker" ]; then
        echo "  [docker ps -a（本程序容器）]"
        docker ps -a --filter "name=^${CONTAINER_NAME}$" --format '    {{.Names}}  {{.Status}}  {{.Ports}}' 2>/dev/null
        echo ""
        echo "  [docker logs 尾部]"
        docker logs --tail 20 "$CONTAINER_NAME" 2>&1 | sed 's/^/    /'
    elif [ "$SYSTEMD_OK" -eq 1 ]; then
        echo "  [systemctl status]"
        systemctl status "$SERVICE_NAME" --no-pager -l 2>&1 | head -14 | sed 's/^/    /'

        echo ""
        echo "  [journalctl 最近日志]"
        journalctl -u "$SERVICE_NAME" -n 15 --no-pager 2>/dev/null | sed 's/^/    /'
    else
        echo "  [无 systemd，跳过 systemctl/journalctl]"
        if [ -x "/etc/init.d/${SERVICE_NAME}" ]; then
            echo "  [init.d 状态]"
            service "$SERVICE_NAME" status 2>&1 | head -8 | sed 's/^/    /' || true
        fi
    fi

    if [ -f "$LOG_DIR/errors.log" ]; then
        echo ""
        echo "  [errors.log 尾部]"
        tail -12 "$LOG_DIR/errors.log" 2>/dev/null | sed 's/^/    /'
    fi

    local p holder ls tw
    for p in $(get_ntrip_port) $(get_web_port); do
        [ -z "$p" ] && continue
        ls=$(count_tcp_state "$p" listen)
        tw=$(count_tcp_state "$p" time-wait)
        echo ""
        if port_in_use "$p"; then
            holder=$(port_holder "$p")
            echo "  [端口 $p 被占用，进程 PID: ${holder:-未知}]  LISTEN=$ls  TIME_WAIT=$tw"
            print_port_detail "$p"
            if [ -n "$holder" ]; then
                ps -fp "$holder" 2>/dev/null | tail -1 | sed 's/^/      /'
            fi
        else
            echo "  [端口 $p 无监听进程]  LISTEN=$ls  TIME_WAIT=$tw"
            if [ "${tw:-0}" -gt 0 ] 2>/dev/null; then
                echo "      TIME_WAIT 是上一轮连接关闭后的收尾状态，不占监听端口，"
                echo "      带 SO_REUSEADDR 的程序（2.3.1 起 main.py 已修复）可直接启动。"
                echo "      若程序仍报「端口已被占用」，说明运行的是旧代码。"
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

    # 记录旧版本号（用于判定是否需要更新）。
    # 优先读安装目录的 VERSION 文件 —— 版本号全项目只维护这一个文件（根目录 VERSION），
    # config.ini 里的 [app] version 只是老部署的兜底，不再是权威来源。
    local old_version=""
    if [ -f "$INSTALL_DIR/VERSION" ]; then
        old_version=$(head -1 "$INSTALL_DIR/VERSION" 2>/dev/null | tr -d '[:space:]')
    fi
    if [ -z "$old_version" ] && [ -f "$CONFIG_DIR/config.ini" ]; then
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

    # 读新版本号：同样以 VERSION 文件为准，config.ini.example 兜底
    local new_version=""
    if [ -f "$tmp_clone/VERSION" ]; then
        new_version=$(head -1 "$tmp_clone/VERSION" 2>/dev/null | tr -d '[:space:]')
    fi
    if [ -z "$new_version" ]; then
        new_version=$(grep -E '^\s*version\s*=' "$tmp_clone/config.ini.example" 2>/dev/null | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]')
    fi
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

    # ===== 关键：先停止旧版本，再动任何文件 =====
    # 顺序不能反：旧进程还在跑，新进程启动时 check_environment 会发现 2101/5757
    # 仍被旧实例占用，直接 status=1 退出，systemd 再按 Restart=always 无限重启，
    # 表现就是「服务重启后未正常运行」，甚至回滚后仍然起不来。
    # 放在备份旧代码 / 同步代码之前：停不下来就当场中止，此时安装目录一字未改。
    if ! stop_service_fully; then
        log_error "旧版本仍在运行或端口未释放，已中止更新（未做任何修改）"
        log_warn "请先手动停止旧版本后重试："
        log_warn "  systemctl stop ${SERVICE_NAME}"
        log_warn "  然后确认端口空闲：ss -lntp | grep -E ':($(get_ntrip_port)|$(get_web_port))'"
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
    #
    # 关键：写到「本机实际在用的单元路径」$SERVICE_UNIT_PATH，而不是无脑写 2rtk.service。
    # 否则机器上会出现两个单元（旧的 ntripcaster.service + 新的 2rtk.service）都指向
    # 同一个 main.py，开机各起一个实例抢 2101/5757 —— 这正是「更新后起不来」的典型原因。
    log_step "同步 systemd 服务文件..."
    if [ "$SYSTEMD_OK" -eq 1 ]; then
        if [ -z "$SERVICE_UNIT_PATH" ]; then
            SERVICE_UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
        fi
        if [ -f "$INSTALL_DIR/2rtk.service" ]; then
            cp -a "$INSTALL_DIR/2rtk.service" "$SERVICE_UNIT_PATH" 2>/dev/null
            log_ok "已从仓库同步服务文件到 $SERVICE_UNIT_PATH"
        else
            cat > "$SERVICE_UNIT_PATH" << EOF
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
            log_ok "已按 install.sh 同款方式重建服务文件: $SERVICE_UNIT_PATH"
        fi
    else
        # 非 systemd：不生成 .service（生成也没人执行），保留原 init.d 脚本
        log_info "本机无 systemd，跳过 .service 同步（沿用 /etc/init.d/${SERVICE_NAME} 或原启动方式）"
    fi

    # 兜底：确保 TimeoutStopSec 存在。
    # 老版本 install.sh 生成的服务文件没有这一项，systemd 会退化为默认 90 秒：
    # 重启时旧进程迟迟不退，restart 长时间阻塞，2101/5757 端口也迟迟不释放。
    local _unit="$SERVICE_UNIT_PATH"
    if [ -n "$_unit" ] && [ -f "$_unit" ] && ! grep -q '^TimeoutStopSec=' "$_unit" 2>/dev/null; then
        if awk '
            /^\[Service\]/ && !done { print; print "TimeoutStopSec=20"; print "KillMode=mixed"; done=1; next }
            { print }
        ' "$_unit" > "${_unit}.new" 2>/dev/null && [ -s "${_unit}.new" ]; then
            cat "${_unit}.new" > "$_unit"
            log_info "已为服务文件补充 TimeoutStopSec=20 / KillMode=mixed"
        fi
        rm -f "${_unit}.new"
    fi
    if [ "$SYSTEMD_OK" -eq 1 ]; then
        # 老单元名（别名）如果在 /etc 里残留且仍 enabled，一并禁用，避免开机双启
        local _other="" _o=""
        for _o in $(discover_services); do
            [ "$_o" = "$SERVICE_NAME" ] && continue
            _other=$(systemctl is-enabled "$_o" 2>/dev/null || true)
            if [ "$_other" = "enabled" ]; then
                systemctl disable "$_o" >/dev/null 2>&1 || true
                log_info "已禁用重复的旧单元 ${_o}.service（统一由 ${SERVICE_NAME} 托管）"
            fi
        done
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi

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
        # 先分清「真的起不来」还是「旧代码被 TIME_WAIT 挡住」：
        # 端口没有监听者、但存在 TIME_WAIT 残留时，等它消退再试一次，
        # 避免一次本可成功的更新被误判并回滚。
        local _tw_pending=0 _p _tw
        for _p in $(get_ntrip_port) $(get_web_port); do
            [ -z "$_p" ] && continue
            if ! port_in_use "$_p"; then
                _tw=$(count_tcp_state "$_p" time-wait)
                [ "${_tw:-0}" -gt 0 ] 2>/dev/null && _tw_pending=1
            fi
        done

        if [ "$_tw_pending" -eq 1 ]; then
            log_warn "端口无监听进程，但存在 TIME_WAIT 残留（旧代码会误判为被占用）"
            log_info "等待残留连接消退后重试启动（最多 70 秒）..."
            wait_time_wait_drain 70 || true
            start_service
            if health_check; then
                log_ok "服务已正常运行（TIME_WAIT 消退后重试成功）"
                return 0
            fi
        fi

        log_error "服务重启后未正常运行"
        print_diagnostics
        do_rollback
        exit 1
    fi
    log_ok "服务已正常运行"
}

# ==================== Docker 更新 ====================
# 记录 compose 归属信息（update_docker 填充，回滚时复用）
DOCKER_COMPOSE_PROJECT=""
DOCKER_COMPOSE_WORKDIR=""
DOCKER_COMPOSE_FILES=""
DOCKER_COMPOSE_SVC=""

# 单引号安全转义：把值里的 ' 变成 '\''，用于生成脚本里的单引号字符串
_sq() {
    printf '%s' "$1" | sed "s/'/'\\\\''/g"
}

# 读取容器 label（没有该 label 时 docker inspect 会输出 <no value>，这里统一转成空）
ci_label() {
    local v=""
    v=$(docker inspect --format "{{index .Config.Labels \"$2\"}}" "$1" 2>/dev/null || true)
    case "$v" in
        "<no value>"|"<nil>") v="" ;;
    esac
    printf '%s' "$v"
}

# compose 命令：优先 v2 插件（docker compose），退回 v1（docker-compose）
dc() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose "$@"
    else
        return 127
    fi
}

# 生成「按原参数重建容器」脚本。
# 为什么需要：docker run 部署的容器没有任何编排文件，升级时只能靠 inspect 出来的参数原样重建；
# 把这些参数内联在 update.sh 里会因引号/空格/换行出错，生成独立脚本最稳，回滚也能直接复用。
write_recreate_script() {
    local name="$1" out="$2"
    local ports="" envs="" mounts="" restart="" network="" privileged=""
    local line="" p_left="" p_host="" p_spec="" mt="" mn="" ms="" md="" mr="" spec=""
    local -a run_args=()

    ports=$(ci "$name" '{{range $p, $conf := .HostConfig.PortBindings}}{{$p}}={{range $conf}}{{.HostIp}}:{{.HostPort}},{{end}};{{end}}')
    envs=$(ci "$name" '{{range .Config.Env}}{{println .}}{{end}}')
    mounts=$(ci "$name" '{{range .Mounts}}{{.Type}}|{{.Name}}|{{.Source}}|{{.Destination}}|{{.RW}}{{println}}{{end}}')
    restart=$(ci "$name" '{{.HostConfig.RestartPolicy.Name}}')
    network=$(ci "$name" '{{.HostConfig.NetworkMode}}')
    privileged=$(ci "$name" '{{.HostConfig.Privileged}}')

    # 端口映射：2101/tcp=0.0.0.0:2101,;5757/tcp=0.0.0.0:5757,;
    local IFS_OLD="$IFS"
    IFS=';'
    for line in $ports; do
        IFS="$IFS_OLD"
        [ -z "$line" ] && continue
        p_left="${line%%=*}"
        p_host="${line#*=}"
        p_host="${p_host%,}"
        [ -z "$p_host" ] && continue
        case "$p_host" in
            :*) p_spec="${p_host#:}:${p_left}" ;;
            *)  p_spec="${p_host}:${p_left}" ;;
        esac
        run_args+=("-p '$(_sq "$p_spec")'")
    done
    IFS="$IFS_OLD"

    # 环境变量（含 NTRIP_CONFIG_FILE 等，必须原样带回来，否则配置路径会丢）
    if [ -n "$envs" ]; then
        while IFS= read -r line; do
            [ -z "$line" ] && continue
            run_args+=("-e '$(_sq "$line")'")
        done <<< "$envs"
    fi

    # 卷 / 绑定挂载 / tmpfs
    if [ -n "$mounts" ]; then
        while IFS= read -r line; do
            [ -z "$line" ] && continue
            IFS='|' read -r mt mn ms md mr <<< "$line"
            case "$mt" in
                volume)
                    if [ -n "$mn" ]; then spec="${mn}:${md}"; else spec="${ms}:${md}"; fi
                    [ "$mr" = "false" ] && spec="${spec}:ro"
                    run_args+=("-v '$(_sq "$spec")'")
                    ;;
                bind)
                    spec="${ms}:${md}"
                    [ "$mr" = "false" ] && spec="${spec}:ro"
                    run_args+=("-v '$(_sq "$spec")'")
                    ;;
                tmpfs)
                    run_args+=("--tmpfs '$(_sq "$md")'")
                    ;;
            esac
        done <<< "$mounts"
    fi

    case "$restart" in
        ""|no) ;;
        *) run_args+=("--restart '$(_sq "$restart")'") ;;
    esac
    [ "$privileged" = "true" ] && run_args+=("--privileged")
    case "$network" in
        ""|default|bridge) ;;
        *) run_args+=("--network '$(_sq "$network")'") ;;
    esac

    {
        printf '#!/bin/bash\n'
        printf '# 由 update.sh %s 自动生成（%s）\n' "$SCRIPT_VERSION" "$(date '+%Y-%m-%d %H:%M:%S')"
        printf '# 作用：先停掉并删除容器 %s，再用原参数（端口/卷/环境变量/重启策略）重建，\n' "$name"
        printf '#       第一个参数为要使用的镜像名或镜像 ID（升级传新镜像，回滚传旧镜像）。\n'
        printf 'set -uo pipefail\n'
        printf 'IMAGE="${1:-}"\n'
        printf '[ -n "$IMAGE" ] || { echo "用法: bash $0 <镜像名或ID>"; exit 1; }\n'
        printf 'echo "[recreate] 停止并删除旧容器: %s"\n' "$name"
        printf 'docker stop %s >/dev/null 2>&1 || true\n' "$name"
        printf 'docker rm -f %s >/dev/null 2>&1 || true\n' "$name"
        printf 'echo "[recreate] 启动新容器，镜像: $IMAGE"\n'
        printf 'exec docker run -d --name %s' "$name"
        if [ "${#run_args[@]}" -gt 0 ]; then
            printf ' \\\n'
            local i=0
            while [ "$i" -lt "${#run_args[@]}" ]; do
                printf '    %s \\\n' "${run_args[$i]}"
                i=$((i + 1))
            done
            printf '    "$IMAGE"\n'
        else
            printf ' "$IMAGE"\n'
        fi
    } > "$out" 2>/dev/null || return 1

    chmod +x "$out" 2>/dev/null || true
    return 0
}

# 用 docker run 参数重建容器（先 stop + rm，避免「容器名已被占用」）
docker_run_recreate() {
    local image="$1" script="$BACKUP_DIR/recreate_container.sh"

    if [ ! -f "$script" ]; then
        log_error "缺少重建脚本 $script，无法自动重建容器"
        return 1
    fi

    log_info "停止并删除旧容器: $CONTAINER_NAME"
    docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

    log_info "按原参数重建容器（镜像: $image）"
    bash "$script" "$image"
}

# 取 compose 服务实际使用的镜像名。
# 为什么需要：docker-compose.yml 里写的是 `image: ntripcaster:2.3.1`（本地构建的 tag），
# 而 update.sh 从仓库拉的是 ccr…/ntripcaster:latest —— 两者不是同一个 tag。
# 不做映射的话，compose 重建时用的还是本地旧镜像，看起来「更新成功」但代码没变。
compose_image_of() {
    local svc="$1"
    shift
    local out="" line=""

    out=$(dc "$@" config --images 2>/dev/null | awk 'NF' 2>/dev/null || true)
    if [ -z "$out" ]; then
        return 1
    fi
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        printf '%s' "$line"
        return 0
    done <<< "$out"
    return 1
}

# 用 compose 重建容器：先 stop + rm，再 up。
# 必须先 rm 的原因（这也是「自动更新 docker 必先删容器」的根因）：
#   1) 容器名是固定 container_name（ntrip-caster），不删就 up 会直接报
#      "container name /ntrip-caster is already in use"；
#   2) 若原容器是 docker run 建的（没有 compose label），compose 视其为「外来容器」，
#      up 时同样因名字冲突失败，或干脆复用旧容器 —— 新镜像根本没生效；
#   3) 旧容器不退，2101/5757 仍被占，新容器起不来。
compose_recreate() {
    local project="$1" workdir="$2" files="$3" svc="$4" image="$5"
    local -a cargs=()
    local f="" IFS_OLD="$IFS"

    [ -n "$project" ] && cargs+=(-p "$project")
    if [ -n "$files" ]; then
        IFS=','
        for f in $files; do
            IFS="$IFS_OLD"
            [ -n "$f" ] && cargs+=(-f "$f")
        done
        IFS="$IFS_OLD"
    fi

    if [ -n "$workdir" ] && [ -d "$workdir" ]; then
        cd "$workdir" || log_warn "无法进入 compose 工作目录 $workdir"
    fi

    # compose 文件不存在时直接放弃（比如用户后来删了）
    if [ -z "$files" ] && [ ! -f ./docker-compose.yml ] && [ ! -f ./compose.yaml ] && [ ! -f ./docker-compose.yaml ]; then
        log_warn "未找到 compose 文件（工作目录: ${workdir:-$PWD}）"
        return 1
    fi

    if [ -n "$svc" ]; then
        dc ${cargs[@]+"${cargs[@]}"} stop "$svc" >/dev/null 2>&1 || true
    fi

    # 关键一步：先删容器
    if has_container "$CONTAINER_NAME"; then
        docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
        log_info "已删除旧容器 $CONTAINER_NAME（避免重建时容器名冲突）"
    fi

    # 把拉取到的新镜像「改名」成 compose 里写的镜像名。
    # compose 的 image: 通常是本地构建 tag（ntripcaster:2.3.1），和仓库 tag 不同；
    # 不映射的话 compose 会继续用本地旧镜像，日志显示更新成功但代码没变。
    if [ -n "$image" ]; then
        local compose_img=""
        compose_img=$(compose_image_of "$svc" ${cargs[@]+"${cargs[@]}"})
        if [ -n "$compose_img" ] && [ "$compose_img" != "$image" ]; then
            if docker tag "$image" "$compose_img" >/dev/null 2>&1; then
                log_info "已把新镜像标记为 compose 使用的镜像: $compose_img"
            else
                log_warn "镜像标记失败（$image -> $compose_img），compose 可能仍用旧镜像"
            fi
        elif [ -z "$compose_img" ]; then
            log_warn "无法从 compose 配置读出镜像名，若升级后版本没变，请检查 compose 里的 image: 标签"
        fi
    fi

    if [ -n "$svc" ]; then
        log_info "compose up 服务: $svc"
        dc ${cargs[@]+"${cargs[@]}"} up -d --no-deps --force-recreate "$svc" && return 0
    else
        log_info "compose up（未取到服务名，整体重建）"
        dc ${cargs[@]+"${cargs[@]}"} up -d --force-recreate && return 0
    fi

    log_warn "compose 重建失败（镜像 $image），尝试用原容器参数直接重建..."
    return 1
}

# 容器更新失败时的回滚：用旧镜像重建
docker_rollback() {
    local old_id="" old_name=""
    old_id=$(tr -d '[:space:]' < "$BACKUP_DIR/docker_old_image_id.txt" 2>/dev/null || true)
    old_name=$(tr -d '[:space:]' < "$BACKUP_DIR/docker_old_image_name.txt" 2>/dev/null || true)

    if [ -z "$old_id" ]; then
        log_warn "未记录旧镜像，无法自动回滚容器"
        return 1
    fi

    log_step "回滚容器到旧镜像..."

    if [ -n "$DOCKER_COMPOSE_PROJECT" ]; then
        # compose 形态：把旧镜像重新打回原 tag，再重建，保证后续 compose 操作仍指向旧版本
        if [ -n "$old_name" ]; then
            docker tag "$old_id" "$old_name" >/dev/null 2>&1 || true
        fi
        if compose_recreate "$DOCKER_COMPOSE_PROJECT" "$DOCKER_COMPOSE_WORKDIR" \
            "$DOCKER_COMPOSE_FILES" "$DOCKER_COMPOSE_SVC" "$old_name"; then
            log_ok "容器已回滚到旧镜像（compose）"
            return 0
        fi
        log_warn "compose 回滚失败，改用原容器参数重建..."
    fi

    if docker_run_recreate "$old_id" >/dev/null 2>&1; then
        log_ok "容器已回滚到旧镜像（docker run）"
        return 0
    fi

    log_error "容器回滚失败，请手动重建："
    echo "    bash $BACKUP_DIR/recreate_container.sh $old_id"
    return 1
}

update_docker() {
    log_step "更新 Docker 镜像..."

    if ! command -v docker >/dev/null 2>&1; then
        log_error "未找到 docker 命令"
        exit 1
    fi

    [ -z "$CONTAINER_NAME" ] && CONTAINER_NAME="ntrip-caster"

    if ! has_container "$CONTAINER_NAME"; then
        log_error "未找到本程序容器（候选名: $CONTAINER_CANDIDATES）"
        log_warn "请用 docker ps -a 确认实际容器名后重试"
        exit 1
    fi

    # ---------- 0) 记录现状（升级失败要能原样还原） ----------
    local old_image_id="" old_image_name=""
    old_image_id=$(ci "$CONTAINER_NAME" '{{.Image}}')
    old_image_name=$(ci "$CONTAINER_NAME" '{{.Config.Image}}')

    DOCKER_COMPOSE_PROJECT=$(ci_label "$CONTAINER_NAME" 'com.docker.compose.project')
    DOCKER_COMPOSE_WORKDIR=$(ci_label "$CONTAINER_NAME" 'com.docker.compose.project.working_dir')
    DOCKER_COMPOSE_FILES=$(ci_label "$CONTAINER_NAME" 'com.docker.compose.project.config_files')
    DOCKER_COMPOSE_SVC=$(ci_label "$CONTAINER_NAME" 'com.docker.compose.service')

    if [ -n "$DOCKER_COMPOSE_PROJECT" ]; then
        log_info "容器由 docker compose 管理（项目: $DOCKER_COMPOSE_PROJECT，服务: ${DOCKER_COMPOSE_SVC:-未知}）"
    else
        log_info "容器由 docker run 创建（无 compose 归属，将按 inspect 参数原样重建）"
    fi

    # 判断数据是否落在持久卷里（决定能否安全删除容器）
    # 本项目数据库/配置/日志都在容器内 /app/ 下，有任一 /app/ 子目录挂载即视为已持久化
    local has_volume="no" _mounts=""
    _mounts=$(ci "$CONTAINER_NAME" '{{range .Mounts}}{{.Destination}} {{end}}')
    case " $_mounts " in
        *" /app/"*) has_volume="yes" ;;
    esac

    mkdir -p "$BACKUP_DIR" 2>/dev/null || true
    write_recreate_script "$CONTAINER_NAME" "$BACKUP_DIR/recreate_container.sh" 2>/dev/null || true
    echo "$old_image_id" > "$BACKUP_DIR/docker_old_image_id.txt" 2>/dev/null || true
    echo "$old_image_name" > "$BACKUP_DIR/docker_old_image_name.txt" 2>/dev/null || true

    # ---------- 1) 拉取新镜像 ----------
    local image="$TCR_IMAGE"
    if [ -n "$REPO_SOURCE" ]; then
        case "$REPO_SOURCE" in
            dockerhub) image="$DOCKERHUB_IMAGE" ;;
            tcr)       image="$TCR_IMAGE" ;;
            *) log_error "Docker 形态 REPO_SOURCE 仅支持 tcr 或 dockerhub"; exit 1 ;;
        esac
    fi

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

    # ---------- 2) 判定是否有更新 ----------
    local new_image_id=""
    new_image_id=$(docker inspect --format '{{.Id}}' "$image" 2>/dev/null || echo "")
    if [ "$FORCE_UPDATE" -eq 0 ] && [ -n "$old_image_id" ] && [ "$old_image_id" = "$new_image_id" ]; then
        log_ok "已是最新镜像，无需更新"
        rm -rf "$BACKUP_DIR" 2>/dev/null
        exit 0
    fi
    log_info "发现新镜像，开始更新..."

    # ---------- 3) 数据安全性检查（放在任何破坏性操作之前） ----------
    if [ "$has_volume" != "yes" ]; then
        log_error "检测到容器未挂载持久化卷（数据存储在容器内部）"
        echo ""
        log_warn "当前容器数据在容器内部，删除容器就会【丢失所有数据】。"
        log_warn "已为你拉取新镜像到本地，但未做任何破坏性操作。"
        echo ""
        echo "  安全升级步骤（请手动执行）："
        echo "    1. 导出当前数据:"
        echo "       docker cp $CONTAINER_NAME:/app/data ./ntrip-data-backup"
        echo "       docker cp $CONTAINER_NAME:/app/config ./ntrip-config-backup"
        echo "    2. 用持久化卷重新部署（一次性，以后就能无损更新）:"
        echo "       mkdir -p ./ntrip-data/{logs,data,config}"
        echo "       docker run --rm $image cat /app/config.ini.example > ./ntrip-data/config/config.ini"
        echo "       docker stop $CONTAINER_NAME && docker rm $CONTAINER_NAME"
        echo "       docker run -d --name $CONTAINER_NAME -p 2101:2101 -p 5757:5757 \\"
        echo "         -v \$(pwd)/ntrip-data/logs:/app/logs \\"
        echo "         -v \$(pwd)/ntrip-data/data:/app/data \\"
        echo "         -v \$(pwd)/ntrip-data/config:/app/config \\"
        echo "         $image"
        echo "    3. 恢复数据（如需要）:"
        echo "       docker cp ./ntrip-data-backup/. $CONTAINER_NAME:/app/data/"
        echo ""
        log_info "已生成重建脚本备用: $BACKUP_DIR/recreate_container.sh"
        exit 1
    fi

    # ---------- 4) 停 / 删 / 建 ----------
    # 顺序不能省：必须先停并删除旧容器，再创建新容器。
    # 直接 `docker compose up -d` 或 `docker run` 同名容器都会因
    # "container name is already in use" 失败；即使复用成功，旧容器不退也让端口一直被占。
    log_step "停止并重建容器..."
    local recreate_ok=1

    if [ -n "$DOCKER_COMPOSE_PROJECT" ]; then
        if compose_recreate "$DOCKER_COMPOSE_PROJECT" "$DOCKER_COMPOSE_WORKDIR" \
            "$DOCKER_COMPOSE_FILES" "$DOCKER_COMPOSE_SVC" "$image"; then
            recreate_ok=0
        else
            log_warn "compose 重建失败，回退为按原参数重建..."
            docker_run_recreate "$image" && recreate_ok=0
        fi
    else
        docker_run_recreate "$image" && recreate_ok=0
    fi

    if [ "$recreate_ok" -ne 0 ]; then
        log_error "容器重建失败"
        docker_rollback
        print_diagnostics
        exit 1
    fi

    # ---------- 5) 健康检查 ----------
    if ! health_check; then
        log_error "容器重启后未正常运行"
        print_diagnostics
        docker_rollback
        exit 1
    fi
    log_ok "容器已正常运行"

    # ---------- 6) 清理旧镜像 ----------
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

# 升级前第一步：检查旧版本是否正在运行（只检查、不改动，先把现状亮出来）。
# 真正停止动作放在确定「确实有新版本要装」之后，避免无更新时白白中断服务。
report_running_state
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
    if [ "$SYSTEMD_OK" -eq 1 ]; then
        log_info "查看服务状态: systemctl status $SERVICE_NAME"
        log_info "查看最新日志: journalctl -u $SERVICE_NAME -f"
    else
        log_info "查看进程: ps -ef | grep '$INSTALL_DIR/main.py' | grep -v grep"
        log_info "查看最新日志: tail -f $LOG_DIR/main.log"
    fi
else
    log_info "查看容器状态: docker ps | grep $CONTAINER_NAME"
    log_info "查看容器日志: docker logs -f $CONTAINER_NAME"
fi
