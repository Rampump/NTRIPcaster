#!/bin/bash
# NTRIP Caster Docker部署脚本 v2.1.8
# 支持开发、测试、生产环境的完整部署解决方案

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 配置变量
IMAGE_NAME="ntrip-caster"
IMAGE_TAG="2.1.8"
CONTAINER_NAME="ntrip-caster"
NETWORK_NAME="ntrip-network"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT="development"
PROFILES=""
COMPOSE_FILES="-f docker-compose.yml"

# 函数定义
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

log_debug() {
    if [[ "${DEBUG:-false}" == "true" ]]; then
        echo -e "${PURPLE}[DEBUG]${NC} $1"
    fi
}

log_success() {
    echo -e "${CYAN}[SUCCESS]${NC} $1"
}

# 显示横幅
show_banner() {
    echo -e "${CYAN}"
    cat << 'EOF'
    ██████╗ ██████╗ ████████╗██╗  ██╗
    ╚════██╗██╔══██╗╚══██╔══╝██║ ██╔╝
     █████╔╝██████╔╝   ██║   █████╔╝ 
    ██╔═══╝ ██╔══██╗   ██║   ██╔═██╗ 
    ███████╗██║  ██║   ██║   ██║  ██╗
    ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝
EOF
    echo -e "${NC}"
    echo -e "${GREEN}    NTRIP Caster Docker 部署脚本 v2.1.8${NC}"
    echo -e "${BLUE}    环境: ${ENVIRONMENT} | 配置文件: ${COMPOSE_FILES}${NC}"
    echo
}

# 解析命令行参数
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --env|--environment)
                ENVIRONMENT="$2"
                shift 2
                ;;
            --profile)
                PROFILES="--profile $2 $PROFILES"
                shift 2
                ;;
            --debug)
                DEBUG="true"
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                break
                ;;
        esac
    done
    
    # 根据环境设置compose文件
    case "$ENVIRONMENT" in
        "production"|"prod")
            COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"
            ENVIRONMENT="production"
            ;;
        "development"|"dev")
            COMPOSE_FILES="-f docker-compose.yml -f docker-compose.override.yml"
            ENVIRONMENT="development"
            ;;
        "testing"|"test")
            COMPOSE_FILES="-f docker-compose.yml"
            ENVIRONMENT="testing"
            ;;
        *)
            log_warn "未知环境: $ENVIRONMENT，使用默认开发环境"
            ENVIRONMENT="development"
            COMPOSE_FILES="-f docker-compose.yml -f docker-compose.override.yml"
            ;;
    esac
    
    log_debug "环境: $ENVIRONMENT"
    log_debug "Compose文件: $COMPOSE_FILES"
    log_debug "Profiles: $PROFILES"
}

# 检查Docker是否安装
check_docker() {
    log_step "检查Docker环境..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker未安装，请先安装Docker"
        echo "安装命令:"
        echo "  Ubuntu/Debian: curl -fsSL https://get.docker.com | sh"
        echo "  CentOS/RHEL: curl -fsSL https://get.docker.com | sh"
        echo "  macOS: brew install docker"
        echo "  Windows: 下载Docker Desktop"
        exit 1
    fi
    
    # 检查Docker Compose (优先使用docker compose插件)
    if docker compose version &> /dev/null; then
        DOCKER_COMPOSE_CMD="docker compose"
        log_debug "使用Docker Compose插件"
    elif command -v docker-compose &> /dev/null; then
        DOCKER_COMPOSE_CMD="docker-compose"
        log_debug "使用独立的docker-compose"
    else
        log_error "Docker Compose未安装，请先安装Docker Compose"
        echo "安装命令:"
        echo "  插件方式: docker plugin install docker/compose"
        echo "  独立安装: sudo curl -L \"https://github.com/docker/compose/releases/latest/download/docker-compose-\$(uname -s)-\$(uname -m)\" -o /usr/local/bin/docker-compose"
        echo "           sudo chmod +x /usr/local/bin/docker-compose"
        exit 1
    fi
    
    # 检查Docker守护进程是否运行
    if ! docker info &> /dev/null; then
        log_error "Docker守护进程未运行，请启动Docker服务"
        echo "启动命令:"
        echo "  systemd: sudo systemctl start docker"
        echo "  macOS/Windows: 启动Docker Desktop"
        exit 1
    fi
    
    # 显示版本信息
    local docker_version=$(docker --version | cut -d' ' -f3 | cut -d',' -f1)
    local compose_version=$($DOCKER_COMPOSE_CMD version --short 2>/dev/null || echo "unknown")
    
    log_info "Docker环境检查通过"
    log_debug "Docker版本: $docker_version"
    log_debug "Compose版本: $compose_version"
}

