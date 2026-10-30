# NTRIP Caster Linux System Installation Tutorial

This tutorial will guide you through installing and deploying NTRIP Caster v2.3.4 directly on Linux systems, supporting mainstream distributions like Debian, Ubuntu, CentOS, RHEL, and more.

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
- **Operating System**: Linux (Debian 11+, Ubuntu 22.04+, CentOS 8+, RHEL 8+)
- **CPU**: 1 core
- **Memory**: 512MB RAM
- **Storage**: 1GB available space
- **Python**: 3.9+ (recommended 3.11)

> ⚠️ **Ubuntu 20.04 / Debian 10 are not supported**: they ship Python 3.8, while `pyrtcm==1.1.8` requires Python 3.9+.

### Recommended Configuration
- **CPU**: 2+ cores
- **Memory**: 2GB+ RAM
- **Storage**: 10GB+ available space
- **Network**: Stable network connection

### Supported Distributions
- Debian 11/12/13 (Bullseye/Bookworm/Trixie)
- Ubuntu 22.04/24.04 LTS (20.04 and earlier are not supported)
- CentOS 8/9
- RHEL 8/9
- Rocky Linux 8/9
- AlmaLinux 8/9
- openSUSE Leap 15.x
- Fedora 35+

## Quick Installation

### Manual Quick Installation

> **Privilege note**: the script requires root. As a **normal user** run `sudo bash install.sh`; if already logged in as **root** (prompt ends with `#`), just run `bash install.sh`.

**International users** (clone from GitHub):

```bash
git clone https://github.com/Rampump/NTRIPcaster.git
cd NTRIPcaster
sudo bash install.sh
```

**Users in mainland China** (clone from GitCode):

```bash
git clone https://gitcode.com/rampump/NTRIPcaster.git
cd NTRIPcaster
sudo bash install.sh
```

After installation the service auto-starts (systemd service name `2rtk`):

```bash
sudo systemctl start 2rtk
sudo systemctl enable 2rtk
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
    nginx

# Install systemd development packages (if needed)
sudo apt install -y libsystemd-dev
```

#### CentOS/RHEL Systems

> ⚠️ CentOS 7 ships Python 3.6 and cannot install this project (Python 3.9+ required). Use CentOS 8/9 or RHEL 8/9.

```bash
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
    nginx
```

### 2. Create System User

```bash
# Create dedicated user
sudo useradd -r -s /bin/false -d /opt/2rtk 2rtk

# Create application directories
sudo mkdir -p /opt/2rtk
sudo mkdir -p /var/log/2rtk
sudo mkdir -p /etc/2rtk

# Set directory permissions
sudo chown -R 2rtk:2rtk /opt/2rtk
sudo chown -R 2rtk:2rtk /var/log/2rtk
sudo chown -R 2rtk:2rtk /etc/2rtk
```

### 3. Download and Install Application

```bash
# Switch to application directory
cd /opt/2rtk

# Download source code (use GitCode in mainland China, GitHub internationally)
sudo -u 2rtk git clone https://github.com/Rampump/NTRIPcaster.git .
# sudo -u 2rtk git clone https://gitcode.com/rampump/NTRIPcaster.git .

# Create Python virtual environment
sudo -u 2rtk python3 -m venv venv

# Activate virtual environment and install dependencies
sudo -u 2rtk bash -c '
    source venv/bin/activate
    pip install --upgrade pip setuptools wheel
    pip install -r requirements.txt
'
```

### 4. Configure Application

```bash
# Copy configuration file
sudo -u 2rtk cp config.ini.example /etc/2rtk/config.ini

# Edit configuration file
sudo nano /etc/2rtk/config.ini
```

#### Main Configuration Items:

```ini
[app]
name = 2RTK Ntrip Caster
version = 2.3.4
description = Ntrip Caster
author = 2rtk
contact = i@jia.im
website = https://2rtk.com

[network]
host = 0.0.0.0
max_connections = 5000

[ntrip]
port = 2101

[web]
port = 5757

[database]
path = /opt/2rtk/data/2rtk.db

[logging]
log_dir = /var/log/2rtk
log_level = INFO
main_log_file = main.log
log_format = %%(asctime)s - %%(name)s - %%(levelname)s - %%(message)s
max_log_size = 10485760
backup_count = 5

[security]
secret_key = $(openssl rand -hex 32)
password_hash_rounds = 3
session_timeout = 3600
```

