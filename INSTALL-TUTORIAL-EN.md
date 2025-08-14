# NTRIP Caster Linux System Installation Tutorial

This tutorial will guide you through installing and deploying NTRIP Caster v2.2.0 directly on Linux systems, supporting mainstream distributions like Debian, Ubuntu, CentOS, RHEL, and more.

## Table of Contents
- [System Requirements](#system-requirements)
- [Quick Installation](#quick-installation)
- [Detailed Installation Steps](#detailed-installation-steps)
- [Configuration](#configuration)
- [Service Management](#service-management)
- [Common Issues](#common-issues)
- [Performance Optimization](#performance-optimization)
- [Security Configuration](#security-configuration)

## System Requirements

### Minimum Requirements
- **Operating System**: Linux (Debian 10+, Ubuntu 18.04+, CentOS 7+, RHEL 7+)
- **CPU**: 1 core
- **Memory**: 512MB RAM
- **Storage**: 1GB available space
- **Python**: 3.8+ (recommended 3.11)

### Recommended Configuration
- **CPU**: 2+ cores
- **Memory**: 2GB+ RAM
- **Storage**: 10GB+ available space
- **Network**: Stable network connection

### Supported Distributions
- Debian 10/11/12 (Buster/Bullseye/Bookworm)
- Ubuntu 18.04/20.04/22.04/24.04 LTS
- CentOS 7/8/9
- RHEL 7/8/9
- Rocky Linux 8/9
- AlmaLinux 8/9
- openSUSE Leap 15.x
- Fedora 35+

## Quick Installation

### Manual Quick Installation

```bash
# 1. Clone project
git clone https://github.com/Rampump/NTRIPcaster.git
cd NTRIPcaster

# 2. Run installation script
sudo chmod +x install.sh
sudo ./install.sh

# 3. Start service
sudo systemctl start ntripcaster
sudo systemctl enable ntripcaster
```

## Detailed Installation Steps

### 1. System Preparation

#### Debian/Ubuntu Systems

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install required system packages
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    git \
    wget \
    curl \
    sqlite3 \
    build-essential \
    python3-dev \
    libssl-dev \
    libffi-dev \
    supervisor \
    nginx

# Install systemd development packages (if needed)
sudo apt install -y libsystemd-dev
```

#### CentOS/RHEL Systems

```bash
# CentOS 7
sudo yum update -y
sudo yum install -y epel-release
sudo yum install -y \
    python3 \
    python3-pip \
    git \
    wget \
    curl \
    sqlite \
    gcc \
    python3-devel \
    openssl-devel \
    libffi-devel \
    supervisor \
    nginx

# CentOS 8/9 or RHEL 8/9
sudo dnf update -y
sudo dnf install -y epel-release
sudo dnf install -y \
    python3 \
    python3-pip \
    git \
    wget \
    curl \
    sqlite \
    gcc \
    python3-devel \
    openssl-devel \
    libffi-devel \
    supervisor \
    nginx
```

#### openSUSE Systems

```bash
# Update system
sudo zypper refresh && sudo zypper update -y

# Install required packages
sudo zypper install -y \
    python3 \
    python3-pip \
    git \
    wget \
    curl \
    sqlite3 \
    gcc \
    python3-devel \
    libopenssl-devel \
    libffi-devel \
    supervisor \
    nginx
```

### 2. Create System User

```bash
# Create dedicated user
sudo useradd -r -s /bin/false -d /opt/ntripcaster ntripcaster

# Create application directories
sudo mkdir -p /opt/ntripcaster
sudo mkdir -p /var/log/ntripcaster
sudo mkdir -p /etc/ntripcaster

# Set directory permissions
sudo chown -R ntripcaster:ntripcaster /opt/ntripcaster
sudo chown -R ntripcaster:ntripcaster /var/log/ntripcaster
sudo chown -R ntripcaster:ntripcaster /etc/ntripcaster
```

### 3. Download and Install Application

```bash
# Switch to application directory
cd /opt/ntripcaster

# Download source code
sudo -u ntripcaster git clone https://github.com/Rampump/NTRIPcaster.git .

# Create Python virtual environment
sudo -u ntripcaster python3 -m venv venv

# Activate virtual environment and install dependencies
sudo -u ntripcaster bash -c '
    source venv/bin/activate
    pip install --upgrade pip setuptools wheel
    pip install -r requirements.txt
'
```

### 4. Configure Application

```bash
# Copy configuration file
sudo -u ntripcaster cp config.ini.example /etc/ntripcaster/config.ini

# Edit configuration file
sudo nano /etc/ntripcaster/config.ini
```

#### Main Configuration Items:

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
path = /opt/ntripcaster/data/2rtk.db

[logging]
log_dir = /var/log/ntripcaster
log_level = INFO
log_file = main.log
log_format = %(asctime)s - %(name)s - %(levelname)s - %(message)s
log_max_size = 10485760
log_backup_count = 5

[security]
secret_key = $(openssl rand -hex 32)
password_hash_rounds = 12
session_timeout = 3600
```

### 5. Generate Keys and Initialize Database

```bash
# Generate security key
SECRET_KEY=$(openssl rand -hex 32)
sudo sed -i "s/your-secret-key-here/$SECRET_KEY/g" /etc/ntripcaster/config.ini

# Create data directory
sudo -u ntripcaster mkdir -p /opt/ntripcaster/data

# Initialize database (if application supports it)
sudo -u ntripcaster bash -c '
    cd /opt/ntripcaster
    source venv/bin/activate
    python -c "from src.database import init_db; init_db()"
'
```

### 6. Create systemd Service

```bash
# Create service file
sudo tee /etc/systemd/system/ntripcaster.service > /dev/null <<EOF
[Unit]
Description=NTRIP Caster Service
After=network.target
Wants=network.target

[Service]
Type=simple
User=ntripcaster
Group=ntripcaster
WorkingDirectory=/opt/ntripcaster
Environment=NTRIP_CONFIG_FILE=/etc/ntripcaster/config.ini
Environment=PYTHONPATH=/opt/ntripcaster
ExecStart=/opt/ntripcaster/venv/bin/python /opt/ntripcaster/main.py
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=30

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/ntripcaster/data /var/log/ntripcaster /etc/ntripcaster

# Resource limits
LimitNOFILE=65536
LimitNPROC=4096

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd configuration
sudo systemctl daemon-reload
```

### 7. Configure Firewall

#### UFW (Ubuntu/Debian)

```bash
# Enable UFW
sudo ufw enable

# Open required ports
sudo ufw allow 2101/tcp comment 'NTRIP Service'
sudo ufw allow 5757/tcp comment 'NTRIP Web Interface'
sudo ufw allow ssh

# Check status
sudo ufw status
```

#### firewalld (CentOS/RHEL)

```bash
# Start firewalld
sudo systemctl start firewalld
sudo systemctl enable firewalld

# Open ports
sudo firewall-cmd --permanent --add-port=2101/tcp
sudo firewall-cmd --permanent --add-port=5757/tcp
sudo firewall-cmd --reload

# Check status
sudo firewall-cmd --list-all
```

#### iptables (Generic)

```bash
# Open ports
sudo iptables -A INPUT -p tcp --dport 2101 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 5757 -j ACCEPT

# Save rules (Debian/Ubuntu)
sudo iptables-save > /etc/iptables/rules.v4

# Save rules (CentOS/RHEL)
sudo service iptables save
```

## Configuration

### Environment Variables

You can override configuration file settings using environment variables:

```bash
# Add to /etc/systemd/system/ntripcaster.service
Environment=NTRIP_PORT=2101
Environment=WEB_PORT=5757
Environment=DEBUG_MODE=false
Environment=DATABASE_PATH=/opt/ntripcaster/data/2rtk.db
Environment=LOG_LEVEL=INFO
```

### Log Configuration

```bash
# Configure logrotate
sudo tee /etc/logrotate.d/ntripcaster > /dev/null <<EOF
/var/log/ntripcaster/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 ntripcaster ntripcaster
    postrotate
        systemctl reload ntripcaster
    endscript
}
EOF
```

## Service Management

### Basic Operations

```bash
# Start service
sudo systemctl start ntripcaster

# Stop service
sudo systemctl stop ntripcaster

# Restart service
sudo systemctl restart ntripcaster

# Reload configuration
sudo systemctl reload ntripcaster

# Check service status
sudo systemctl status ntripcaster

# Enable auto-start on boot
sudo systemctl enable ntripcaster

# Disable auto-start on boot
sudo systemctl disable ntripcaster
```

### Log Viewing

```bash
# View service logs
sudo journalctl -u ntripcaster

# Follow logs in real-time
sudo journalctl -u ntripcaster -f

# View application logs
sudo tail -f /var/log/ntripcaster/main.log

# View error logs
sudo grep ERROR /var/log/ntripcaster/main.log
```

### Service Monitoring

```bash
# Check if service is running
sudo systemctl is-active ntripcaster

# Check port listening
sudo netstat -tlnp | grep -E ':(2101|5757)'
# Or use ss
sudo ss -tlnp | grep -E ':(2101|5757)'

# Check processes
sudo ps aux | grep ntripcaster
```

## Common Issues

### Q1: Service fails to start

**Troubleshooting steps:**

```bash
# View detailed error information
sudo journalctl -u ntripcaster -n 50

# Check configuration file syntax
sudo -u ntripcaster bash -c '
    cd /opt/ntripcaster
    source venv/bin/activate
    python -c "import configparser; c=configparser.ConfigParser(); c.read("/etc/ntripcaster/config.ini")"
'

# Check file permissions
sudo ls -la /opt/ntripcaster/
sudo ls -la /etc/ntripcaster/
sudo ls -la /var/log/ntripcaster/

# Test manual startup
sudo -u ntripcaster bash -c '
    cd /opt/ntripcaster
    source venv/bin/activate
    NTRIP_CONFIG_FILE=/etc/ntripcaster/config.ini python main.py
'
```

### Q2: Port already in use

```bash
# Check port usage
sudo lsof -i :2101
sudo lsof -i :5757

# Kill occupying process
sudo kill -9 <PID>

# Or modify ports in configuration file
sudo nano /etc/ntripcaster/config.ini
```

### Q3: Permission issues

```bash
# Reset permissions
sudo chown -R ntripcaster:ntripcaster /opt/ntripcaster
sudo chown -R ntripcaster:ntripcaster /var/log/ntripcaster
sudo chown -R ntripcaster:ntripcaster /etc/ntripcaster

# Check SELinux (CentOS/RHEL)
sudo getenforce
sudo setsebool -P httpd_can_network_connect 1
```

### Q4: Python dependency issues

```bash
# Reinstall dependencies
sudo -u ntripcaster bash -c '
    cd /opt/ntripcaster
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt --force-reinstall
'

# Check Python version
python3 --version

# If Python version is too old, install newer version
# Ubuntu/Debian
sudo apt install software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.11 python3.11-venv python3.11-dev
```

### Q5: Database issues

```bash
# Check database file
sudo ls -la /opt/ntripcaster/data/

# Reinitialize database
sudo -u ntripcaster bash -c '
    cd /opt/ntripcaster
    source venv/bin/activate
    rm -f data/2rtk.db
    python -c "from src.database import init_db; init_db()"
'

# Check database integrity
sudo -u ntripcaster sqlite3 /opt/ntripcaster/data/2rtk.db "PRAGMA integrity_check;"
```

## Performance Optimization

### 1. System Optimization

```bash
# Optimize file descriptor limits
sudo tee -a /etc/security/limits.conf > /dev/null <<EOF
ntripcaster soft nofile 65536
ntripcaster hard nofile 65536
ntripcaster soft nproc 4096
ntripcaster hard nproc 4096
EOF

# Optimize network parameters
sudo tee -a /etc/sysctl.conf > /dev/null <<EOF
# NTRIP Caster network optimization
net.core.somaxconn = 1024
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_max_syn_backlog = 1024
net.ipv4.tcp_keepalive_time = 600
net.ipv4.tcp_keepalive_intvl = 60
net.ipv4.tcp_keepalive_probes = 3
EOF

# Apply system parameters
sudo sysctl -p
```

### 2. Application Optimization

Edit configuration file `/etc/ntripcaster/config.ini`:

```ini
[performance]
# Thread pool size
thread_pool_size = 10

# Maximum worker threads
max_workers = 20

# Connection queue size
connection_queue_size = 100

# Maximum memory usage (MB)
max_memory_usage = 512

# CPU usage warning threshold (%)
cpu_warning_threshold = 80

# Memory usage warning threshold (%)
memory_warning_threshold = 85

[network]
# Maximum connections
max_connections = 1000

# Buffer size
buffer_size = 8192

[tcp]
# TCP Keep-Alive settings
keepalive_enable = true
keepalive_idle = 600
keepalive_interval = 60
keepalive_count = 3

# Socket timeout
socket_timeout = 30
connection_timeout = 10
```

### 3. Monitoring Script

Create monitoring script `/opt/ntripcaster/monitor.sh`:

```bash
#!/bin/bash

# NTRIP Caster monitoring script

LOG_FILE="/var/log/ntripcaster/monitor.log"
PID_FILE="/var/run/ntripcaster.pid"

# Check service status
check_service() {
    if ! systemctl is-active --quiet ntripcaster; then
        echo "$(date): NTRIP Caster service is not running, attempting to restart..." >> $LOG_FILE
        systemctl start ntripcaster
        sleep 5
        if systemctl is-active --quiet ntripcaster; then
            echo "$(date): NTRIP Caster service restarted successfully" >> $LOG_FILE
        else
            echo "$(date): Failed to restart NTRIP Caster service" >> $LOG_FILE
        fi
    fi
}

# Check ports
check_ports() {
    if ! netstat -tlnp | grep -q ":2101"; then
        echo "$(date): NTRIP port 2101 is not listening" >> $LOG_FILE
    fi
    
    if ! netstat -tlnp | grep -q ":5757"; then
        echo "$(date): Web port 5757 is not listening" >> $LOG_FILE
    fi
}

# Check memory usage
check_memory() {
    MEMORY_USAGE=$(ps -o pid,ppid,cmd,%mem --sort=-%mem -C python3 | grep ntripcaster | awk '{print $4}' | head -1)
    if [ ! -z "$MEMORY_USAGE" ] && (( $(echo "$MEMORY_USAGE > 80" | bc -l) )); then
        echo "$(date): High memory usage detected: ${MEMORY_USAGE}%" >> $LOG_FILE
    fi
}

# Execute checks
check_service
check_ports
check_memory

echo "$(date): Monitor check completed" >> $LOG_FILE
```

Set up scheduled task:

```bash
# Set execution permissions
sudo chmod +x /opt/ntripcaster/monitor.sh

# Add to crontab
sudo crontab -e
# Add the following line (check every 5 minutes)
*/5 * * * * /opt/ntripcaster/monitor.sh
```

## Security Configuration

### 1. SSL/TLS Configuration

#### Generate Self-signed Certificate

```bash
# Create certificate directory
sudo mkdir -p /etc/ntripcaster/ssl

# Generate private key
sudo openssl genrsa -out /etc/ntripcaster/ssl/server.key 2048

# Generate certificate signing request
sudo openssl req -new -key /etc/ntripcaster/ssl/server.key -out /etc/ntripcaster/ssl/server.csr

# Generate self-signed certificate
sudo openssl x509 -req -days 365 -in /etc/ntripcaster/ssl/server.csr -signkey /etc/ntripcaster/ssl/server.key -out /etc/ntripcaster/ssl/server.crt

# Set permissions
sudo chown -R ntripcaster:ntripcaster /etc/ntripcaster/ssl
sudo chmod 600 /etc/ntripcaster/ssl/server.key
sudo chmod 644 /etc/ntripcaster/ssl/server.crt
```

#### Configure Nginx Reverse Proxy

```bash
# Create Nginx configuration
sudo tee /etc/nginx/sites-available/ntripcaster > /dev/null <<EOF
server {
    listen 80;
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL configuration
    ssl_certificate /etc/ntripcaster/ssl/server.crt;
    ssl_certificate_key /etc/ntripcaster/ssl/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # HTTP redirect to HTTPS
    if (\$scheme != "https") {
        return 301 https://\$host\$request_uri;
    }

    # Web interface proxy
    location / {
        proxy_pass http://127.0.0.1:5757;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # NTRIP service proxy
    location /ntrip {
        proxy_pass http://127.0.0.1:2101;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

# Enable site
sudo ln -s /etc/nginx/sites-available/ntripcaster /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# Restart Nginx
sudo systemctl restart nginx
```

### 2. Access Control

```bash
# Configure fail2ban
sudo apt install fail2ban  # Debian/Ubuntu
sudo yum install fail2ban  # CentOS/RHEL

# Create fail2ban configuration
sudo tee /etc/fail2ban/jail.d/ntripcaster.conf > /dev/null <<EOF
[ntripcaster]
enabled = true
port = 2101,5757
filter = ntripcaster
logpath = /var/log/ntripcaster/main.log
maxretry = 5
bantime = 3600
findtime = 600
EOF

# Create filter rules
sudo tee /etc/fail2ban/filter.d/ntripcaster.conf > /dev/null <<EOF
[Definition]
failregex = ^.*Authentication failed.*from <HOST>.*$
            ^.*Invalid credentials.*from <HOST>.*$
            ^.*Connection refused.*from <HOST>.*$
ignoreregex =
EOF

# Restart fail2ban
sudo systemctl restart fail2ban
```

### 3. Regular Backup

Create backup script `/opt/ntripcaster/backup.sh`:

```bash
#!/bin/bash

# NTRIP Caster backup script

BACKUP_DIR="/opt/backups/ntripcaster"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="ntripcaster_backup_$DATE.tar.gz"

# Create backup directory
mkdir -p $BACKUP_DIR

# Stop service
systemctl stop ntripcaster

# Create backup
tar -czf $BACKUP_DIR/$BACKUP_FILE \
    /opt/ntripcaster \
    /etc/ntripcaster \
    /var/log/ntripcaster

# Start service
systemctl start ntripcaster

# Delete backups older than 30 days
find $BACKUP_DIR -name "ntripcaster_backup_*.tar.gz" -mtime +30 -delete

echo "Backup completed: $BACKUP_DIR/$BACKUP_FILE"
```

Set up scheduled backup:

```bash
# Set execution permissions
sudo chmod +x /opt/ntripcaster/backup.sh

# Add to crontab (backup daily at 2 AM)
sudo crontab -e
# Add the following line
0 2 * * * /opt/ntripcaster/backup.sh
```

## Uninstallation

To completely uninstall NTRIP Caster:

```bash
# Stop and disable service
sudo systemctl stop ntripcaster
sudo systemctl disable ntripcaster

# Remove service file
sudo rm /etc/systemd/system/ntripcaster.service
sudo systemctl daemon-reload

# Remove application files
sudo rm -rf /opt/ntripcaster
sudo rm -rf /etc/ntripcaster
sudo rm -rf /var/log/ntripcaster

# Remove user
sudo userdel ntripcaster

# Remove Nginx configuration (if configured)
sudo rm /etc/nginx/sites-enabled/ntripcaster
sudo rm /etc/nginx/sites-available/ntripcaster
sudo systemctl restart nginx

# Remove firewall rules
sudo ufw delete allow 2101/tcp
sudo ufw delete allow 5757/tcp
```

## Technical Support

If you encounter issues during installation or usage:

1. **Check Logs**: `sudo journalctl -u ntripcaster -f`
2. **Check Status**: `sudo systemctl status ntripcaster`
3. **View Documentation**: [GitHub Repository](https://github.com/Rampump/NTRIPcaster)
4. **Report Issues**: [GitHub Issues](https://github.com/Rampump/NTRIPcaster/issues)
5. **Contact Author**: i@jia.by
6. **Visit Website**: https://2rtk.com

## Changelog

- **v2.2.0**: Latest version with support for more Linux distributions
- **v2.1.8**: Performance optimizations and security enhancements
- **v2.1.7**: Added monitoring and logging features

---

**Version**: NTRIP Caster v2.2.0  
**Updated**: December 2024  
**Author**: 2RTK Team  
**License**: Apache 2.0