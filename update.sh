#!/bin/bash
#
# NTRIP Caster 一键无损更新脚本 v1.0.0
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

# ==================== 参数解析 ====================
for arg in "$@"; do
    case "$arg" in
        --force|-f)
            FORCE_UPDATE=1
            ;;
        --help|-h)
            echo "用法: sudo bash update.sh [--force]"
            echo "  --force  跳过版本判定，强制执行更新"
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

# ==================== 部署形态检测 ====================
detect_deploy_mode() {
    # 1. 源码 + systemd 形态：/opt/2rtk 存在且是 git 仓库，且 systemd 服务存在
    if [ -d "$INSTALL_DIR/.git" ] && (systemctl list-unit-files 2>/dev/null | grep -q "${SERVICE_NAME}.service"); then
        echo "source"
        return
    fi

    # 2. Docker 形态：存在 ntrip-caster 容器，或 docker-compose 项目在运行
    if command -v docker >/dev/null 2>&1; then
        if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q 'ntrip-caster'; then
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
    echo "    - 源码形态：$INSTALL_DIR/.git 存在 + ${SERVICE_NAME}.service 服务存在"
    echo "    - Docker 形态：存在名为 ntrip-caster 的容器"
    echo ""
    log_warn "请确认你已通过 install.sh 或 docker-deploy.sh 正确部署后再更新"
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
        # 源码形态：恢复数据库和配置
        if [ -f "$BACKUP_DIR/2rtk.db" ]; then
            cp -a "$BACKUP_DIR/2rtk.db" "${INSTALL_DIR}/data/2rtk.db" 2>/dev/null
            log_ok "已回滚数据库"
        fi
        if [ -f "$BACKUP_DIR/config.ini" ]; then
            cp -a "$BACKUP_DIR/config.ini" "$CONFIG_DIR/config.ini" 2>/dev/null
            log_ok "已回滚配置文件"
        fi
    fi
    log_warn "代码/镜像可能已更新，请检查服务状态。备份保留在 $BACKUP_DIR"
}

# ==================== 健康检查 ====================
health_check() {
    local tries=0
    local max_tries=12
    while [ $tries -lt $max_tries ]; do
        if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            return 0
        fi
        # Docker 形态检查容器
        if [ "$DEPLOY_MODE" = "docker" ] && docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'ntrip-caster'; then
            return 0
        fi
        tries=$((tries + 1))
        sleep 2
    done
    return 1
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

    # 备份旧代码（用于失败回滚）
    log_step "备份旧代码..."
    cp -a "$INSTALL_DIR" "$BACKUP_DIR/opt_2rtk_bak" 2>/dev/null
    log_ok "旧代码已备份"

    # 同步新代码到安装目录（排除运行时目录：venv、data、config.ini 软链、日志）
    log_step "同步新代码..."
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
        # 无 rsync 时用 cp 兜底（先删旧代码，但保留 venv/data/config.ini）
        log_warn "未找到 rsync，使用 cp 兜底"
        find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 \
            ! -name 'venv' ! -name 'data' ! -name 'config.ini' \
            -exec rm -rf {} + 2>/dev/null
        cp -r "$tmp_clone"/* "$INSTALL_DIR/" 2>/dev/null
    fi
    rm -rf "$tmp_clone"
    log_ok "代码同步完成"

    # 恢复 config.ini 软链（rsync --exclude config.ini 会保留原软链，此处兜底重建）
    if [ ! -L "$INSTALL_DIR/config.ini" ] && [ -f "$CONFIG_DIR/config.ini" ]; then
        ln -sf "$CONFIG_DIR/config.ini" "$INSTALL_DIR/config.ini" 2>/dev/null
    fi

    # 重新安装依赖（requirements.txt 可能变化，用清华镜像加速 + 回退）
    log_step "更新 Python 依赖..."
    local PIP_ARGS=""
    if curl -fsS --connect-timeout 2 https://mirrors.tuna.tsinghua.edu.cn >/dev/null 2>&1; then
        PIP_ARGS="-i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn"
        log_info "使用清华镜像加速"
    fi
    if ! "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" $PIP_ARGS 2>/dev/null; then
        if [ -n "$PIP_ARGS" ]; then
            log_warn "清华镜像安装失败，回退官方源..."
            "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" 2>/dev/null || {
                log_error "依赖安装失败"
                do_rollback
                exit 1
            }
        else
            log_error "依赖安装失败"
            do_rollback
            exit 1
        fi
    fi
    log_ok "依赖更新完成"

    # 重启服务
    log_step "重启服务..."
    systemctl restart "$SERVICE_NAME" 2>/dev/null

    # 健康检查
    if ! health_check; then
        log_error "服务重启后未正常运行"
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
    local has_volume
    has_volume=$(docker inspect --format '{{range .Mounts}}{{.Destination}} {{end}}' ntrip-caster 2>/dev/null | grep -q '/app/' && echo "yes" || echo "no")

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
        for d in "/opt/ntripcaster" "$(pwd)"; do
            if [ -f "$d/docker-compose.yml" ]; then
                compose_dir="$d"
                break
            fi
        done

        if [ -n "$compose_dir" ]; then
            cd "$compose_dir" || { log_error "无法进入 $compose_dir"; exit 1; }
            docker compose up -d --no-deps ntripcaster 2>/dev/null || {
                log_error "compose 重建失败"
                do_rollback
                exit 1
            }
        else
            # docker run + volume 形态：stop -> rm -> 用相同挂载重新 run
            log_warn "未找到 compose 文件，使用 docker run 重建（保留原挂载卷）..."
            log_warn "需要手动重建容器，已拉取新镜像，请手动执行："
            log_warn "  docker stop ntrip-caster && docker rm ntrip-caster"
            log_warn "  然后按你原来的 docker run 命令（含 -v 挂载）重新启动，镜像已更新"
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
echo -e "${BLUE}   NTRIP Caster 一键无损更新 v1.0.0           ${NC}"
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