### 5. Generate Keys and Initialize Database

```bash
# Generate security key (secret_key auto-generates when left empty; can also set manually)
SECRET_KEY=$(openssl rand -hex 32)
sudo sed -i "s/^secret_key = *$/secret_key = $SECRET_KEY/" /etc/2rtk/config.ini

# Create data directory
sudo -u 2rtk mkdir -p /opt/2rtk/data

# Initialize database (main.py creates tables on first run; no manual init required)
sudo -u 2rtk bash -c '
    cd /opt/2rtk
    source venv/bin/activate
    python3 -c "from src.database import DatabaseManager; DatabaseManager().init_database()"
'
```

### 6. Create systemd Service

```bash
# Create service file
sudo tee /etc/systemd/system/2rtk.service > /dev/null <<EOF
[Unit]
Description=NTRIP Caster Service
After=network.target
Wants=network.target

[Service]
Type=simple
User=2rtk
Group=2rtk
WorkingDirectory=/opt/2rtk
Environment=NTRIP_CONFIG_FILE=/etc/2rtk/config.ini
Environment=PYTHONPATH=/opt/2rtk
ExecStart=/opt/2rtk/venv/bin/python /opt/2rtk/main.py
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
ReadWritePaths=/opt/2rtk/data /var/log/2rtk /etc/2rtk

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
# Add to /etc/systemd/system/2rtk.service
Environment=NTRIP_PORT=2101
Environment=WEB_PORT=5757
Environment=DEBUG=false
Environment=DATABASE_PATH=/opt/2rtk/data/2rtk.db
Environment=LOG_LEVEL=INFO
```

### Log Configuration

```bash
# Configure logrotate
sudo tee /etc/logrotate.d/2rtk > /dev/null <<EOF
/var/log/2rtk/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 2rtk 2rtk
    postrotate
        systemctl reload 2rtk
    endscript
}
EOF
```

## Service Management

### Basic Operations

```bash
# Start service
sudo systemctl start 2rtk

# Stop service
sudo systemctl stop 2rtk

# Restart service
sudo systemctl restart 2rtk

# Reload configuration
sudo systemctl reload 2rtk

# Check service status
sudo systemctl status 2rtk

# Enable auto-start on boot
sudo systemctl enable 2rtk

# Disable auto-start on boot
sudo systemctl disable 2rtk
```

### Log Viewing

```bash
# View service logs
sudo journalctl -u 2rtk

# Follow logs in real-time
sudo journalctl -u 2rtk -f

# View application logs
sudo tail -f /var/log/2rtk/main.log

# View error logs
sudo grep ERROR /var/log/2rtk/main.log
```

### Service Monitoring

```bash
# Check if service is running
sudo systemctl is-active 2rtk

# Check port listening
sudo ss -tlnp | grep -E ':(2101|5757)'

# Check processes
sudo ps aux | grep 2rtk
```

## Common Issues

### Q1: Service fails to start

**Troubleshooting steps:**

```bash
# View detailed error information
sudo journalctl -u 2rtk -n 50

# Check configuration file syntax
sudo -u 2rtk bash -c '
    cd /opt/2rtk
    source venv/bin/activate
    python3 -c "import configparser; c=configparser.ConfigParser(); c.read("/etc/2rtk/config.ini")"
'

# Check file permissions
sudo ls -la /opt/2rtk/
sudo ls -la /etc/2rtk/
sudo ls -la /var/log/2rtk/

# Test manual startup
sudo -u 2rtk bash -c '
    cd /opt/2rtk
    source venv/bin/activate
    NTRIP_CONFIG_FILE=/etc/2rtk/config.ini python3 main.py
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
sudo nano /etc/2rtk/config.ini
```

### Q3: Permission issues

```bash
# Reset permissions
sudo chown -R 2rtk:2rtk /opt/2rtk
sudo chown -R 2rtk:2rtk /var/log/2rtk
sudo chown -R 2rtk:2rtk /etc/2rtk

# Check SELinux (CentOS/RHEL)
sudo getenforce
sudo setsebool -P httpd_can_network_connect 1
```

