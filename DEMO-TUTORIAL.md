# 演示站部署教程（demo_mode）

本文只讲一件事：**把本程序部署成一个对外公开的试用站**——任何人都能登录、注册账号、添加挂载点、自己推流看解析，但**改不掉、删不掉别人的东西**。

常规生产部署不需要看这份文档。四份文档的分工：

| 文档 | 解决什么 |
|---|---|
| [README-zh.md](README-zh.md) | 功能概览、配置项清单、升级方式 |
| [INSTALL-TUTORIAL.md](INSTALL-TUTORIAL.md) | 一台 Linux 上从零装好（系统准备、依赖、systemd、防火墙） |
| [DOCKER-TUTORIAL.md](DOCKER-TUTORIAL.md) | 容器相关细节（镜像、卷、反代容器） |
| **本文** | 专门针对「公网演示站」：开关怎么开、有哪些配套、怎么验收、哪里会踩坑 |

> 本文不重复系统要求、依赖安装、防火墙基础命令。那些在 INSTALL-TUTORIAL.md 里已经写过了，这里直接引用。

---

## 1. 演示站和生产站差在哪

先看清楚差异，再决定怎么装。演示站不是「加一个开关」，而是**一套独立的部署形态**。

| 项目 | 生产站 | 演示站 |
|---|---|---|
| 实例 | 自己一台机器，独占 | 建议独立机器/独立容器，**不要和正式站共库** |
| 数据库 | 长期数据 | 一次性数据，60 天自动清 |
| 管理员口令 | 上线前必须改 | **必须是默认 `admin123`**（演示模式下改不了） |
| 写操作 | 全开 | 只能新增，改/删/改密/重启全部 403 |
| 平台注册 | 通常关闭 | 默认开放，靠上限和清理兜底 |
| 数据可信度 | 真实数据 | **任何数据都不可信**，别放真实站点坐标和口令 |
| 资源 | 按真实负载 | 解析线程数要压住（默认 4） |

一句话：**演示站的数据库是垃圾桶，随时可以被清理；里面不能有任何你还想要的东西。**

---

## 2. 动手前的三个决定

**① 独立实例，还是与正式站共存？**

