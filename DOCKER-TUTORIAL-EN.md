# NTRIP Caster Docker Installation and Usage Tutorial

This tutorial will guide you through installing and deploying NTRIP Caster v2.2.0 using Docker, providing the fastest and most reliable deployment method.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Detailed Installation](#detailed-installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Common Issues](#common-issues)
- [Advanced Configuration](#advanced-configuration)
- [Updates and Maintenance](#updates-and-maintenance)
- [Technical Support](#technical-support)

## Prerequisites

### System Requirements
- **Operating System**: Linux, Windows, macOS
- **Docker**: 20.10.0+
- **Docker Compose**: 2.0.0+ (optional)
- **Memory**: 512MB+ RAM
- **Storage**: 2GB+ available space
- **Network**: Stable internet connection

### Install Docker

#### Ubuntu/Debian
```bash
# Update package index
sudo apt update

# Install Docker
sudo apt install docker.io docker-compose

# Start Docker service
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group
sudo usermod -aG docker $USER

# Logout and login again, then test
docker --version
```

#### CentOS/RHEL
```bash
# Install Docker
sudo yum install -y docker docker-compose

# Start Docker service
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group
sudo usermod -aG docker $USER

# Test installation
docker --version
```

## Quick Start

### Method 1: One-Click Start
```bash
# Pull and run directly
docker run -d \
  --name ntrip-caster \
  --restart unless-stopped \
  -p 2101:2101 \
  -p 5757:5757 \
  2rtk/ntripcaster:latest

# Check status
docker ps

# View logs
docker logs ntrip-caster
```

### Method 2: Persistent Data Storage
```bash
# Create data directory
mkdir -p data/{logs,data,config}

# Run with volume mounts
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

### Method 3: Using Docker Compose
```bash
# Download docker-compose.yml
wget https://raw.githubusercontent.com/Rampump/NTRIPcaster/main/docker-compose.yml

# Start services
docker-compose up -d

# Check status
docker-compose ps
```

## Detailed Installation

### 1. Prepare Working Directory
```bash
# Create project directory
mkdir ntripcaster-docker
cd ntripcaster-docker

# Create data directories
mkdir -p data/{logs,data,config}
```

### 2. Download Configuration Files
```bash
# Download docker-compose.yml
wget https://raw.githubusercontent.com/Rampump/NTRIPcaster/main/docker-compose.yml

# Download environment file
wget https://raw.githubusercontent.com/Rampump/NTRIPcaster/main/.env.example -O .env

# Download configuration file
wget https://raw.githubusercontent.com/Rampump/NTRIPcaster/main/config.ini.example -O data/config/config.ini
```

### 3. Edit Configuration Files

#### Edit .env file
```bash
vim .env
```

Configuration content:
```ini
# NTRIP Caster Configuration
NTRIP_PORT=2101
WEB_PORT=5757
DEBUG_MODE=false
DATABASE_PATH=data/2rtk.db
SECRET_KEY=your-secret-key-here
```

#### Edit config.ini file
```bash
vim data/config/config.ini
```

Configuration content:
```ini
[server]
ntrip_port = 2101
web_port = 5757

[database]
path = data/2rtk.db

[logging]
log_dir = logs
log_level = INFO

[security]
secret_key = your-secret-key-here   # Must change default key in production
password_hash_rounds = 12
```

### 4. Start Container

#### Basic Start
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

#### Start with Environment Variables
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

## Configuration

### Port Description
- **2101**: NTRIP service port (standard NTRIP port)
- **5757**: Web management interface port

### Volume Description
- `/app/logs`: Log files directory
- `/app/data`: Database and data files directory
- `/app/config`: Configuration files directory

### Environment Variables
- `NTRIP_PORT`: NTRIP service port (default: 2101)
- `WEB_PORT`: Web service port (default: 5757)
- `DEBUG_MODE`: Debug mode (default: false)
- `DATABASE_PATH`: Database path (default: data/2rtk.db)
- `SECRET_KEY`: Application secret key

## Usage

### 1. Access Web Management Interface

Open browser and visit: `http://localhost:5757`

Default administrator account:
- Username: `admin`
- Password: `admin123`

### 2. Add Mount Points

In the web interface:
1. Login to management interface
2. Click "Add Mount Point"
3. Fill in mount point information:
   - Mount Point Name: e.g., `RTCM3`
   - Description: Mount point description
   - Format: Select data format

### 3. Connect NTRIP Client

Use NTRIP client to connect:
- Server: `your-server-ip`
- Port: `2101`
- Mount Point: Your created mount point name
- Username/Password: Set in management interface

### 4. View Logs

```bash
# View container logs
docker logs ntrip-caster

# Real-time log viewing
docker logs -f ntrip-caster

# View application log files
tail -f data/logs/main.log
```

## Common Issues

### Q1: Container Start Failure - Permission Error

**Problem Description:**
If you encounter `PermissionError: [Errno 13] Permission denied: '/app/logs/main.log'` error, this is caused by Docker volume permission issues.

**Solution:**
```bash
# 1. Stop and remove existing container
docker-compose down
docker rm ntrip-caster

# 2. Remove existing data volumes (Note: This will clear all data)
docker volume rm ntripcaster_ntrip-logs ntripcaster_ntrip-data ntripcaster_ntrip-config

# 3. Rebuild image (if using latest version)
docker-compose build --no-cache

# 4. Restart services
docker-compose up -d
```

**Other startup issue checks:**
```bash
# Check container status
docker ps -a

# View error logs
docker logs ntrip-caster

# Check port usage
netstat -tlnp | grep :2101
netstat -tlnp | grep :5757
```

### Q2: Cannot Access Web Interface

**Check items:**
1. Confirm container is running: `docker ps`
2. Confirm port mapping is correct: `docker port ntrip-caster`
3. Check firewall settings
4. Confirm port settings in configuration file

### Q3: NTRIP Client Cannot Connect

**Check items:**
1. Confirm NTRIP port 2101 is open
2. Check if mount point is correctly created
3. Verify username and password
4. View server logs

### Q4: Data Persistence Issues

**Solution:**
```bash
# Ensure data volumes are correctly mounted
docker inspect ntrip-caster | grep Mounts -A 20

# Check directory permissions
ls -la data/
sudo chown -R 1000:1000 data/
```

## Advanced Configuration

### 1. Using Docker Compose

Create `docker-compose.yml` file:

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

  # Optional: Add Nginx reverse proxy
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

Start services:
```bash
docker-compose up -d
```

### 2. Production Environment Deployment

#### Using SSL/TLS

1. Prepare SSL certificates
2. Configure Nginx reverse proxy
3. Update firewall rules

#### Monitoring and Logging

```bash
# Set log rotation
docker run -d \
  --name ntrip-caster \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  # ... other parameters
```

### 3. Backup and Recovery

#### Backup Data
```bash
# Backup data directory
tar -czf ntrip-backup-$(date +%Y%m%d).tar.gz data/

# Backup database
docker exec ntrip-caster sqlite3 /app/data/2rtk.db ".backup /app/data/backup.db"
```

#### Restore Data
```bash
# Stop container
docker stop ntrip-caster

# Restore data
tar -xzf ntrip-backup-20231201.tar.gz

# Restart container
docker start ntrip-caster
```

### 4. Performance Optimization

#### Resource Limits
```bash
docker run -d \
  --name ntrip-caster \
  --memory=512m \
  --cpus=1.0 \
  # ... other parameters
```

#### Network Optimization
```bash
# Create custom network
docker network create ntrip-network

# Use custom network
docker run -d \
  --name ntrip-caster \
  --network ntrip-network \
  # ... other parameters
```

## Updates and Maintenance

### Update to New Version

```bash
# 1. Backup data
tar -czf backup-$(date +%Y%m%d).tar.gz data/

# 2. Stop and remove old container
docker stop ntrip-caster
docker rm ntrip-caster

# 3. Pull new image
docker pull 2rtk/ntripcaster:latest

# 4. Start new container
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

### Health Check

```bash
# Check container health status
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Manually execute health check
docker exec ntrip-caster python /app/healthcheck.py
```

## Technical Support

If you encounter problems during use, you can:

1. View project documentation: [GitHub Repository](https://github.com/Rampump/NTRIPcaster)
2. Submit Issue: [GitHub Issues](https://github.com/Rampump/NTRIPcaster/issues)
3. Contact author: i@jia.by
4. Visit official website: https://2rtk.com

---

**Version Information:** NTRIP Caster v2.2.0  
**Update Time:** August 2025  
**Author:** i@jia.by