### Q4: Python dependency issues

```bash
# Reinstall dependencies
sudo -u 2rtk bash -c '
    cd /opt/2rtk
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
sudo ls -la /opt/2rtk/data/

# Reinitialize database (restart service after removing; main.py auto-creates tables)
sudo -u 2rtk bash -c '
    cd /opt/2rtk
    source venv/bin/activate
    rm -f data/2rtk.db
'
# Restart service, main.py will recreate tables automatically
sudo systemctl restart 2rtk

# Check database integrity
sudo -u 2rtk sqlite3 /opt/2rtk/data/2rtk.db "PRAGMA integrity_check;"
```

## Performance Optimization

### 1. System Optimization

```bash
# Optimize file descriptor limits
sudo tee -a /etc/security/limits.conf > /dev/null <<EOF
2rtk soft nofile 65536
2rtk hard nofile 65536
2rtk soft nproc 4096
2rtk hard nproc 4096
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

Edit configuration file `/etc/2rtk/config.ini`:

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
keepalive_enabled = true
keepalive_idle = 600
keepalive_interval = 60
keepalive_count = 3

# Socket timeout
socket_timeout = 30
```

### 3. Monitoring Script

Create monitoring script `/opt/2rtk/monitor.sh`:

```bash
#!/bin/bash

# NTRIP Caster monitoring script

LOG_FILE="/var/log/2rtk/monitor.log"
PID_FILE="/var/run/2rtk.pid"

# Check service status
check_service() {
    if ! systemctl is-active --quiet 2rtk; then
        echo "$(date): NTRIP Caster service is not running, attempting to restart..." >> $LOG_FILE
        systemctl start 2rtk
        sleep 5
        if systemctl is-active --quiet 2rtk; then
            echo "$(date): NTRIP Caster service restarted successfully" >> $LOG_FILE
        else
            echo "$(date): Failed to restart NTRIP Caster service" >> $LOG_FILE
        fi
    fi
}

# Check ports
check_ports() {
    if ! ss -tlnp | grep -q ":2101"; then
        echo "$(date): NTRIP port 2101 is not listening" >> $LOG_FILE
    fi
    
    if ! ss -tlnp | grep -q ":5757"; then
        echo "$(date): Web port 5757 is not listening" >> $LOG_FILE
    fi
}

# Check memory usage
check_memory() {
    MEMORY_USAGE=$(ps -o pid,ppid,cmd,%mem --sort=-%mem -C python3 | grep 2rtk | awk '{print $4}' | head -1)
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
sudo chmod +x /opt/2rtk/monitor.sh

# Add to crontab
sudo crontab -e
# Add the following line (check every 5 minutes)
*/5 * * * * /opt/2rtk/monitor.sh
```

## Security Configuration

### 1. SSL/TLS Configuration

#### Generate Self-signed Certificate

```bash
# Create certificate directory
sudo mkdir -p /etc/2rtk/ssl

# Generate private key
sudo openssl genrsa -out /etc/2rtk/ssl/server.key 2048

# Generate certificate signing request
sudo openssl req -new -key /etc/2rtk/ssl/server.key -out /etc/2rtk/ssl/server.csr

# Generate self-signed certificate
sudo openssl x509 -req -days 365 -in /etc/2rtk/ssl/server.csr -signkey /etc/2rtk/ssl/server.key -out /etc/2rtk/ssl/server.crt

# Set permissions
sudo chown -R 2rtk:2rtk /etc/2rtk/ssl
sudo chmod 600 /etc/2rtk/ssl/server.key
sudo chmod 644 /etc/2rtk/ssl/server.crt
```

#### Configure Nginx Reverse Proxy

