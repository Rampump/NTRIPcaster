# 2RTK NTRIP Caster v2.2.0

**Language / 语言选择:**
- [English](#) (Current)
- [中文版](README-zh.md)

---

This is a simple NTRIP caster written in Python that supports NTRIP v1.0 and v2.0 protocols, managed through a web interface.
You can use the web interface to add users and mount points in your browser, and also view the NTRIP caster connection information.
It supports high concurrent connections and can handle 2000+ concurrent connections. (Due to my limited testing environment, I only tested with 2 RTCM data sources and 2000 concurrent user downloads, but its performance is excellent)

It used to be a single file 2rtk.py,https://github.com/Rampump/2RTKcaster   . but I recently refactored it. Now it looks much fatter, but I still think it's relatively lightweight. lol

- Web-based management. So you can deploy it on any cloud host.
- Uses SQLite database to store user information and mount point information.
- Leverages the pyrtcm library to parse uploaded data and correct STR tables (this part of the code has been rewritten several times, but I'm still not satisfied with it, including the current version. Looking forward to future updates).
    (You just need to add a mount point on the web page and upload RTCM data, 2rtk-NtripCaster will automatically generate STR information and parse RTCM data to extract data types and location information to correct the STR)
- This NTRIP caster is still far from the caster in my mind. I will gradually improve it in my spare time.
- It supports most systems with Python environment, including debian, ubuntu, centos, armbian, etc.
- Supports Docker deployment.
- Bilingual web UI: 中文 / English, switchable in the header (the choice is remembered; `?lang=zh|en` forces it). Server-side messages are localized too.
- "Support the author" entry in the header/footer shows the Alipay and WeChat donation QR codes.

## Installation Tutorials

### Source Installation (tar.gz, simplest)
No Docker required, works on any Linux/macOS/Windows with Python 3.8+.

```bash
# 1. Unpack
tar -xzf ntripcaster-full.tar.gz
cd ntripcaster

# 2. Create the config file from the example (then edit the admin password)
cp config.ini.example config.ini

# 3. Install dependencies (a virtualenv is recommended)
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt

# 4. Run
python3 main.py
```

- The web UI is at `http://yourserverip:5757` (default account `admin` / `admin123` — change it right away).
- On first run, an empty `secret_key` auto-generates into `data/.secret_key`.
- **Detailed native install guide**: [Linux Native Installation Guide](INSTALL-TUTORIAL-EN.md)

### One-click Installer (Debian 10+ / Ubuntu 20.04+, installs systemd service + auto-start)

The script auto-detects the network (tries GitCode first, falls back to GitHub; can also be forced via `REPO_SOURCE`) and auto-switches to a mainland China PyPI mirror for faster dependency installs.

> Run as a **normal user** with `sudo bash install.sh`; if already logged in as **root**, just run `bash install.sh` (no sudo needed).

**International users** (clone from GitHub):

```bash
git clone https://github.com/Rampump/NTRIPcaster.git && cd NTRIPcaster && sudo bash install.sh
```

**Users in mainland China** (clone from GitCode):

```bash
git clone https://gitcode.com/rampump/NTRIPcaster.git && cd NTRIPcaster && sudo bash install.sh
```

> The script auto-detects local sources and skips duplicate downloads. If you hit network issues, force a source with `REPO_SOURCE=gitcode` or `REPO_SOURCE=github`.

### Docker Deployment (Recommended)
Recommended one-click deployment, no manual configuration required.

**International users** (Docker Hub):

```bash
docker run -d --name ntrip-caster -p 2101:2101 -p 5757:5757 2rtk/ntripcaster:latest
```

**Users in mainland China** (Tencent Cloud, no login required):

```bash
docker run -d --name ntrip-caster -p 2101:2101 -p 5757:5757 ccr.ccs.tencentyun.com/2rtk/ntripcaster:latest
```
- **中文教程**: [Docker 安装和使用教程](DOCKER-TUTORIAL.md)
- **English Tutorial**: [Docker Installation and Usage Guide](DOCKER-TUTORIAL-EN.md)

### Debian System Native Installation
- **中文教程**: [Linux 系统原生安装教程](INSTALL-TUTORIAL.md)
- **English Tutorial**: [Linux Native Installation Guide](INSTALL-TUTORIAL-EN.md)

**Access URLs**:
- Web Management Interface: `http://yourserverip:5757`
- NTRIP Service: `ntrip://yourserverip:2101`
- Default Account: `admin` / `admin123` (Remember to change the default password)

## Hardware Recommendations

### Minimum Configuration Requirements
- **CPU**: 2 cores (x86_64 architecture recommended)
- **Memory**: 2GB RAM
- **Storage**: 10GB available disk space
- **Network**: Stable network connection
- **Operating System**: Ubuntu 18.04+ / Debian 10+ / CentOS 7+

## Frontend Web Interface Features
### Homepage
You can see the current caster's running status on the homepage, including connection count, user count, mount point count, etc. The log information below will push user or mount point connection status in real-time. DEBUG mode will push more debugging information.

![Homepage](img/Home.png)

### User Management Page
You can add users, delete users, modify user passwords, etc. on the user management page. You can also see online users. (User management will be added later, API is reserved)

![User Management](img/user.png)

### Mount Point Management Page
You can add mount points, delete mount points, modify mount point information, etc. on the mount point management page. You can also see online information. (Mount point management will be added later, API is reserved)

![Mount Point Management](img/mount.png)

### Base Station Information Page
You can view RTCM status on the base station information page. Click the INFO button in front of the STR entry, and the backend will parse it and display it in the information below. (This usually takes some time to parse before updating the display)

![Base Station Information](img/rtcm.png)

### Configuration Recommendations for Different Loads

| Concurrent Connections | CPU | Memory | Storage | Network Bandwidth |
|------------------------|-----|--------|---------|------------------|
| **< 100** | 1 core | 1GB | 5GB | 10Mbps |
| **100-500** | 2 cores | 2GB | 10GB | 50Mbps |
| **500-1000** | 4 cores | 4GB | 20GB | 100Mbps |
| **1000-2000** | 8 cores | 8GB | 50GB | 200Mbps |
| **2000+** | 16+ cores | 16GB+ | 100GB+ | 500Mbps+ |

### Cloud Server Recommendations
For cloud server deployment, please open ports 5757 and 2101 in security settings
#### AWS EC2
- **Entry Level**: t3.small (2 cores 2GB)
- **Standard**: c5.large (2 cores 4GB)
- **High Performance**: c5.2xlarge (8 cores 16GB)

## Performance Benchmark Tests

- **500 Connection Test**: CPU 18.1%, Memory 29.5%, Network 7.47 Mbps
- **1000 Connection Test**: CPU 19.1%, Memory 33.9%, Network 10.79 Mbps
- **2000 Connection Limit Test**: CPU 17.3%, Memory 30.3%, Network 7.69 Mbps

> For detailed test reports, please check the [tests/](tests/) directory

## Configuration Guide

### Main Configuration Options

```ini
[ntrip]
port = 2101                        # NTRIP service port
download_enforce_user_binding = false  # Require mount<->user binding for downloads

[web]
port = 5757                        # Web management port
realtime_push_interval = 3         # Realtime push interval (seconds)

[database]
path = 2rtk.db                     # SQLite path; use /app/data/2rtk.db inside Docker
pool_size = 10                     # Connection pool size

[data_forwarding]
ring_buffer_size = 60              # Ring buffer window (seconds)
ring_buffer_max_chunks = 3000      # Max cached chunks per mount
sender_threads = 8                 # Sender thread count
client_send_queue_size = 200       # Per-client outbox limit (chunks)
client_pending_max_bytes = 1048576 # Per-client backlog byte limit

[performance]
thread_pool_size = 5000            # Concurrent connection thread pool size

[security]
secret_key =                       # Leave empty to auto-generate into data/.secret_key
login_max_attempts = 5             # Failed login limit before lockout
login_lockout_seconds = 300        # Lockout duration
```

```ini
[admin]
username = admin               # Administrator username
password = admin123            # Administrator password (change in production)
```

### Security Notes

- **Only HTTP Basic authentication is supported; Digest has been removed entirely.**
  NTRIP 1.0 authenticates with the mount password, NTRIP 2.0 with user credentials.
  The previous Digest implementation never worked (nonces were never validated, and
  only PBKDF2 hashes are stored, so no HA1 can be derived), and fixing it would have
  opened a replay window. Digest requests now get a 401 that advertises Basic only,
  so clients must be reconfigured.
- **Mount and user passwords are stored as PBKDF2 hashes**; the admin UI can reset but
  never reveal them, and the API returns a mask. Legacy plaintext rows are migrated on
  the next password change.
- **`/classic` has been removed** and now 302-redirects to the SPA dashboard.
- **`config.ini` is no longer tracked** (it contains the session key and admin
  password). Run `cp config.ini.example config.ini` on first deploy.
- Behind a reverse proxy set `[security] trust_proxy_headers = true`; with HTTPS also
  enable `session_cookie_secure`.

### Common Issue Diagnosis

#### Port Occupation Issues
```bash
# Check port occupation
sudo ss -tlnp | grep :2101    # NTRIP port
sudo ss -tlnp | grep :5757    # Web management port
sudo lsof -i :2101                 # Check port usage

# Release port
sudo kill -9 <PID>                 # Force terminate process
sudo fuser -k 2101/tcp             # Force release port
```

#### Network Connection Issues
```bash
# Firewall check
sudo ufw status                    # Ubuntu firewall
sudo firewall-cmd --list-all       # CentOS firewall

# Open ports
sudo ufw allow 2101/tcp            # NTRIP port
sudo ufw allow 5757/tcp            # Web management port

# Network connectivity test
telnet localhost 2101              # Test NTRIP port
curl http://localhost:5757/        # Test Web port
```

### Performance Optimization

#### High Concurrency Configuration
```ini
# config.ini optimization configuration
[network]
max_connections = 10000            # Maximum connections

[ntrip]
port = 2101

[performance]
thread_pool_size = 10000           # Thread pool size
max_workers = 10000                # Maximum worker threads

[data_forwarding]
ring_buffer_size = 60              # Ring buffer
```

#### System-level Optimization
```bash
# Increase file descriptor limit
echo "* soft nofile 65536" >> /etc/security/limits.conf
echo "* hard nofile 65536" >> /etc/security/limits.conf

# Network parameter optimization
echo "net.core.somaxconn = 65536" >> /etc/sysctl.conf
echo "net.ipv4.tcp_max_syn_backlog = 65536" >> /etc/sysctl.conf
sudo sysctl -p
```

### Monitoring and Logging

#### Log Level Configuration
```ini
# config.ini log configuration
[logging]
log_level = WARNING                 # DEBUG, INFO, WARNING, ERROR
log_format = %%(asctime)s - %%(name)s - %%(levelname)s - %%(message)s
max_log_size = 10485760             # Log rotation size (bytes)
backup_count = 5                    # Number of log files to keep
```
## Contributing

- Welcome to submit Pull Requests
- Contact: i@jia.by
- 2rtk.com


## Acknowledgments and Open Source Libraries

This project uses the following excellent open source libraries and tools, and we express our sincere gratitude:

### Core Dependencies

| Library | Version | Purpose | License |
|---------|---------|---------|----------|
| **Flask** | 3.0.3 | Web framework, providing HTTP services and APIs | BSD-3-Clause |
| **Flask-SocketIO** | 5.3.6 | WebSocket real-time communication support | MIT |
| **python-socketio** | 5.8.0 | Socket.IO protocol implementation | MIT |
| **psutil** | 5.9.5 | System performance monitoring and resource statistics | BSD-3-Clause |
| **pyrtcm** | 1.1.8 | RTCM 3.x message parsing (MSM support) | BSD-3-Clause |
| **pyproj** | 3.7.2 | Geographic coordinate system conversion and projection calculation | MIT |
| **reverse-geocode** | 1.6.6 | Offline reverse geocoding (STR location info) | MIT |

### RTCM Parsing Library

**pyrtcm** - Core RTCM message parsing library
- **Source**: Installed via pip from the standard [pyrtcm](https://github.com/semuconsulting/pyrtcm) package
- **Version**: 1.1.8 (pinned for reproducible builds)
- **Author**: semuconsulting
- **License**: BSD-3-Clause
- **Purpose**: Provides complete RTCM 3.x message parsing (MSM4/5/7 for GPS/GLONASS/Galileo/BeiDou)
- **Note**: The dependency is declared in `requirements.txt`; there is no vendored copy in the repo.

## Open Source License

This project is licensed under the [Apache License 2.0](LICENSE).

### Third-party Library Licenses

- **pyrtcm**: BSD-3-Clause License
- **Flask series**: MIT/BSD License
- **psutil**: BSD-3-Clause License
- **pyproj**: MIT License

All integrated third-party libraries maintain their original open source licenses. Please comply with the corresponding license terms when using.

---

** If this project helps you, please give me a Star!**
