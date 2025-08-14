# NTRIP Caster Docker Installation and Usage Tutorial

This tutorial will guide you through installing and deploying NTRIP Caster v2.2.0 using Docker, providing the fastest and most reliable deployment method.

## Table of Contents
- [System Requirements](#system-requirements)
- [Quick Start](#quick-start)
- [Detailed Installation](#detailed-installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Common Issues](#common-issues)
- [Advanced Configuration](#advanced-configuration)
- [Updates and Maintenance](#updates-and-maintenance)
- [Technical Support](#technical-support)

## System Requirements

### Minimum Requirements
- **Operating System**: Linux, Windows, macOS
- **Docker**: 20.10.0+
- **Docker Compose**: 2.0.0+ (optional but recommended)
- **CPU**: 1 core
- **Memory**: 512MB RAM
- **Storage**: 2GB available space
- **Network**: Stable internet connection

### Recommended Configuration
- **CPU**: 2+ cores
- **Memory**: 2GB+ RAM
- **Storage**: 10GB+ available space
- **Network**: Gigabit network connection

### Supported Platforms
- **Linux**: Ubuntu 18.04+, Debian 10+, CentOS 7+, RHEL 7+, Rocky Linux 8+
- **Windows**: Windows 10/11 with WSL2
- **macOS**: macOS 10.15+
- **Architecture**: AMD64, ARM64

## Quick Start

### Method 1: One-Click Start (Simplest)

```bash
# Pull and run directly, the image will automatically create required directories and config files
docker run -d \
  --name ntripcaster \
  -p 2101:2101 \
  -p 5757:5757 \
  2rtk/ntripcaster:latest

# Check if running
docker ps

# View logs
docker logs ntripcaster
```

> **Note:** The image has built-in startup scripts that automatically create necessary directories (logs, data, config) and initialize default configuration files. Data will be stored inside the container and persisted as long as the container is not deleted. Suitable for quick testing, evaluation, and lightweight deployments.
>
> **Data Persistence:** As long as the container is not deleted (`docker rm`), all data (logs, database, config) will be retained. Restarting the container (`docker restart ntripcaster`) will not cause data loss.

### Method 2: Using Pre-built Image with Persistent Storage (Recommended for Production)

> **Use Cases:** Data is stored in host directories, facilitating server migration, version upgrades, data backup, and configuration management. Particularly suitable for scenarios requiring frequent maintenance or multi-environment deployments.

```bash
# Pull the latest image
docker pull 2rtk/ntripcaster:latest

# Run the container
docker run -d \
  --name ntripcaster \
  -p 2101:2101 \
  -p 5757:5757 \
  -v ntripcaster_data:/app/data \
  -v ntripcaster_logs:/app/logs \
  -v ntripcaster_config:/app/config \
  2rtk/ntripcaster:latest

# Check if running
docker ps

# View logs
docker logs ntripcaster
```

### Method 3: Using Docker Compose (Recommended for Production)

```bash
# Download docker-compose.yml
wget https://raw.githubusercontent.com/2rtk/NTRIPcaster/main/docker-compose.yml

# Start services
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f ntripcaster
```

### Method 4: Build from Source

```bash
# Clone repository
git clone https://github.com/2rtk/NTRIPcaster.git
cd NTRIPcaster

# Build image
docker build -t ntripcaster:local .

# Run container
docker run -d \
  --name ntripcaster \
  -p 2101:2101 \
  -p 5757:5757 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/config:/app/config \
  ntripcaster:local
```

## Detailed Installation

### Step 1: Install Docker

#### Linux (Ubuntu/Debian)

```bash
# Update package index
sudo apt update

# Install required packages
sudo apt install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release

# Add Docker's official GPG key
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# Add Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Start and enable Docker
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group
sudo usermod -aG docker $USER

# Logout and login again, then test
docker --version
docker compose version
```

#### Linux (CentOS/RHEL)

```bash
# Install required packages
sudo yum install -y yum-utils

# Add Docker repository
sudo yum-config-manager \
    --add-repo \
    https://download.docker.com/linux/centos/docker-ce.repo

# Install Docker Engine
sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Start and enable Docker
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group
sudo usermod -aG docker $USER

# Test installation
docker --version
```

#### Windows

1. Download Docker Desktop from [https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)
2. Install Docker Desktop
3. Enable WSL2 integration
4. Restart computer
5. Open PowerShell and test: `docker --version`

#### macOS

1. Download Docker Desktop from [https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)
2. Install Docker Desktop
3. Start Docker Desktop
4. Open Terminal and test: `docker --version`

### Step 2: Download and Configure

#### Option A: Using Docker Compose (Recommended)

```bash
# Create project directory
mkdir ntripcaster && cd ntripcaster

# Download docker-compose.yml
wget https://raw.githubusercontent.com/2rtk/NTRIPcaster/main/docker-compose.yml

# Download environment file
wget https://raw.githubusercontent.com/2rtk/NTRIPcaster/main/.env.example -O .env

# Edit environment variables
nano .env  # or use your preferred editor
```

#### Option B: Manual Setup

```bash
# Create directories
mkdir -p ntripcaster/{data,logs,config}
cd ntripcaster

# Download sample configuration
wget https://raw.githubusercontent.com/2rtk/NTRIPcaster/main/config.ini.example -O config/config.ini

# Edit configuration
nano config/config.ini
```

### Step 3: Configure Application

Edit the configuration file `config/config.ini`:

```ini
[app]
name = 2RTK Ntrip Caster
version = 2.1.9
description = Ntrip Caster
author = 2rtk
contact = your-email@example.com
website = https://your-domain.com

[network]
host = 0.0.0.0
ntrip_port = 2101
web_port = 5757

[database]
path = /app/data/2rtk.db

[logging]
log_dir = /app/logs
log_level = INFO
log_file = main.log
log_format = %(asctime)s - %(name)s - %(levelname)s - %(message)s
log_max_size = 10485760
log_backup_count = 5

[security]
secret_key = your-secret-key-here
password_hash_rounds = 12
session_timeout = 3600

[performance]
max_connections = 1000
thread_pool_size = 10
max_workers = 20
connection_queue_size = 100
```

### Step 4: Start Services

#### Using Docker Compose

```bash
# Start in background
docker-compose up -d

# View logs
docker-compose logs -f

# Check status
docker-compose ps
```

#### Using Docker Run

```bash
# Run container
docker run -d \
  --name ntripcaster \
  --restart unless-stopped \
  -p 2101:2101 \
  -p 5757:5757 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/config:/app/config \
  -e TZ=UTC \
  2rtk/ntripcaster:2.1.9

# Check if running
docker ps

# View logs
docker logs -f ntripcaster
```

## Configuration

### Environment Variables

You can override configuration using environment variables:

```bash
# In .env file or docker-compose.yml
NTRIP_PORT=2101
WEB_PORT=5757
DEBUG_MODE=false
DATABASE_PATH=/app/data/2rtk.db
LOG_LEVEL=INFO
SECRET_KEY=your-secret-key
MAX_CONNECTIONS=1000
THREAD_POOL_SIZE=10
```

### Volume Mounts

```bash
# Data persistence
-v ntripcaster_data:/app/data      # Database and user data
-v ntripcaster_logs:/app/logs      # Application logs
-v ntripcaster_config:/app/config  # Configuration files

# Or use bind mounts
-v $(pwd)/data:/app/data
-v $(pwd)/logs:/app/logs
-v $(pwd)/config:/app/config
```

### Network Configuration

```bash
# Port mapping
-p 2101:2101  # NTRIP service port
-p 5757:5757  # Web interface port

# Custom network
docker network create ntripcaster-network
docker run --network ntripcaster-network ...
```

## Usage

### Accessing the Application

1. **Web Interface**: http://localhost:5757
2. **NTRIP Service**: ntrip://localhost:2101
3. **Health Check**: http://localhost:5757/health

### Basic Operations

```bash
# View container status
docker ps

# View logs
docker logs ntripcaster
docker logs -f ntripcaster  # Follow logs

# Execute commands in container
docker exec -it ntripcaster bash

# Stop container
docker stop ntripcaster

# Start container
docker start ntripcaster

# Restart container
docker restart ntripcaster

# Remove container
docker rm ntripcaster
```

### Docker Compose Operations

```bash
# Start services
docker-compose up -d

# Stop services
docker-compose down

# Restart services
docker-compose restart

# View logs
docker-compose logs -f ntripcaster

# Scale services
docker-compose up -d --scale ntripcaster=2

# Update services
docker-compose pull
docker-compose up -d
```

### Data Management

```bash
# Backup data
docker run --rm \
  -v ntripcaster_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/ntripcaster-data-$(date +%Y%m%d).tar.gz -C /data .

# Restore data
docker run --rm \
  -v ntripcaster_data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/ntripcaster-data-20231201.tar.gz -C /data

# View data
docker run --rm \
  -v ntripcaster_data:/data \
  alpine ls -la /data
```

## Common Issues

### Q1: Container fails to start

**Troubleshooting:**

```bash
# Check container logs
docker logs ntripcaster

# Check container status
docker ps -a

# Inspect container
docker inspect ntripcaster

# Test configuration
docker run --rm \
  -v $(pwd)/config:/app/config \
  2rtk/ntripcaster:2.1.9 \
  python -c "import configparser; c=configparser.ConfigParser(); c.read('/app/config/config.ini')"
```

### Q2: Port already in use

```bash
# Check what's using the port
sudo netstat -tlnp | grep :2101
sudo netstat -tlnp | grep :5757

# Use different ports
docker run -p 2102:2101 -p 5758:5757 ...

# Or stop conflicting services
sudo systemctl stop service-name
```

### Q3: Permission denied errors

**Problem Description:**
If you encounter `PermissionError: [Errno 13] Permission denied: '/app/logs/main.log'` error, this is caused by Docker volume permission issues.

**Solution:**
```bash
# 1. Stop and remove existing containers
docker-compose down
docker rm ntripcaster

# 2. Remove existing data volumes (WARNING: This will delete all data)
docker volume rm ntripcaster_ntrip-logs ntripcaster_ntrip-data ntripcaster_ntrip-config

# 3. Rebuild image (if using latest version)
docker-compose build --no-cache

# 4. Restart services
docker-compose up -d
```

**Alternative solutions:**
```bash
# Fix volume permissions
sudo chown -R 1000:1000 ./data ./logs ./config

# Or run with user mapping
docker run --user $(id -u):$(id -g) ...
```

### Q4: Cannot connect to Docker daemon

```bash
# Start Docker service
sudo systemctl start docker

# Add user to docker group
sudo usermod -aG docker $USER
# Logout and login again

# Check Docker status
sudo systemctl status docker
```

### Q5: Image pull fails

```bash
# Check network connectivity
ping docker.io

# Try different registry
docker pull registry.cn-hangzhou.aliyuncs.com/2rtk/ntripcaster:2.1.9

# Build from source
git clone https://github.com/2rtk/NTRIPcaster.git
cd NTRIPcaster
docker build -t ntripcaster:local .
```

### Q6: Database connection issues

```bash
# Check database file permissions
docker exec ntripcaster ls -la /app/data/

# Recreate database
docker exec ntripcaster rm -f /app/data/2rtk.db
docker restart ntripcaster

# Check database integrity
docker exec ntripcaster sqlite3 /app/data/2rtk.db "PRAGMA integrity_check;"
```

## Advanced Configuration

### Docker Compose with Multiple Services

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  ntripcaster:
    image: 2rtk/ntripcaster:2.1.9
    container_name: ntripcaster
    restart: unless-stopped
    ports:
      - "2101:2101"
      - "5757:5757"
    volumes:
      - ntripcaster_data:/app/data
      - ntripcaster_logs:/app/logs
      - ntripcaster_config:/app/config
    environment:
      - TZ=UTC
      - LOG_LEVEL=INFO
      - MAX_CONNECTIONS=1000
    networks:
      - ntripcaster-network
    healthcheck:
      test: ["CMD", "python", "/app/healthcheck.py"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  nginx:
    image: nginx:alpine
    container_name: ntripcaster-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
      - ./nginx/logs:/var/log/nginx
    depends_on:
      - ntripcaster
    networks:
      - ntripcaster-network

  redis:
    image: redis:alpine
    container_name: ntripcaster-redis
    restart: unless-stopped
    volumes:
      - redis_data:/data
      - ./redis/redis.conf:/etc/redis/redis.conf:ro
    command: redis-server /etc/redis/redis.conf
    networks:
      - ntripcaster-network

  prometheus:
    image: prom/prometheus:latest
    container_name: ntripcaster-prometheus
    restart: unless-stopped
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus:/etc/prometheus:ro
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/etc/prometheus/console_libraries'
      - '--web.console.templates=/etc/prometheus/consoles'
    networks:
      - ntripcaster-network

  grafana:
    image: grafana/grafana:latest
    container_name: ntripcaster-grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin123
      - GF_USERS_ALLOW_SIGN_UP=false
    networks:
      - ntripcaster-network

volumes:
  ntripcaster_data:
  ntripcaster_logs:
  ntripcaster_config:
  redis_data:
  prometheus_data:
  grafana_data:

networks:
  ntripcaster-network:
    driver: bridge
```

### SSL/TLS Configuration

Create Nginx configuration `nginx/conf.d/ntripcaster.conf`:

```nginx
server {
    listen 80;
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL Configuration
    ssl_certificate /etc/nginx/ssl/server.crt;
    ssl_certificate_key /etc/nginx/ssl/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Redirect HTTP to HTTPS
    if ($scheme != "https") {
        return 301 https://$host$request_uri;
    }

    # Web Interface Proxy
    location / {
        proxy_pass http://ntripcaster:5757;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 30s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }

    # NTRIP Service Proxy
    location /ntrip {
        proxy_pass http://ntripcaster:2101;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_connect_timeout 30s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }

    # Health Check
    location /health {
        proxy_pass http://ntripcaster:5757/health;
        access_log off;
    }
}
```

### Production Deployment

#### Resource Limits

```yaml
services:
  ntripcaster:
    image: 2rtk/ntripcaster:2.1.9
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
      restart_policy:
        condition: on-failure
        delay: 5s
        max_attempts: 3
        window: 120s
```

#### Health Checks

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:5757/health"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

#### Logging Configuration

```yaml
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
    labels: "service=ntripcaster"
```

### Backup and Recovery

#### Automated Backup Script

Create `backup.sh`:

```bash
#!/bin/bash

# NTRIP Caster Docker Backup Script

BACKUP_DIR="/opt/backups/ntripcaster"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="ntripcaster_backup_$DATE.tar.gz"

# Create backup directory
mkdir -p $BACKUP_DIR

# Stop services
docker-compose down

# Create backup
tar -czf $BACKUP_DIR/$BACKUP_FILE \
    docker-compose.yml \
    .env \
    nginx/ \
    monitoring/ \
    redis/

# Backup volumes
docker run --rm \
    -v ntripcaster_data:/data \
    -v ntripcaster_logs:/logs \
    -v ntripcaster_config:/config \
    -v $BACKUP_DIR:/backup \
    alpine tar czf /backup/volumes_$DATE.tar.gz -C / data logs config

# Start services
docker-compose up -d

# Clean old backups (keep 30 days)
find $BACKUP_DIR -name "*.tar.gz" -mtime +30 -delete

echo "Backup completed: $BACKUP_DIR/$BACKUP_FILE"
echo "Volumes backup: $BACKUP_DIR/volumes_$DATE.tar.gz"
```

#### Recovery Script

Create `restore.sh`:

```bash
#!/bin/bash

# NTRIP Caster Docker Recovery Script

if [ $# -eq 0 ]; then
    echo "Usage: $0 <backup_date>"
    echo "Example: $0 20231201_120000"
    exit 1
fi

BACKUP_DATE=$1
BACKUP_DIR="/opt/backups/ntripcaster"

# Stop services
docker-compose down

# Remove existing volumes
docker volume rm ntripcaster_data ntripcaster_logs ntripcaster_config

# Restore configuration files
tar -xzf $BACKUP_DIR/ntripcaster_backup_$BACKUP_DATE.tar.gz

# Restore volumes
docker run --rm \
    -v ntripcaster_data:/data \
    -v ntripcaster_logs:/logs \
    -v ntripcaster_config:/config \
    -v $BACKUP_DIR:/backup \
    alpine tar xzf /backup/volumes_$BACKUP_DATE.tar.gz -C /

# Start services
docker-compose up -d

echo "Recovery completed from backup: $BACKUP_DATE"
```

### Performance Optimization

#### Docker Daemon Configuration

Edit `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "storage-driver": "overlay2",
  "storage-opts": [
    "overlay2.override_kernel_check=true"
  ],
  "default-ulimits": {
    "nofile": {
      "Name": "nofile",
      "Hard": 65536,
      "Soft": 65536
    }
  },
  "max-concurrent-downloads": 10,
  "max-concurrent-uploads": 5
}
```

#### Container Optimization

```yaml
services:
  ntripcaster:
    image: 2rtk/ntripcaster:2.1.9
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
      nproc:
        soft: 4096
        hard: 4096
    sysctls:
      - net.core.somaxconn=1024
      - net.ipv4.tcp_keepalive_time=600
      - net.ipv4.tcp_keepalive_intvl=60
      - net.ipv4.tcp_keepalive_probes=3
```

### Monitoring and Alerting

#### Prometheus Configuration

Create `monitoring/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules/*.yml"

scrape_configs:
  - job_name: 'ntripcaster'
    static_configs:
      - targets: ['ntripcaster:5757']
    metrics_path: '/metrics'
    scrape_interval: 30s

  - job_name: 'docker'
    static_configs:
      - targets: ['host.docker.internal:9323']

  - job_name: 'node-exporter'
    static_configs:
      - targets: ['node-exporter:9100']

alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093
```

#### Alert Rules

Create `monitoring/prometheus/rules/ntripcaster.yml`:

```yaml
groups:
  - name: ntripcaster
    rules:
      - alert: NTRIPCasterDown
        expr: up{job="ntripcaster"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "NTRIP Caster is down"
          description: "NTRIP Caster has been down for more than 1 minute."

      - alert: HighMemoryUsage
        expr: (container_memory_usage_bytes{name="ntripcaster"} / container_spec_memory_limit_bytes{name="ntripcaster"}) * 100 > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High memory usage"
          description: "NTRIP Caster memory usage is above 80%"

      - alert: HighCPUUsage
        expr: rate(container_cpu_usage_seconds_total{name="ntripcaster"}[5m]) * 100 > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High CPU usage"
          description: "NTRIP Caster CPU usage is above 80%"
```

## Updates and Maintenance

### Updating the Application

```bash
# Pull latest image
docker pull 2rtk/ntripcaster:latest

# Using Docker Compose
docker-compose pull
docker-compose up -d

# Using Docker Run
docker stop ntripcaster
docker rm ntripcaster
docker run -d \
  --name ntripcaster \
  --restart unless-stopped \
  -p 2101:2101 \
  -p 5757:5757 \
  -v ntripcaster_data:/app/data \
  -v ntripcaster_logs:/app/logs \
  -v ntripcaster_config:/app/config \
  2rtk/ntripcaster:latest
```

### Version Management

```bash
# Use specific version
docker pull 2rtk/ntripcaster:2.1.9

# List available tags
curl -s https://registry.hub.docker.com/v2/repositories/2rtk/ntripcaster/tags/ | jq '.results[].name'

# Rollback to previous version
docker-compose down
docker tag 2rtk/ntripcaster:2.1.8 2rtk/ntripcaster:rollback
docker-compose up -d
```

### Maintenance Tasks

```bash
# Clean up unused images
docker image prune -a

# Clean up unused volumes
docker volume prune

# Clean up unused networks
docker network prune

# Clean up everything
docker system prune -a --volumes

# View disk usage
docker system df

# Monitor resource usage
docker stats ntripcaster
```

### Log Management

```bash
# View logs
docker logs ntripcaster
docker logs -f --tail 100 ntripcaster

# Export logs
docker logs ntripcaster > ntripcaster.log

# Rotate logs
docker-compose restart ntripcaster

# Configure log rotation in docker-compose.yml
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

## Uninstallation

To completely remove NTRIP Caster:

```bash
# Stop and remove containers
docker-compose down

# Remove images
docker rmi 2rtk/ntripcaster:2.1.9
docker rmi 2rtk/ntripcaster:latest

# Remove volumes (WARNING: This will delete all data)
docker volume rm ntripcaster_data ntripcaster_logs ntripcaster_config

# Remove networks
docker network rm ntripcaster-network

# Clean up
docker system prune -a

# Remove project files
rm -rf ntripcaster/
```

## Technical Support

If you encounter issues during installation or usage:

1. **Check Logs**: `docker logs ntripcaster`
2. **Check Status**: `docker ps -a`
3. **View Documentation**: [GitHub Repository](https://github.com/2rtk/NTRIPcaster)
4. **Report Issues**: [GitHub Issues](https://github.com/2rtk/NTRIPcaster/issues)
5. **Contact Author**: i@jia.by
6. **Visit Website**: https://2rtk.com

### Useful Commands for Troubleshooting

```bash
# Container information
docker inspect ntripcaster

# Resource usage
docker stats ntripcaster

# Network information
docker network ls
docker network inspect bridge

# Volume information
docker volume ls
docker volume inspect ntripcaster_data

# Image information
docker images
docker history 2rtk/ntripcaster:2.1.9

# System information
docker info
docker version
```

## Changelog

- **v2.2.0**: Latest version with enhanced Docker support
- **v2.1.8**: Performance optimizations and security enhancements
- **v2.1.7**: Added monitoring and logging features

---

**Version**: NTRIP Caster v2.2.0  
**Updated**: December 2024  
**Author**: 2RTK Team  
**License**: Apache 2.0