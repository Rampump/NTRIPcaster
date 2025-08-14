# NTRIP Caster Docker 安装和使用教程

本教程将指导您如何使用 Docker 快速部署和运行 NTRIP Caster v2.2.0。

## 目录
- [前置要求](#前置要求)
- [快速开始](#快速开始)
- [详细安装步骤](#详细安装步骤)
- [配置说明](#配置说明)
- [使用方法](#使用方法)
- [常见问题](#常见问题)
- [高级配置](#高级配置)

## 前置要求

### 1. 安装 Docker

#### Windows 系统
1. 下载并安装 [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
2. 启动 Docker Desktop
3. 验证安装：
   ```cmd
   docker --version
   docker-compose --version
   ```

#### Linux 系统 (Ubuntu/Debian)
```bash
# 更新包索引
sudo apt update

# 安装必要的包
sudo apt install apt-transport-https ca-certificates curl gnupg lsb-release

# 添加 Docker 官方 GPG 密钥
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# 添加 Docker 仓库
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 安装 Docker Engine
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 启动 Docker 服务
sudo systemctl start docker
sudo systemctl enable docker

# 将当前用户添加到 docker 组（可选）
sudo usermod -aG docker $USER
```

#### CentOS/RHEL 系统
```bash
# 安装 Docker
sudo yum install -y yum-utils
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo yum install docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 启动 Docker 服务
sudo systemctl start docker
sudo systemctl enable docker
```

## 快速开始

### 方法一：一键启动（最简单）

```bash
# 拉取并直接运行，镜像会自动创建所需目录和配置文件
docker run -d \
  --name ntrip-caster \
  -p 2101:2101 \
  -p 5757:5757 \
  2rtk/ntripcaster:latest
```

> **说明：** 镜像内置了启动脚本，会自动创建必要的目录（logs、data、config）并初始化默认配置文件。数据将存储在容器内部，只要不删除容器，数据就会持久化保存。适合快速测试、体验和轻量级部署。
>
> **数据持久化：** 只要容器不被删除（`docker rm`），所有数据（日志、数据库、配置）都会保留。重启容器（`docker restart ntrip-caster`）不会丢失数据。

### 方法二：持久化数据存储（推荐生产环境）

> **适用场景：** 数据存储在宿主机目录中，便于服务器迁移、版本升级、数据备份和配置管理。特别适合需要频繁维护或多环境部署的场景。

```bash
# 1. 拉取最新镜像
docker pull 2rtk/ntripcaster:latest

# 2. 创建数据目录
mkdir -p ./ntrip-data/{logs,data,config}

# 3. 复制配置文件模板
docker run --rm 2rtk/ntripcaster:latest cat /app/config.ini.example > ./ntrip-data/config/config.ini

# 4. 运行容器
docker run -d \
  --name ntrip-caster \
  -p 2101:2101 \
  -p 5757:5757 \
  -v $(pwd)/ntrip-data/logs:/app/logs \
  -v $(pwd)/ntrip-data/data:/app/data \
  -v $(pwd)/ntrip-data/config:/app/config \
  2rtk/ntripcaster:latest
```

### 方法三：使用 Docker Compose（推荐生产环境）

1. 下载项目文件：
```bash
git clone https://github.com/2rtk/NTRIPcaster.git
cd NTRIPcaster
```

2. 启动服务：
```bash
# 开发环境
docker-compose up -d

# 生产环境
docker-compose -f docker-compose.prod.yml up -d
```

## 详细安装步骤

### 1. 准备工作目录

```bash
# 创建项目目录
mkdir ntrip-caster && cd ntrip-caster

# 创建数据目录结构
mkdir -p data/{logs,data,config}
```

### 2. 准备配置文件

```bash
# 获取配置文件模板
docker run --rm 2rtk/ntripcaster:latest cat /app/config.ini.example > data/config/config.ini
```

### 3. 编辑配置文件

编辑 `data/config/config.ini` 文件，主要配置项：

```ini

[network]
host = 0.0.0.0
ntrip_port = 2101
web_port = 5757

[database]
path = data/2rtk.db

[logging]
log_dir = logs
log_level = INFO

[security]
secret_key = your-secret-key-here   #生产环境必须修改默认的key
password_hash_rounds = 12
```

### 4. 启动容器

#### 基础启动
```bash
docker run -d \
  --name ntrip-caster \
  --restart unless-stopped \
  -p 2101:2101 \
  -p 5757:5757 \
  -v $(pwd)/data/logs:/app/logs \
  -v $(pwd)/data/data:/app/data \
  -v $(pwd)/data/config:/app/config \
  2rtk/ntripcaster:latest
```

#### 带环境变量的启动
```bash
docker run -d \
  --name ntrip-caster \
  --restart unless-stopped \
  -p 2101:2101 \
  -p 5757:5757 \
  -e NTRIP_PORT=2101 \
  -e WEB_PORT=5757 \
  -e DEBUG_MODE=false \
  -v $(pwd)/data/logs:/app/logs \
  -v $(pwd)/data/data:/app/data \
  -v $(pwd)/data/config:/app/config \
  2rtk/ntripcaster:latest
```

## 配置说明

### 端口说明
- **2101**: NTRIP 服务端口（标准 NTRIP 端口）
- **5757**: Web 管理界面端口

### 数据卷说明
- `/app/logs`: 日志文件目录
- `/app/data`: 数据库和数据文件目录
- `/app/config`: 配置文件目录

### 环境变量
- `NTRIP_PORT`: NTRIP 服务端口（默认：2101）
- `WEB_PORT`: Web 服务端口（默认：5757）
- `DEBUG_MODE`: 调试模式（默认：false）
- `DATABASE_PATH`: 数据库路径（默认：data/2rtk.db）
- `SECRET_KEY`: 应用密钥

## 使用方法

### 1. 访问 Web 管理界面

打开浏览器访问：`http://localhost:5757`

默认管理员账户：
- 用户名：`admin`
- 密码：`admin123`

### 2. 添加挂载点

在 Web 界面中：
1. 登录管理界面
2. 点击"添加挂载点"
3. 填写挂载点信息：
   - 挂载点名称：如 `RTCM3`
   - 描述：挂载点描述
   - 格式：选择数据格式

### 3. 连接 NTRIP 客户端

使用 NTRIP 客户端连接：
- 服务器：`your-server-ip`
- 端口：`2101`
- 挂载点：您创建的挂载点名称
- 用户名/密码：在管理界面中设置

### 4. 查看日志

```bash
# 查看容器日志
docker logs ntrip-caster

# 实时查看日志
docker logs -f ntrip-caster

# 查看应用日志文件
tail -f data/logs/main.log
```

## 常见问题

### Q1: 容器启动失败 - 权限错误

**问题描述：**
如果遇到 `PermissionError: [Errno 13] Permission denied: '/app/logs/main.log'` 错误，这是由于Docker卷权限问题导致的。

**解决方案：**
```bash
# 1. 停止并删除现有容器
docker-compose down
docker rm ntrip-caster

# 2. 删除现有的数据卷（注意：这会清除所有数据）
docker volume rm ntripcaster_ntrip-logs ntripcaster_ntrip-data ntripcaster_ntrip-config

# 3. 重新构建镜像（如果使用最新版本）
docker-compose build --no-cache

# 4. 重新启动服务
docker-compose up -d
```

**其他启动问题检查：**
```bash
# 检查容器状态
docker ps -a

# 查看错误日志
docker logs ntrip-caster

# 检查端口占用
netstat -tlnp | grep :2101
netstat -tlnp | grep :5757
```

### Q2: 无法访问 Web 界面

**检查项：**
1. 确认容器正在运行：`docker ps`
2. 确认端口映射正确：`docker port ntrip-caster`
3. 检查防火墙设置
4. 确认配置文件中的端口设置

### Q3: NTRIP 客户端无法连接

**检查项：**
1. 确认 NTRIP 端口 2101 已开放
2. 检查挂载点是否正确创建
3. 验证用户名和密码
4. 查看服务器日志

### Q4: 数据持久化问题

**解决方案：**
```bash
# 确保数据卷正确挂载
docker inspect ntrip-caster | grep Mounts -A 20

# 检查目录权限
ls -la data/
sudo chown -R 1000:1000 data/
```

## 高级配置

### 1. 使用 Docker Compose

创建 `docker-compose.yml` 文件：

```yaml
version: '3.8'

services:
  ntrip-caster:
    image: 2rtk/ntripcaster:latest
    container_name: ntrip-caster
    restart: unless-stopped
    ports:
      - "2101:2101"
      - "5757:5757"
    volumes:
      - ./data/logs:/app/logs
      - ./data/data:/app/data
      - ./data/config:/app/config
    environment:
      - NTRIP_PORT=2101
      - WEB_PORT=5757
      - DEBUG_MODE=false
    healthcheck:
      test: ["CMD", "python", "/app/healthcheck.py"]
      interval: 30s
      timeout: 15s
      retries: 3
      start_period: 90s

  # 可选：添加 Nginx 反向代理
  nginx:
    image: nginx:alpine
    container_name: ntrip-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf
      - ./nginx/ssl:/etc/nginx/ssl
    depends_on:
      - ntrip-caster
```

启动服务：
```bash
docker-compose up -d
```

### 2. 生产环境部署

#### 使用 SSL/TLS

1. 准备 SSL 证书
2. 配置 Nginx 反向代理
3. 更新防火墙规则

#### 监控和日志

```bash
# 设置日志轮转
docker run -d \
  --name ntrip-caster \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  # ... 其他参数
```

### 3. 备份和恢复

#### 备份数据
```bash
# 备份数据目录
tar -czf ntrip-backup-$(date +%Y%m%d).tar.gz data/

# 备份数据库
docker exec ntrip-caster sqlite3 /app/data/2rtk.db ".backup /app/data/backup.db"
```

#### 恢复数据
```bash
# 停止容器
docker stop ntrip-caster

# 恢复数据
tar -xzf ntrip-backup-20231201.tar.gz

# 重启容器
docker start ntrip-caster
```

### 4. 性能优化

#### 资源限制
```bash
docker run -d \
  --name ntrip-caster \
  --memory=512m \
  --cpus=1.0 \
  # ... 其他参数
```

#### 网络优化
```bash
# 创建自定义网络
docker network create ntrip-network

# 使用自定义网络
docker run -d \
  --name ntrip-caster \
  --network ntrip-network \
  # ... 其他参数
```

## 更新和维护

### 更新到新版本

```bash
# 1. 备份数据
tar -czf backup-$(date +%Y%m%d).tar.gz data/

# 2. 停止并删除旧容器
docker stop ntrip-caster
docker rm ntrip-caster

# 3. 拉取新镜像
docker pull 2rtk/ntripcaster:latest

# 4. 启动新容器
docker run -d \
  --name ntrip-caster \
  --restart unless-stopped \
  -p 2101:2101 \
  -p 5757:5757 \
  -v $(pwd)/data/logs:/app/logs \
  -v $(pwd)/data/data:/app/data \
  -v $(pwd)/data/config:/app/config \
  2rtk/ntripcaster:latest
```

### 健康检查

```bash
# 检查容器健康状态
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# 手动执行健康检查
docker exec ntrip-caster python /app/healthcheck.py
```

## 技术支持

如果您在使用过程中遇到问题，可以：

1. 查看项目文档：[GitHub Repository](https://github.com/2rtk/NTRIPcaster)
2. 提交 Issue：[GitHub Issues](https://github.com/2rtk/NTRIPcaster/issues)
3. 联系作者：i@jia.by
4. 访问官网：https://2rtk.com

---

**版本信息：** NTRIP Caster v2.2.0  
**更新时间：** 2025年8月  
**作者：** i@jia.by