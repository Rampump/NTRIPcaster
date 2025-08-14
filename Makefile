# NTRIP Caster Docker Makefile
# 简化Docker操作的便捷工具

# 默认环境
ENV ?= development
PROFILE ?= 
DEBUG ?= false

# 项目配置
PROJECT_NAME := ntrip-caster
IMAGE_NAME := $(PROJECT_NAME)
IMAGE_TAG := latest

# Docker Compose 文件
COMPOSE_FILE := docker-compose.yml
ifeq ($(ENV),development)
	COMPOSE_FILE += -f docker-compose.override.yml
else ifeq ($(ENV),production)
	COMPOSE_FILE += -f docker-compose.prod.yml
endif

# Docker Compose 命令
DOCKER_COMPOSE := docker compose $(addprefix -f ,$(COMPOSE_FILE))
ifeq ($(PROFILE),)
	DOCKER_COMPOSE_CMD := $(DOCKER_COMPOSE)
else
	DOCKER_COMPOSE_CMD := $(DOCKER_COMPOSE) $(addprefix --profile ,$(PROFILE))
endif

# 颜色定义
RED := \033[31m
GREEN := \033[32m
YELLOW := \033[33m
BLUE := \033[34m
MAGENTA := \033[35m
CYAN := \033[36m
WHITE := \033[37m
RESET := \033[0m

# 默认目标
.DEFAULT_GOAL := help

# 帮助信息
.PHONY: help
help: ## 显示帮助信息
	@echo "$(CYAN)NTRIP Caster Docker Makefile$(RESET)"
	@echo ""
	@echo "$(YELLOW)用法:$(RESET)"
	@echo "  make [目标] [变量=值]"
	@echo ""
	@echo "$(YELLOW)变量:$(RESET)"
	@echo "  ENV=development|testing|production  指定环境 (默认: development)"
	@echo "  PROFILE=nginx,monitoring            指定compose profile"
	@echo "  DEBUG=true|false                    启用调试模式 (默认: false)"
	@echo ""
	@echo "$(YELLOW)目标:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  $(GREEN)%-20s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "$(YELLOW)示例:$(RESET)"
	@echo "  make build ENV=production          # 构建生产环境镜像"
	@echo "  make up PROFILE=nginx,monitoring   # 启动完整服务栈"
	@echo "  make logs SERVICE=ntrip-caster     # 查看特定服务日志"

# 环境检查
.PHONY: check-env
check-env: ## 检查环境依赖
	@echo "$(BLUE)检查环境依赖...$(RESET)"
	@command -v docker >/dev/null 2>&1 || { echo "$(RED)错误: Docker 未安装$(RESET)"; exit 1; }
	@command -v docker compose >/dev/null 2>&1 || { echo "$(RED)错误: Docker Compose 未安装$(RESET)"; exit 1; }
	@echo "$(GREEN)✓ Docker 环境检查通过$(RESET)"
	@docker --version
	@docker compose version

# 创建必要目录
.PHONY: setup
setup: ## 创建必要的目录和文件
	@echo "$(BLUE)创建项目目录结构...$(RESET)"
	@mkdir -p data logs config backup
	@mkdir -p secrets nginx/logs redis
	@mkdir -p monitoring/prometheus/rules
	@mkdir -p monitoring/grafana/provisioning/datasources
	@mkdir -p monitoring/grafana/provisioning/dashboards
	@mkdir -p monitoring/grafana/dashboards
	@echo "$(GREEN)✓ 目录结构创建完成$(RESET)"

# 构建镜像
.PHONY: build
build: check-env setup ## 构建Docker镜像
	@echo "$(BLUE)构建Docker镜像 (环境: $(ENV))...$(RESET)"
	$(DOCKER_COMPOSE_CMD) build
	@echo "$(GREEN)✓ 镜像构建完成$(RESET)"

# 拉取镜像
.PHONY: pull
pull: check-env ## 拉取Docker镜像
	@echo "$(BLUE)拉取Docker镜像...$(RESET)"
	$(DOCKER_COMPOSE_CMD) pull
	@echo "$(GREEN)✓ 镜像拉取完成$(RESET)"

# 启动服务
.PHONY: up
up: check-env setup ## 启动服务
	@echo "$(BLUE)启动服务 (环境: $(ENV))...$(RESET)"
	$(DOCKER_COMPOSE_CMD) up -d
	@sleep 5
	@$(MAKE) health
	@$(MAKE) info
	@echo "$(GREEN)✓ 服务启动完成$(RESET)"

# 停止服务
.PHONY: down
down: ## 停止服务
	@echo "$(BLUE)停止服务...$(RESET)"
	$(DOCKER_COMPOSE_CMD) down
	@echo "$(GREEN)✓ 服务已停止$(RESET)"

# 重启服务
.PHONY: restart
restart: ## 重启服务
	@echo "$(BLUE)重启服务...$(RESET)"
	$(DOCKER_COMPOSE_CMD) restart
	@sleep 5
	@$(MAKE) health
	@echo "$(GREEN)✓ 服务重启完成$(RESET)"

# 查看状态
.PHONY: status
status: ## 查看服务状态
	@echo "$(BLUE)服务状态:$(RESET)"
	$(DOCKER_COMPOSE_CMD) ps

# 查看日志
.PHONY: logs
logs: ## 查看服务日志 (SERVICE=服务名)
	@echo "$(BLUE)查看服务日志...$(RESET)"
ifeq ($(SERVICE),)
	$(DOCKER_COMPOSE_CMD) logs -f
else
	$(DOCKER_COMPOSE_CMD) logs -f $(SERVICE)
endif

# 健康检查
.PHONY: health
health: ## 检查服务健康状态
	@echo "$(BLUE)检查服务健康状态...$(RESET)"
	@./docker-deploy.sh health 2>/dev/null || echo "$(YELLOW)请使用 './docker-deploy.sh health' 进行详细健康检查$(RESET)"

# 显示服务信息
.PHONY: info
info: ## 显示服务信息
	@echo "$(BLUE)NTRIP Caster 服务信息:$(RESET)"
	@echo ""
	@echo "$(CYAN)环境:$(RESET) $(ENV)"
	@echo "$(CYAN)配置文件:$(RESET) $(COMPOSE_FILE)"
	@echo "$(CYAN)项目名称:$(RESET) $(PROJECT_NAME)"
	@echo ""
	@echo "$(CYAN)服务端点:$(RESET)"
	@echo "  • NTRIP Caster: http://localhost:2101"
	@echo "  • Web界面: http://localhost:5757"
	@echo "  • Prometheus: http://localhost:9090"
	@echo "  • Grafana: http://localhost:3000"
ifeq ($(ENV),development)
	@echo "  • Adminer: http://localhost:8081"
	@echo "  • Dozzle: http://localhost:8082"
	@echo "  • cAdvisor: http://localhost:8083"
endif
	@echo ""

# 进入容器
.PHONY: shell
shell: ## 进入容器shell (SERVICE=服务名，默认ntrip-caster)
	@echo "$(BLUE)进入容器shell...$(RESET)"
	$(DOCKER_COMPOSE_CMD) exec $(or $(SERVICE),ntrip-caster) /bin/bash

# 执行命令
.PHONY: exec
exec: ## 在容器中执行命令 (SERVICE=服务名 CMD=命令)
	@echo "$(BLUE)在容器中执行命令...$(RESET)"
	$(DOCKER_COMPOSE_CMD) exec $(or $(SERVICE),ntrip-caster) $(CMD)

# 备份数据
.PHONY: backup
backup: ## 备份数据
	@echo "$(BLUE)备份数据...$(RESET)"
	@./docker-deploy.sh backup
	@echo "$(GREEN)✓ 数据备份完成$(RESET)"

# 恢复数据
.PHONY: restore
restore: ## 恢复数据 (BACKUP_PATH=备份路径)
	@echo "$(BLUE)恢复数据...$(RESET)"
	@if [ -z "$(BACKUP_PATH)" ]; then \
		echo "$(RED)错误: 请指定备份路径 BACKUP_PATH=<路径>$(RESET)"; \
		exit 1; \
	fi
	@./docker-deploy.sh restore $(BACKUP_PATH)
	@echo "$(GREEN)✓ 数据恢复完成$(RESET)"

# 更新服务
.PHONY: update
update: ## 更新服务
	@echo "$(BLUE)更新服务...$(RESET)"
	@$(MAKE) pull
	@$(MAKE) build
	@$(MAKE) restart
	@echo "$(GREEN)✓ 服务更新完成$(RESET)"

# 清理资源
.PHONY: clean
clean: ## 清理Docker资源
	@echo "$(BLUE)清理Docker资源...$(RESET)"
	$(DOCKER_COMPOSE_CMD) down -v --remove-orphans
	docker system prune -f
	docker volume prune -f
	@echo "$(GREEN)✓ 资源清理完成$(RESET)"

# 深度清理
.PHONY: clean-all
clean-all: ## 深度清理（包括镜像）
	@echo "$(BLUE)深度清理Docker资源...$(RESET)"
	@$(MAKE) clean
	docker image prune -a -f
	docker builder prune -a -f
	@echo "$(GREEN)✓ 深度清理完成$(RESET)"

# 开发环境快捷方式
.PHONY: dev
dev: ## 启动开发环境
	@$(MAKE) up ENV=development

# 生产环境快捷方式
.PHONY: prod
prod: ## 启动生产环境
	@$(MAKE) up ENV=production PROFILE=nginx,monitoring

# 测试环境快捷方式
.PHONY: test
test: ## 启动测试环境
	@$(MAKE) up ENV=testing

# 监控服务
.PHONY: monitoring
monitoring: ## 启动监控服务
	@echo "$(BLUE)启动监控服务...$(RESET)"
	$(DOCKER_COMPOSE_CMD) --profile monitoring up -d
	@echo "$(GREEN)✓ 监控服务启动完成$(RESET)"
	@echo "$(CYAN)Prometheus:$(RESET) http://localhost:9090"
	@echo "$(CYAN)Grafana:$(RESET) http://localhost:3000 (admin/admin)"

# 网络代理
.PHONY: proxy
proxy: ## 启动网络代理
	@echo "$(BLUE)启动网络代理...$(RESET)"
	$(DOCKER_COMPOSE_CMD) --profile nginx up -d
	@echo "$(GREEN)✓ 网络代理启动完成$(RESET)"

# 性能测试
.PHONY: benchmark
benchmark: ## 运行性能测试
	@echo "$(BLUE)运行性能测试...$(RESET)"
	@echo "$(YELLOW)TODO: 实现性能测试脚本$(RESET)"

# 安全扫描
.PHONY: security-scan
security-scan: ## 运行安全扫描
	@echo "$(BLUE)运行安全扫描...$(RESET)"
	@command -v trivy >/dev/null 2>&1 && trivy image $(IMAGE_NAME):$(IMAGE_TAG) || echo "$(YELLOW)请安装 trivy 进行安全扫描$(RESET)"

# 生成配置
.PHONY: config
config: ## 生成配置文件
	@echo "$(BLUE)生成配置文件...$(RESET)"
	$(DOCKER_COMPOSE_CMD) config

# 验证配置
.PHONY: validate
validate: ## 验证配置文件
	@echo "$(BLUE)验证配置文件...$(RESET)"
	$(DOCKER_COMPOSE_CMD) config --quiet
	@echo "$(GREEN)✓ 配置文件验证通过$(RESET)"

# 显示版本信息
.PHONY: version
version: ## 显示版本信息
	@echo "$(CYAN)NTRIP Caster Docker 版本信息:$(RESET)"
	@echo "项目: $(PROJECT_NAME)"
	@echo "镜像: $(IMAGE_NAME):$(IMAGE_TAG)"
	@echo "环境: $(ENV)"
	@docker --version
	@docker compose version

# 清理构建缓存
.PHONY: clean-cache
clean-cache: ## 清理构建缓存
	@echo "$(BLUE)清理构建缓存...$(RESET)"
	docker builder prune -f
	@echo "$(GREEN)✓ 构建缓存清理完成$(RESET)"

# 导出镜像
.PHONY: export
export: ## 导出Docker镜像
	@echo "$(BLUE)导出Docker镜像...$(RESET)"
	docker save -o $(PROJECT_NAME)-$(IMAGE_TAG).tar $(IMAGE_NAME):$(IMAGE_TAG)
	@echo "$(GREEN)✓ 镜像导出完成: $(PROJECT_NAME)-$(IMAGE_TAG).tar$(RESET)"

# 导入镜像
.PHONY: import
import: ## 导入Docker镜像 (FILE=镜像文件)
	@echo "$(BLUE)导入Docker镜像...$(RESET)"
	@if [ -z "$(FILE)" ]; then \
		echo "$(RED)错误: 请指定镜像文件 FILE=<文件路径>$(RESET)"; \
		exit 1; \
	fi
	docker load -i $(FILE)
	@echo "$(GREEN)✓ 镜像导入完成$(RESET)"

# 显示资源使用
.PHONY: stats
stats: ## 显示容器资源使用情况
	@echo "$(BLUE)容器资源使用情况:$(RESET)"
	docker stats --no-stream

# 显示网络信息
.PHONY: network
network: ## 显示网络信息
	@echo "$(BLUE)Docker网络信息:$(RESET)"
	docker network ls | grep ntrip
	docker network inspect ntrip-network 2>/dev/null || echo "$(YELLOW)网络 ntrip-network 不存在$(RESET)"

# 显示卷信息
.PHONY: volumes
volumes: ## 显示卷信息
	@echo "$(BLUE)Docker卷信息:$(RESET)"
	docker volume ls | grep ntrip

# 快速重建
.PHONY: rebuild
rebuild: ## 快速重建服务
	@echo "$(BLUE)快速重建服务...$(RESET)"
	@$(MAKE) down
	@$(MAKE) build
	@$(MAKE) up
	@echo "$(GREEN)✓ 服务重建完成$(RESET)"