```bash
# Create Nginx configuration
sudo tee /etc/nginx/sites-available/2rtk > /dev/null <<EOF
server {
    listen 80;
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL configuration
    ssl_certificate /etc/2rtk/ssl/server.crt;
    ssl_certificate_key /etc/2rtk/ssl/server.key;
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
sudo ln -s /etc/nginx/sites-available/2rtk /etc/nginx/sites-enabled/

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
sudo tee /etc/fail2ban/jail.d/2rtk.conf > /dev/null <<EOF
[2rtk]
enabled = true
port = 2101,5757
filter = 2rtk
logpath = /var/log/2rtk/main.log
maxretry = 5
bantime = 3600
findtime = 600
EOF

# Create filter rules
sudo tee /etc/fail2ban/filter.d/2rtk.conf > /dev/null <<EOF
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

Create backup script `/opt/2rtk/backup.sh`:

```bash
#!/bin/bash

# NTRIP Caster backup script

BACKUP_DIR="/opt/backups/2rtk"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="2rtk_backup_$DATE.tar.gz"

# Create backup directory
mkdir -p $BACKUP_DIR

# Stop service
systemctl stop 2rtk

# Create backup
tar -czf $BACKUP_DIR/$BACKUP_FILE \
    /opt/2rtk \
    /etc/2rtk \
    /var/log/2rtk

# Start service
systemctl start 2rtk

# Delete backups older than 30 days
find $BACKUP_DIR -name "2rtk_backup_*.tar.gz" -mtime +30 -delete

echo "Backup completed: $BACKUP_DIR/$BACKUP_FILE"
```

Set up scheduled backup:

```bash
# Set execution permissions
sudo chmod +x /opt/2rtk/backup.sh

# Add to crontab (backup daily at 2 AM)
sudo crontab -e
# Add the following line
0 2 * * * /opt/2rtk/backup.sh
```

## Uninstallation

To completely uninstall NTRIP Caster:

```bash
# Stop and disable service
sudo systemctl stop 2rtk
sudo systemctl disable 2rtk

# Remove service file
sudo rm /etc/systemd/system/2rtk.service
sudo systemctl daemon-reload

# Remove application files
sudo rm -rf /opt/2rtk
sudo rm -rf /etc/2rtk
sudo rm -rf /var/log/2rtk

# Remove user
sudo userdel 2rtk

# Remove Nginx configuration (if configured)
sudo rm /etc/nginx/sites-enabled/2rtk
sudo rm /etc/nginx/sites-available/2rtk
sudo systemctl restart nginx

# Remove firewall rules
sudo ufw delete allow 2101/tcp
sudo ufw delete allow 5757/tcp
```

## Technical Support

If you encounter issues during installation or usage:

1. **Check Logs**: `sudo journalctl -u 2rtk -f`
2. **Check Status**: `sudo systemctl status 2rtk`
3. **View Documentation**: [GitHub Repository](https://github.com/Rampump/NTRIPcaster)
4. **Report Issues**: [GitHub Issues](https://github.com/Rampump/NTRIPcaster/issues)
5. **Contact Author**: i@jia.im
6. **Visit Website**: https://2rtk.com

## Changelog

- **v2.3.4**: Fixed the base station marker not rendering on the map — the icon was inlined via btoa, and the Chinese comments inside the SVG raised InvalidCharacterError, aborting the whole draw (the map panned but no icon, label or coverage circles appeared); the icon is now a same-origin static file with a dot fallback; coverage circles moved below the icon, their radius corrected for Web Mercator latitude scale, and given dashed outlines
- **v2.3.3**: Fixed STR table button highlight drifting out of sync with the backend — the highlight now follows the backend's current_web_mount, and leaving the monitor page stops parsing and restores the button colour
- **v2.3.2**: Fixed a false "port already in use" error on restart — the port check now sets SO_REUSEADDR so TIME_WAIT connections are no longer mistaken for an occupied port; the update script reports TCP state and retries startup once TIME_WAIT has drained
- **v2.3.1**: Fixed one-click update script deployment detection false negative (grep -q triggering SIGPIPE under pipefail); STR table buttons now show station names with selection highlight; map marker changed to signal tower icon; health check respects custom web port
- **v2.3.0**: Support for more Linux distributions
- **v2.1.8**: Performance optimizations and security enhancements
- **v2.1.7**: Added monitoring and logging features

---

**Version**: NTRIP Caster v2.3.4  
**Updated**: December 2024  
**Author**: 2RTK Team  
**License**: Apache 2.0