# 创建必要的目录
create_directories() {
    log_step "创建必要的目录结构..."
    
    # 基础目录
    local dirs=(
        "data"
        "logs"
        "config"
        "secrets"
        "nginx/conf.d"
        "nginx/ssl"
        "nginx/logs"
        "redis"
        "monitoring/prometheus/rules"
        "monitoring/grafana/provisioning/datasources"
        "monitoring/grafana/provisioning/dashboards"
        "monitoring/grafana/dashboards"
        "backup"
    )
    
    for dir in "${dirs[@]}"; do
        if [[ ! -d "$dir" ]]; then
            mkdir -p "$dir"
            log_debug "创建目录: $dir"
        fi
    done
    
    # 设置目录权限
    chmod 755 data logs config
    chmod 700 secrets
    
    # 复制配置文件
    if [[ ! -f "config/config.ini" && -f "config.ini" ]]; then
        cp config.ini config/config.ini
        log_info "配置文件已复制到 config/config.ini"
    fi
    
    # 创建环境配置文件
    if [[ ! -f ".env.${ENVIRONMENT}" ]]; then
        create_env_file
    fi
    
    log_success "目录结构创建完成"
}

# 创建环境配置文件
create_env_file() {
    log_step "创建环境配置文件..."
    
    cat > ".env.${ENVIRONMENT}" << EOF
# ${ENVIRONMENT} 环境配置
COMPOSE_PROJECT_NAME=ntrip-${ENVIRONMENT}
COMPOSE_FILE=${COMPOSE_FILES// /,}
ENVIRONMENT=${ENVIRONMENT}

# 应用配置
NTRIP_HOST=0.0.0.0
NTRIP_PORT=2101
WEB_HOST=0.0.0.0
WEB_PORT=5757

# 日志配置
LOG_LEVEL=INFO
LOG_FORMAT=json

# 数据库配置
DATABASE_PATH=/app/data/2rtk.db

# 时区配置
TZ=Asia/Shanghai
EOF
    
    log_info "环境配置文件已创建: .env.${ENVIRONMENT}"
}

# 创建Nginx配置
create_nginx_config() {
    log_step "创建Nginx配置..."
    
    cat > nginx/nginx.conf << 'EOF'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    access_log /var/log/nginx/access.log main;
    
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types
        text/plain
        text/css
        text/xml
        text/javascript
        application/json
        application/javascript
        application/xml+rss
        application/atom+xml
        image/svg+xml;
    
    include /etc/nginx/conf.d/*.conf;
}
EOF

    cat > nginx/conf.d/ntrip.conf << 'EOF'
server {
    listen 80;
    server_name _;
    
    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;
    add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
    
    # Web管理界面
    location / {
        proxy_pass http://ntrip-caster:5757;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
    
    # 健康检查
    location /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
}

# NTRIP服务代理（可选）
stream {
    upstream ntrip_backend {
        server ntrip-caster:2101;
    }
    
    server {
        listen 2101;
        proxy_pass ntrip_backend;
        proxy_timeout 1s;
        proxy_responses 1;
        error_log /var/log/nginx/ntrip.log;
    }
}
EOF

    log_info "Nginx配置创建完成"
}

# 创建监控配置
create_monitoring_config() {
    log_step "创建监控配置..."
    
    cat > monitoring/prometheus.yml << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  # - "first_rules.yml"
  # - "second_rules.yml"

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
  
  - job_name: 'ntrip-caster'
    static_configs:
      - targets: ['ntrip-caster:5757']
    metrics_path: '/metrics'
    scrape_interval: 30s
EOF

    mkdir -p monitoring/grafana/provisioning/datasources
    cat > monitoring/grafana/provisioning/datasources/prometheus.yml << 'EOF'
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
EOF

    log_info "监控配置创建完成"
}

# 构建镜像
build_image() {
    log_step "构建Docker镜像..."
    
    if [ -f "Dockerfile" ]; then
        docker build -t $IMAGE_NAME:$IMAGE_TAG .
        docker tag $IMAGE_NAME:$IMAGE_TAG $IMAGE_NAME:latest
        log_success "镜像构建完成: $IMAGE_NAME:$IMAGE_TAG"
    else
        log_info "未找到Dockerfile，使用docker-compose构建..."
        $DOCKER_COMPOSE_CMD $COMPOSE_FILES build
        log_success "镜像构建完成"
    fi
}

# 启动服务
start_services() {
    log_step "启动服务..."
    
    # 基础服务
    $DOCKER_COMPOSE_CMD $COMPOSE_FILES $PROFILES up -d ntrip-caster
    
    # 等待服务启动
    log_info "等待服务启动..."
    sleep 10
    
    # 检查服务状态
    if $DOCKER_COMPOSE_CMD $COMPOSE_FILES ps | grep -q "Up"; then
        log_info "NTRIP Caster服务启动成功"
        check_health
    else
        log_error "服务启动失败"
        $DOCKER_COMPOSE_CMD $COMPOSE_FILES logs ntrip-caster
        exit 1
    fi
}

# 启动完整服务（包括Nginx和监控）
start_full_services() {
    log_step "启动完整服务栈..."
    
    $DOCKER_COMPOSE_CMD $COMPOSE_FILES --profile nginx --profile monitoring up -d
    
    # 等待服务启动
    log_info "等待服务启动..."
    sleep 15
    
    check_health
    show_info
    log_success "完整服务栈启动完成"
}

# 停止服务
stop_services() {
    log_step "停止服务..."
    
    $DOCKER_COMPOSE_CMD $COMPOSE_FILES $PROFILES down
    
    log_success "服务已停止"
}

# 清理资源
clean_resources() {
    log_step "清理Docker资源..."
    
    # 停止并删除容器
    $DOCKER_COMPOSE_CMD $COMPOSE_FILES down -v --remove-orphans
    
    # 删除镜像
    docker rmi $IMAGE_NAME:$IMAGE_TAG $IMAGE_NAME:latest 2>/dev/null || true
    
    # 清理未使用的资源
    docker system prune -f
    docker volume prune -f
    
    log_success "资源清理完成"
}

# 查看日志
view_logs() {
    $DOCKER_COMPOSE_CMD $COMPOSE_FILES logs -f ntrip-caster
}

# 查看状态
view_status() {
    echo "=== Docker Compose状态 ==="
    $DOCKER_COMPOSE_CMD $COMPOSE_FILES ps
    echo
    echo "=== 容器资源使用 ==="
    docker stats --no-stream
    echo
    echo "=== 服务健康状态 ==="
    if docker ps --format "table {{.Names}}\t{{.Status}}" | grep -q "ntrip-caster.*Up"; then
        if $DOCKER_COMPOSE_CMD $COMPOSE_FILES exec -T ntrip-caster curl -f http://localhost:5757/ >/dev/null 2>&1; then
            echo "✓ Web服务正常"
        else
            echo "✗ Web服务异常"
        fi
    else
        echo "✗ NTRIP Caster服务未运行"
    fi
}

# 显示帮助信息
show_help() {
    echo "NTRIP Caster Docker部署脚本 v2.1.8"
    echo
    echo "用法: $0 [选项] [命令] [参数]"
    echo
    echo "选项:"
    echo "  --env, --environment ENV  指定环境 (development|testing|production)"
    echo "  --profile PROFILE         启用指定的compose profile"
    echo "  --debug                   启用调试模式"
    echo "  --help, -h               显示帮助信息"
    echo
    echo "命令:"
    echo "  build     - 构建Docker镜像"
    echo "  start     - 启动基础服务"
    echo "  full      - 启动完整服务（包括Nginx和监控）"
    echo "  stop      - 停止服务"
    echo "  restart   - 重启服务"
    echo "  logs      - 查看日志"
    echo "  status    - 查看状态"
    echo "  health    - 检查服务健康状态"
    echo "  info      - 显示服务信息"
    echo "  backup    - 备份数据"
    echo "  restore   - 恢复数据 (需要指定备份路径)"
    echo "  update    - 更新服务"
    echo "  clean     - 清理资源"
    echo "  help      - 显示帮助信息"
    echo
    echo "示例:"
    echo "  $0 --env production build && $0 start    # 生产环境构建并启动"
    echo "  $0 --profile nginx --profile monitoring full  # 启动完整服务栈"
    echo "  $0 --debug logs                          # 调试模式查看日志"
    echo "  $0 backup                                # 备份数据"
    echo "  $0 restore ./backup/20231201_120000     # 恢复数据"
    echo
    echo "环境说明:"
    echo "  development - 开发环境，包含调试工具"
    echo "  testing     - 测试环境，基础配置"
    echo "  production  - 生产环境，优化配置"
}

# 检查服务健康状态
check_health() {
    log_info "检查服务健康状态..."
    
    local services=("ntrip-caster" "ntrip-nginx" "ntrip-prometheus" "ntrip-grafana")
    local healthy=true
    
    for service in "${services[@]}"; do
        if $DOCKER_COMPOSE_CMD $COMPOSE_FILES $PROFILES ps --format "table {{.Service}}\t{{.Status}}" | grep -q "$service.*healthy"; then
            log_success "✓ $service: 健康"
        elif $DOCKER_COMPOSE_CMD $COMPOSE_FILES $PROFILES ps --format "table {{.Service}}\t{{.Status}}" | grep -q "$service.*Up"; then
            log_warn "⚠ $service: 运行中但健康检查未通过"
            healthy=false
        else
            log_error "✗ $service: 未运行"
            healthy=false
        fi
    done
    
    if [ "$healthy" = true ]; then
        log_success "所有服务运行正常"
    else
        log_warn "部分服务存在问题，请检查日志"
    fi
}

# 显示服务信息
show_info() {
    log_info "NTRIP Caster 服务信息:"
    echo
    echo "${BLUE}环境:${NC} $ENVIRONMENT"
    echo "${BLUE}配置文件:${NC} $COMPOSE_FILES"
    echo "${BLUE}项目名称:${NC} ${CONTAINER_NAME}"
    echo
    echo "${BLUE}服务端点:${NC}"
    echo "  • NTRIP Caster: http://localhost:2101"
    echo "  • Web界面: http://localhost:5757"
    echo "  • Prometheus: http://localhost:9090"
    echo "  • Grafana: http://localhost:3000"
    if [ "$ENVIRONMENT" = "development" ]; then
        echo "  • Adminer: http://localhost:8081"
        echo "  • Dozzle: http://localhost:8082"
        echo "  • cAdvisor: http://localhost:8083"
    fi
    echo
    
    if $DOCKER_COMPOSE_CMD $COMPOSE_FILES ps >/dev/null 2>&1; then
        echo "${BLUE}服务状态:${NC}"
        $DOCKER_COMPOSE_CMD $COMPOSE_FILES ps
    fi
}

# 备份数据
backup_data() {
    log_info "备份数据..."
    
    local backup_dir="./backup/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$backup_dir"
    
    # 备份配置文件
    log_info "备份配置文件..."
    cp -r config/ "$backup_dir/" 2>/dev/null || true
    cp -r nginx/ "$backup_dir/" 2>/dev/null || true
    cp -r monitoring/ "$backup_dir/" 2>/dev/null || true
    cp .env.* "$backup_dir/" 2>/dev/null || true
    
    # 备份数据卷
    log_info "备份数据卷..."
    if docker volume ls | grep -q "ntrip.*data"; then
        docker run --rm -v "ntrip-data:/data" -v "$(pwd)/$backup_dir:/backup" alpine tar czf /backup/ntrip-data.tar.gz -C /data .
    fi
    
    if docker volume ls | grep -q "prometheus.*data"; then
        docker run --rm -v "prometheus-data:/data" -v "$(pwd)/$backup_dir:/backup" alpine tar czf /backup/prometheus-data.tar.gz -C /data .
    fi
    
    if docker volume ls | grep -q "grafana.*data"; then
        docker run --rm -v "grafana-data:/data" -v "$(pwd)/$backup_dir:/backup" alpine tar czf /backup/grafana-data.tar.gz -C /data .
    fi
    
    log_success "备份完成: $backup_dir"
}

# 恢复数据
restore_data() {
    local backup_path="$1"
    
    if [ -z "$backup_path" ] || [ ! -d "$backup_path" ]; then
        log_error "请指定有效的备份目录路径"
        exit 1
    fi
    
    log_info "从 $backup_path 恢复数据..."
    
    # 停止服务
    $DOCKER_COMPOSE_CMD $COMPOSE_FILES down
    
    # 恢复配置文件
    if [ -d "$backup_path/config" ]; then
        log_info "恢复配置文件..."
        cp -r "$backup_path/config/" ./ 2>/dev/null || true
    fi
    
    # 恢复数据卷
    if [ -f "$backup_path/ntrip-data.tar.gz" ]; then
        log_info "恢复NTRIP数据..."
        docker run --rm -v "ntrip-data:/data" -v "$(realpath $backup_path):/backup" alpine tar xzf /backup/ntrip-data.tar.gz -C /data
    fi
    
    if [ -f "$backup_path/prometheus-data.tar.gz" ]; then
        log_info "恢复Prometheus数据..."
        docker run --rm -v "prometheus-data:/data" -v "$(realpath $backup_path):/backup" alpine tar xzf /backup/prometheus-data.tar.gz -C /data
    fi
    
    if [ -f "$backup_path/grafana-data.tar.gz" ]; then
        log_info "恢复Grafana数据..."
        docker run --rm -v "grafana-data:/data" -v "$(realpath $backup_path):/backup" alpine tar xzf /backup/grafana-data.tar.gz -C /data
    fi
    
    log_success "数据恢复完成"
}

# 更新服务
update_services() {
    log_info "更新服务..."
    
    # 拉取最新镜像
    log_info "拉取最新镜像..."
    $DOCKER_COMPOSE_CMD $COMPOSE_FILES pull
    
    # 重新构建本地镜像
    log_info "重新构建本地镜像..."
    $DOCKER_COMPOSE_CMD $COMPOSE_FILES build --no-cache
    
    # 重启服务
    log_info "重启服务..."
    $DOCKER_COMPOSE_CMD $COMPOSE_FILES up -d
    
    # 清理旧镜像
    log_info "清理未使用的镜像..."
    docker image prune -f
    
    log_success "服务更新完成"
}

# 主函数
main() {
    show_banner
    parse_args "$@"
    
    case "$1" in
        build)
            check_docker
            create_directories
            create_nginx_config
            create_monitoring_config
            build_image
            ;;
        start)
            check_docker
            create_directories
            start_services
            ;;
        full)
            check_docker
            create_directories
            create_nginx_config
            create_monitoring_config
            start_full_services
            ;;
        stop)
            check_docker
            stop_services
            ;;
        restart)
            check_docker
            stop_services
            sleep 2
            start_services
            ;;
        logs)
            check_docker
            view_logs
            ;;
        status)
            check_docker
            view_status
            ;;
        health)
            check_docker
            check_health
            ;;
        info)
            show_info
            ;;
        backup)
            check_docker
            backup_data
            ;;
        restore)
            check_docker
            restore_data "$2"
            ;;
        update)
            check_docker
            update_services
            ;;
        clean)
            check_docker
            clean_resources
            ;;
        help|--help|-h)
            show_help
            ;;
        "")
            log_info "开始自动部署..."
            check_docker
            create_directories
            create_nginx_config
            create_monitoring_config
            build_image
            start_services
            
            echo
            echo "==========================================="
            echo "    NTRIP Caster Docker部署完成"
            echo "==========================================="
            echo
            echo "服务地址:"
            echo "  - NTRIP服务: $(hostname -I | awk '{print $1}'):2101"
            echo "  - Web管理: http://$(hostname -I | awk '{print $1}'):5757"
            echo
            echo "管理命令:"
            echo "  - 查看状态: $0 status"
            echo "  - 查看日志: $0 logs"
            echo "  - 停止服务: $0 stop"
            echo "  - 重启服务: $0 restart"
            echo
            ;;
        *)
            log_error "未知命令: $1"
            show_help
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"