- 有第二台机器 / 第二个 VPS → 走 [3.1](#31-路径-a独立机器推荐)，最省事
- 只有一台机器 → 走 [3.2](#32-路径-b同机共存docker)，端口和卷要全部错开
- 只想先看看效果 → 走 [3.3](#33-路径-c本机试跑不对外)

**② 域名和 HTTPS（强烈建议，但不是功能必需）**

演示站面向陌生人，登录态和 `ntrip_cid` 标识都靠 Cookie。有域名就上 HTTPS，Cookie 不会在公网明文传输；**纯 HTTP 一样能跑**，只是口令和 Cookie 全程明文——抓到就能用你的 admin 登录（演示模式下也只能新增，改删仍被 403 挡住）。不开 HTTPS 时务必确认 `session_cookie_secure = false`（默认就是 false），否则浏览器在 HTTP 下不回传 Cookie，表现为「登录成功却立刻跳回登录页」。两种情况的配置见[第 8 节](#8-nginx--https强烈建议)。

**③ 数据库必须是新建的**

演示模式下管理员口令改不了。如果你拿一个「口令已经被改过」的旧库来跑演示站，结果是**谁都进不去，而且没有界面可以救**。全新库最安全，做法见[第 5 节](#5-数据库与-admin-口令)。

---

## 3. 部署

### 3.1 路径 A：独立机器（推荐）

系统要求、依赖、防火墙基础操作见 [INSTALL-TUTORIAL.md 第 1 节](INSTALL-TUTORIAL.md#系统要求)，这里只给演示站要执行的命令。

```bash
# 1. 拉源码并安装（Debian 11+ / Ubuntu 22.04+）
git clone https://gitcode.com/rampump/NTRIPcaster.git
cd NTRIPcaster
sudo bash install.sh
```

安装脚本会把配置放在 `/etc/2rtk/config.ini`，服务名 `2rtk`，数据在 `/opt/2rtk/data/2rtk.db`。

```bash
CFG=/etc/2rtk/config.ini

# 2. 开演示模式（已有该行就改值，没有就在 [security] 段后插入）
if sudo grep -q '^demo_mode' "$CFG"; then
    sudo sed -i 's/^demo_mode *=.*/demo_mode = true/' "$CFG"
else
    sudo sed -i '/^\[security\]/a demo_mode = true' "$CFG"
fi

# 3. 演示站把解析并发压小（同上，该行不存在时插到 [web] 段）
if sudo grep -q '^max_web_parsers' "$CFG"; then
    sudo sed -i 's/^max_web_parsers *=.*/max_web_parsers = 4/' "$CFG"
else
    sudo sed -i '/^\[web\]/a max_web_parsers = 4' "$CFG"
fi

# 4. 重启并验证
sudo systemctl restart 2rtk
curl -s http://127.0.0.1:5757/health
```

`/health` 返回里出现 `"demo_mode": true` 就说明开关生效了。

> 这段 `if/else` 不是啰嗦：**同一个段里出现两行 `demo_mode` 会让程序直接启动失败**（configparser 遇到重复键会抛异常）。所以要么改值、要么插入，不能无脑追加。

> 从老版本升上来的实例，配置里可能连 `demo_mode` / `max_web_parsers` 这些键都没有。
> 不想手工插的话，先跑一次迁移脚本让它自动补齐（只补缺的键，已有值一个都不动）：
>
> ```bash
> sudo /opt/2rtk/venv/bin/python /opt/2rtk/scripts/merge_config.py --apply \
>     --config /etc/2rtk/config.ini --example /opt/2rtk/config.ini.example
> ```
>
> 补完再执行上面的第 3、4 步即可（`grep` 会命中，走的是「改值」分支）。

> 不想碰命令行的，直接用编辑器改 `/etc/2rtk/config.ini` 效果相同，改完记得 `systemctl restart 2rtk`。

### 3.2 路径 B：同机共存（Docker）

正式站已经占了 2101 / 5757 和服务名 `2rtk`，第二个实例必须全部错开。**不要重复跑 `install.sh`**——它把安装目录、配置目录、服务名都写死了，跑第二次会覆盖正式站的配置。

新建一个 `docker-compose.demo.yml`（放在源码目录里，和 `Dockerfile` 同级）：

```yaml
services:
  ntrip-demo:
    build:
      context: .
      dockerfile: Dockerfile
    image: ntripcaster:2.3.6-demo
    container_name: ntrip-caster-demo
    restart: unless-stopped
    ports:
      - "2102:2101"     # 对外 NTRIP（与正式站 2101 错开）
      - "5758:5757"     # 对外 Web（与正式站 5757 错开）
    volumes:
      - ntrip-demo-data:/app/data
      - ntrip-demo-logs:/app/logs
      - ntrip-demo-config:/app/config
      - /etc/localtime:/etc/localtime:ro
    environment:
      - PYTHONUNBUFFERED=1
      - TZ=Asia/Shanghai
      - NTRIP_CONFIG_FILE=/app/config/config.ini
      - DATABASE_PATH=/app/data/2rtk.db
      - LOG_DIR=/app/logs
      - DATA_DIR=/app/data
      - DEMO_MODE=true          # 关键：容器内无需改 config.ini
    networks:
      - ntrip-demo-net
    healthcheck:
      test: ["CMD", "python", "/app/healthcheck.py"]
      interval: 30s
      timeout: 15s
      retries: 3
      start_period: 90s
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"
    security_opt:
      - no-new-privileges:true

networks:
  ntrip-demo-net:
    name: ntrip-demo-net       # 独立网络，不要复用正式站的 ntrip-network

volumes:
  ntrip-demo-data:
    name: ntrip-demo-data      # 三个卷全部改名，否则会挂到正式站的数据上
  ntrip-demo-logs:
    name: ntrip-demo-logs
  ntrip-demo-config:
    name: ntrip-demo-config
```

```bash
docker compose -f docker-compose.demo.yml up -d --build
docker compose -f docker-compose.demo.yml ps
curl -s http://127.0.0.1:5758/health
```

容器首次启动会自动把 `config.ini.example` 复制成 `/app/config/config.ini`，所以**只需要 `DEMO_MODE=true` 这一个环境变量**，不用进容器改文件。卷里已经有旧版 `config.ini` 时，启动脚本会按 `config.ini.example` 把缺的键补进去（同样不覆盖已有值），所以升级镜像不用手动迁移配置。

两个一定要错开的地方（写错就会撞车）：

| 变量 | 正式站 | 演示站 | 撞了会怎样 |
|---|---|---|---|
| 端口 | 2101 / 5757 | 2102 / 5758 | 容器起不来，或外部连到正式站 |
| 卷名 | `ntrip-data` 等 | `ntrip-demo-data` 等 | **两个实例共用一个数据库**，演示站的清理脚本会删掉正式站数据 |
| 容器名 | `ntrip-caster` | `ntrip-caster-demo` | `docker rm -f ntrip-caster` 会误删正式站 |

### 3.3 路径 C：本机试跑（不对外）

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config.ini.example config.ini

# 只改一处：config.ini 的 [security] 段
#   demo_mode = true

DEMO_MODE=1 python main.py
```

打开 `http://127.0.0.1:5757`，用 `admin / admin123` 登录。这种方式不装服务、不开机自启，改完代码重启一下就是干净的，适合验证界面。

---

## 4. 打开演示模式

**先说清楚默认值**：`config.ini.example` 里写的是 `demo_mode = false`，`install.sh` 只是把它原样复制成 `config.ini`，**不会替你打开**。所以新装、升级、Docker 首次启动出来的实例都是正常的完整管理员模式；要跑演示站，必须自己显式改这一行（或传环境变量）。

一个开关，三种写法，效果完全一样：

| 场景 | 写法 |
|---|---|
| 源码 / 脚本安装（**推荐**） | `/etc/2rtk/config.ini` 的 `[security]` 段加 `demo_mode = true` |
| 临时验证、不想改配置文件 | 启动前加环境变量 `DEMO_MODE=1` |
| Docker | compose 的 `environment` 里 `DEMO_MODE=true`（`.env` 文件写 `DEMO_MODE=true` 也行） |

```ini
[security]
# ... 上面原有内容保持不变 ...
demo_mode = true
```

环境变量优先级更高：只要 `DEMO_MODE` 是 `1/true/yes/on` 之一，配置文件写 `false` 也会被当成开启。

**验证开关真的生效**（这一步别省，很多「演示模式没起作用」其实是没重启服务）：

```bash
curl -s http://127.0.0.1:5757/health
# {"status":"ok","version":"2.3.7","uptime":12.3,"demo_mode":true}
```

如果还是 `false`：

1. 配置文件路径对不对——脚本安装看 `/etc/2rtk/config.ini`，**不是** `/opt/2rtk/config.ini`（那是软链，改它也行但容易改错位置）
2. 服务重启了没：`sudo systemctl restart 2rtk`
3. 环境变量里有没有 `DEMO_MODE=0` 之类的东西把它压掉

---

## 5. 数据库与 admin 口令

演示模式**不会**帮你重置口令（这是刻意的：万一有人拿旧库来跑，重置会悄悄抹掉他的密码）。它只做一件事——启动时试一次 `admin / admin123`，失败就在日志里告警：

```
[WARNING] 演示模式已开启，但管理员 admin 的口令不是默认值 admin123。
          演示模式下无法通过界面修改口令，请确认数据库是否为新建库，
          或临时关闭 demo_mode 改回默认口令。
```

看到这条就先停下来处理，否则站点等于对外关门。

**正确姿势：用全新数据库**

```bash
# 源码安装：先停服务，挪走旧库（不要删，留着）
sudo systemctl stop 2rtk
sudo mv /opt/2rtk/data/2rtk.db /opt/2rtk/data/2rtk.db.bak
sudo systemctl start 2rtk      # 启动时自动建库，写入 admin/admin123
```

数据库文件的位置看 `config.ini` 的 `[database] path`，脚本安装默认是 `/opt/2rtk/data/2rtk.db`。

**已经锁死了怎么救**

演示模式下改密码接口是 403，界面上也没有入口。两条路：

```bash
# 路线一：临时关掉开关改密码，再打开
sudo sed -i 's/^demo_mode *=.*/demo_mode = false/' /etc/2rtk/config.ini
sudo systemctl restart 2rtk
# 浏览器登录 → 设置 → 修改管理员密码 → 改回 admin123
sudo sed -i 's/^demo_mode *=.*/demo_mode = true/' /etc/2rtk/config.ini
sudo systemctl restart 2rtk

# 路线二：删掉 admins 那一行，重启后自动重建默认管理员
sudo systemctl stop 2rtk
sqlite3 /opt/2rtk/data/2rtk.db "DELETE FROM admins;"
sudo systemctl start 2rtk
```

> 演示站上允许新建的「用户」是 NTRIP 挂载点账号（存在 `users` 表），**和后台登录的管理员（`admins` 表）是两回事**。访客新建的账号再多，也进不了后台。

---

## 6. 演示模式到底拦住了什么

规则是 **fail-closed**：除白名单外，所有非只读请求一律拒绝。所以「哪些被拦」比「哪些放行」更值得背——将来新增的写接口，默认就在被拦的那一侧。

**放行的（只有这些）**

| 接口 | 用途 |
|---|---|
| `POST /api/login` | 登录 |
| `POST /api/users` | 新建 NTRIP 账号 |
| `POST /api/mounts` | 新建挂载点 |
| `POST /api/mount/<名>/rtcm-parse/start` | 订阅解析 |
| `POST /api/mount/<名>/rtcm-parse/stop` | 退订解析 |
| `POST /api/mount/rtcm-parse/{stop,heartbeat}` | 退订全部 / 心跳 |
| `POST /api/public/*` | 匿名数据页用（见第 7 节） |
| 所有 `GET / HEAD / OPTIONS` | 查询 |

**全部 403 的（常见几个）**

| 接口 | 原本想做什么 |
|---|---|
| `PUT /api/users/<名>`、`DELETE /api/users/<名>` | 改密码 / 删用户 |
| `PUT /api/mounts/<名>`、`DELETE /api/mounts/<名>` | 改挂载点 / 删挂载点 |
| `PUT /api/admin/password` | 改管理员密码 |
| `POST /api/system/restart` | 重启程序 |
| `POST /api/mount/initialize`、`POST /api/bypass/stop-all`、`POST /api/mount/<名>/simulate` | 初始化 / 停全部解析 / 注入模拟数据 |

被拦时返回统一的 JSON：

```json
{"error": "演示模式：仅允许新增，不支持修改或删除", "code": "demo_readonly"}
```

前端会识别 `code` 并弹「演示模式不允许此操作」，不会误报成「未知错误」。如果你在写脚本调用接口，也请按 `code` 判断，不要匹配中文文案。

**为什么 RTCM 解析放行**：它只操作内存里的查看线程，不写数据库。访客点「停止解析」最多影响他自己那条订阅（v2.3.6 起按挂载点复用线程、客户端各持订阅），不会破坏任何人的数据。

**登录也有限制**：演示模式下只认 `admin`，历史库里残留的其他管理员账号会被拒绝登录，避免「后台有多个管理员、其中一个口令是别人设的」。

---

## 7. 三个配套开关（建议全开）

演示站真正的风险不是「谁登录了后台」，而是**接口被脚本刷爆**和**数据无限堆积**。这三个配套项 README 只列了名字，这里给具体怎么调。

### 7.1 解析并发上限（必调）

每个 Web 解析线程都是一颗实时跑 MSM 解码的 CPU 核，公网上有人连点几下就能把 2 核机器吃满。

```ini
[web]
max_web_parsers = 4              # 同时被解析的挂载点上限，2C2G 建议 3~4
parser_client_ttl = 45           # 心跳超时（秒），兜住"直接关浏览器"
parser_grace_seconds = 5         # 订阅归零后的宽限期，来回切站不重建线程
parser_ip_rate_per_minute = 10   # 单 IP 每分钟最多发起几次解析
```

超限时新订阅返回 **429**，前端提示「当前解析任务已满，请稍后再试」。这四个数都不是拍脑袋：`max_web_parsers` 按你的核数定，`parser_ip_rate_per_minute` 按「一个正常访客切换站点的频率」定，正常人手速不会超过每分钟 10 次。

### 7.2 数据页对匿名开放（可选）

默认未登录访问会跳登录页。如果你想让访客**不用登录就能看 STR 表和实时卫星图**（这通常是演示站的主要看点）：

```ini
[web]
public_access = true
```

开启后：

- 匿名请求只走 `/api/public/` 命名空间，用户清单、源站 IP、系统日志不会下发
- 匿名也可以自己发起解析，否则卫星图没有数据可看
- 用户管理、挂载点管理、设置页**仍然要登录**
- 建议同时把 `max_web_parsers` 再压一档，匿名流量比登录流量更难约束

不开这个开关也完全能用——访客登录 `admin/admin123` 后一样能看，只是多一步。

### 7.3 60 天清理（公网必须）

别人能随便加数据，不清就是个垃圾场。`users` / `mounts` 两张表在程序启动时会自动补 `created_at` 列（老库直接升级即可，不用手工迁移）。

```bash
# 先预览要删什么（默认就是预览，不会改数据）
sudo -u 2rtk /opt/2rtk/venv/bin/python /opt/2rtk/scripts/demo_reset.py

# 确认无误再真删
sudo -u 2rtk /opt/2rtk/venv/bin/python /opt/2rtk/scripts/demo_reset.py --apply --days 60
```

脚本只动 `users` 和 `mounts`，**永远不碰 `admins`**；删用户前会先解绑挂载点的 `user_id`；`created_at` 为空的历史行默认保留，要一起清就加 `--include-no-date`。

配成定时任务：

```bash
sudo tee /etc/systemd/system/ntrip-demo-cleanup.service >/dev/null <<'EOF'
[Unit]
Description=NTRIP demo data cleanup (60 days)

[Service]
Type=oneshot
User=2rtk
Environment=NTRIP_CONFIG_FILE=/etc/2rtk/config.ini
ExecStart=/opt/2rtk/venv/bin/python /opt/2rtk/scripts/demo_reset.py --apply --days 60
EOF

sudo tee /etc/systemd/system/ntrip-demo-cleanup.timer >/dev/null <<'EOF'
[Unit]
Description=Run NTRIP demo cleanup daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ntrip-demo-cleanup.timer
systemctl list-timers ntrip-demo-cleanup.timer
```

几个容易写错的地方：

- **`User=2rtk` 不能省**。以 root 跑会在数据目录里留下 root 属主的库文件，之后服务以 `2rtk` 身份启动就会报权限错误。
- **用项目自己的 venv 解释器**（`/opt/2rtk/venv/bin/python`），不要用系统 `python3`——脚本要 `from src import config` 读配置，venv 里才有完整环境。
- **`NTRIP_CONFIG_FILE` 要显式给**，否则脚本会去源码目录找 `config.ini`；脚本安装时那是个软链，软链一断就读不到库路径了。

Docker 部署在容器内跑（镜像里已经带了 `scripts/`）：

```bash
docker compose -f docker-compose.demo.yml exec ntrip-demo \
    python /app/scripts/demo_reset.py --apply --days 60
```

---

## 8. Nginx + HTTPS（强烈建议）

### 8.0 只跑 HTTP：不配域名和证书也能用

程序里没有任何逻辑依赖 TLS，演示站直接 `http://服务器IP:5757` 就能跑完全部功能（登录、切换挂载点、
实时解析、卫星图）。只要确认这三条：

| 配置项 | 纯 HTTP 时 | 说明 |
|---|---|---|
| `security.session_cookie_secure` | **false**（默认值） | 设成 true 后浏览器在 HTTP 下不回传 Cookie，症状是「登录成功但立刻跳回登录页」 |
| `security.trust_proxy_headers` | **false**（默认值） | 没有反代时必须关；挂了 Nginx 才改 true，否则按 IP 限流全算在 127.0.0.1 上 |
| `web.public_access` | 按需 | 想让访客不登录也能看数据页就设 true，否则他们要用 `admin / admin123` 登录一次 |

代价说清楚：`admin / admin123` 与 Cookie 都是明文，谁抓包谁就能登录；但演示模式下能做的只有
「新增用户 / 新增挂载点 / 启停解析」，改、删、改密码、重启仍被后端 403 挡住，数据本来也会定期清空 ——
纯演示用途可以接受。要长期挂着、或者有真实用户，还是按 8.1 上 HTTPS。

### 8.1 有域名时：Nginx + HTTPS

演示站是给陌生人用的，Cookie 和口令不该裸奔。最小可用配置——**整段直接存成 `/etc/nginx/conf.d/demo.conf` 即可**（`conf.d/*.conf` 是被 `http {}` 包含的，所以 `limit_req_zone` 放在这个文件里也合法）：

```nginx
# 对写接口单独限流，比在应用层做更省 CPU
limit_req_zone $binary_remote_addr zone=demo_write:10m rate=20r/m;

server {
    listen 443 ssl;
    server_name demo.example.com;

    ssl_certificate     /etc/letsencrypt/live/demo.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/demo.example.com/privkey.pem;

    # 反代参数放在 server 级，下面的 location 自动继承（不用依赖 proxy_params 文件，
    # 官方 nginx 镜像里没有那个文件，include 会直接报错）
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade           $http_upgrade;   # Socket.IO 需要
    proxy_set_header Connection        "upgrade";
    proxy_read_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:5758;      # Docker 部署 5758；脚本安装 5757
    }

    # 只给"新增"限流，GET 不限（看数据页要频繁请求）
    location = /api/users  { limit_req zone=demo_write burst=5 nodelay;
                             proxy_pass http://127.0.0.1:5758; }
    location = /api/mounts { limit_req zone=demo_write burst=5 nodelay;
                             proxy_pass http://127.0.0.1:5758; }
}

server {
    listen 80;
    server_name demo.example.com;
    return 301 https://$host$request_uri;
}
```

反代之后，`config.ini` 里这两项要打开，否则拿到的 IP 全是 `127.0.0.1`（限流会把所有访客当成同一个人）：

```ini
[security]
trust_proxy_headers = true       # 信任 X-Forwarded-For
session_cookie_secure = true     # 只走 HTTPS 传 Cookie
```

`trust_proxy_headers` **只能在确实位于可信反代之后时开**，否则客户端可以自己伪造 `X-Forwarded-For` 绕过限流。

NTRIP 端口（2101 / 2102）是裸 TCP，不走 Nginx，直接在防火墙/安全组放行即可。

---

## 9. 验收清单

部署完照着打一遍，每条都对了才算完成。以下命令在服务器本机执行，Docker 部署把 `5757` 换成 `5758`。

```bash
BASE=http://127.0.0.1:5757

# 1. 开关生效
curl -s $BASE/health | grep -o '"demo_mode":true'

# 2. 登录（用 cookie 罐保存会话）
curl -s -c /tmp/demo.jar -X POST $BASE/api/login \
     -H 'Content-Type: application/json' \
     -d '{"username":"admin","password":"admin123"}' -o /dev/null -w "login=%{http_code}\n"
# 期望 200

# 3. 新增用户放行
curl -s -b /tmp/demo.jar -X POST $BASE/api/users \
     -H 'Content-Type: application/json' \
     -d '{"username":"guest_test","password":"guest123"}' -o /dev/null -w "create_user=%{http_code}\n"
# 期望 201

# 4. 改用户被拦
curl -s -b /tmp/demo.jar -X PUT $BASE/api/users/guest_test \
     -H 'Content-Type: application/json' \
     -d '{"password":"newpass123"}' -w "  edit_user=%{http_code}\n"
# 期望 403 {"code":"demo_readonly",...}

# 5. 删用户被拦
curl -s -b /tmp/demo.jar -X DELETE $BASE/api/users/guest_test \
     -w "  delete_user=%{http_code}\n"
# 期望 403

# 6. 改管理员密码被拦
curl -s -b /tmp/demo.jar -X PUT $BASE/api/admin/password \
     -H 'Content-Type: application/json' -d '{}' -w "  admin_pwd=%{http_code}\n"
# 期望 403

# 7. 重启被拦
curl -s -b /tmp/demo.jar -X POST $BASE/api/system/restart -w "  restart=%{http_code}\n"
# 期望 403

# 8. 非默认管理员登录被拒（返回登录页，含错误提示）
curl -s -X POST $BASE/api/login -H 'Content-Type: application/json' \
     -d '{"username":"someone","password":"whatever"}' -o /dev/null -w "other_admin=%{http_code}\n"
# 期望 401 或 200 返回登录页错误，反正不会进后台

# 清理测试数据
sqlite3 /opt/2rtk/data/2rtk.db "DELETE FROM users WHERE username='guest_test';"
```

界面侧再确认三件事：

1. 用户页 / 挂载点页**没有**「编辑」「删除」按钮，顶部有黄色提示条
2. 设置页的「修改管理员密码」「重启服务」两块变成了一段演示说明
3. 登录页显示「演示账号 `admin / admin123`」，访客不用猜

---

## 10. 常见问题

**开着一个浏览器能进去，另一个浏览器进去什么都没有**

正常。演示站也是按会话登录的，每个浏览器要各自登录一次。想免登录看数据，开 `public_access`（第 7.2 节）。

**访客反馈「按钮是灰的 / 点了没反应」**

多半是 429：解析任务已满。看日志有没有 `解析任务已达上限`。要么等别人退订，要么调大 `max_web_parsers`（先看 CPU 余量）。

**访客说「我加的挂载点在列表里找不到」**

挂载点列表默认只显示有连接在线的站点，离线的不显示。让他推流后再看；也可以直接看 `/api/public/mount/rtcm-parse/status`。

**页面上的「编辑 / 删除」按钮没了，是不是坏了**

演示模式下前端本来就不渲染这些入口。真想验证后端的锁还在，用第 9 节的 curl 直接打接口，会拿到 403。

**两个访客在看不同的站，会不会互相干扰**

不会。v2.3.6 起解析线程按挂载点复用、每个客户端持有自己的订阅，A 切站不会打断 B，数据也按房间隔离。同一个人同时只能订阅一个站（界面本来就是单选）。

**清理脚本会不会删掉管理员**

不会。脚本只处理 `users` / `mounts`，`admins` 表永远不动——那是演示站的入口。

**服务器被人刷了几万条数据**

先停服务（`systemctl stop 2rtk`），再 `demo_reset.py --apply --days 0` 清空非管理员数据，然后收紧 `parser_ip_rate_per_minute` 和 Nginx 的 `limit_req`，最后考虑在防火墙层封 IP 段。

---

## 11. 关掉演示模式

```bash
sudo sed -i 's/^demo_mode *=.*/demo_mode = false/' /etc/2rtk/config.ini
sudo systemctl restart 2rtk
curl -s http://127.0.0.1:5757/health | grep -o '"demo_mode":false'
```

功能立刻回到完整管理员模式，界面上的编辑/删除按钮也会重新出现。**但数据不会回滚**——演示期间被创建、被清理的东西保持现状。

如果这台机器要转成生产站，还要做三件事：

1. 改掉 `admin123`（设置页或直接改库）
2. 把 `public_access` 关掉（如果你想恢复「必须登录才能看」）
3. 清掉演示期间留下的测试账号和挂载点，或者干脆换一个新库

---

## 附：一页速查

> 下面是**开启演示模式之后**这几项应取的值，不是出厂默认。出厂默认：`demo_mode`、`public_access`、`trust_proxy_headers`、`session_cookie_secure` 全为 `false`，`max_web_parsers`、`parser_*` 为正常模式下的默认值。

```ini
# /etc/2rtk/config.ini
[security]
demo_mode = true                 # 演示模式总开关
trust_proxy_headers = true       # 只在 Nginx 之后开；直连 5757 保持 false
session_cookie_secure = true     # 只在 HTTPS 下开；纯 HTTP 必须保持 false

[web]
public_access = true             # 可选：匿名也能看数据页
max_web_parsers = 4              # 解析并发上限
parser_client_ttl = 45           # 心跳超时（秒）
parser_grace_seconds = 5         # 退订宽限期（秒）
parser_ip_rate_per_minute = 10   # 单 IP 每分钟解析次数
```

```bash
sudo systemctl restart 2rtk                                  # 改完必重启
curl -s http://127.0.0.1:5757/health                         # 看 demo_mode
sudo -u 2rtk /opt/2rtk/venv/bin/python /opt/2rtk/scripts/demo_reset.py            # 预览清理
sudo -u 2rtk /opt/2rtk/venv/bin/python /opt/2rtk/scripts/demo_reset.py --apply    # 真删
```

英文版：[DEMO-TUTORIAL-EN.md](DEMO-TUTORIAL-EN.md)
