# NTRIPcaster 代码审核报告

- **仓库**：https://github.com/Rampump/NTRIPcaster.git
- **版本**：v2.2.0（单提交 `1bceb9b`，浅克隆）
- **规模**：71 个受版本控制文件，自研 Python 约 7,300 行（`main.py` 463 + `src/` 约 6,800），内置 `pyrtcm` 副本约 5,000 行
- **技术栈**：Python 3 / Flask 3 + Flask-SocketIO(threading) / SQLite / 原生 socket 线程池 / Docker + Nginx + Prometheus/Grafana
- **审核方式**：全量阅读自研源码 + 关键路径动态验证（协议识别、响应报文实测）+ 部署编排配置核对

---

## 一、总体评价

工程骨架完整、模块划分清晰，是一个**能跑起来的真实项目**，而不是玩具：NTRIP 0.8/1.0/2.0 三种协议入口都做了兼容，挂载点鉴权、STR 表自动生成、RTCM 解析、Web 管理端、容器编排、监控告警都已具备，SQL 全部参数化（无注入），用户/管理员密码使用 PBKDF2 存储，容器用 `tini` + 非 root 运行，这些是实打实的加分项。

但**距离"生产可用"还有明显距离**，核心问题集中在四处：

| 维度 | 结论 |
|---|---|
| 正确性 | 多个必然触发的功能性 Bug（健康检查永远失败、`/classic` 页面 404、Digest 认证恒失败、响应头 Content-Length 与字节数不符） |
| 并发模型 | 线程模型（1 连接 = 1 线程 + 广播时持锁阻塞发送），按项目自带压测数据 2002 连接占用 10.7 GB 内存，约 **5.3 MB/连接**，5000 上限实际会 OOM |
| 安全 | 默认弱口令 + 密钥硬编码入库、SocketIO 无鉴权、多个敏感 API 无鉴权、日志打印 Basic 凭据、挂载点密码明文返回 |
| 运维 | 数据卷与 SQLite 路径错配（重建容器丢库）、`SIGTERM` 时优雅关闭会卡死、健康检查端点缺失 |

**优先级建议**：P0 项（约 10 处）必须在对外提供服务前修完，P1 项建议排进第一个迭代，P2 属于重构级改造（线程模型 → asyncio/selectors），需要单独立项。

---

## 二、P0 严重问题（必须修）

### P0-1 健康检查必然失败，导致依赖服务永远不启动

`healthcheck.py:40` 探测 `http://localhost:5757/health`，但 `src/web.py` 中**没有任何 `/health` 路由**（全仓库仅 `nginx/conf.d/ntrip.conf` 里用 Nginx `return 200` 兜了个 `/health`，与应用无关）。

后果链：

1. `urllib.request.urlopen` 收到 404 抛 `HTTPError`（`URLError` 子类）→ `check_web_service` 返回 False；
2. `healthcheck.py` 以 exit 1 结束 → 容器状态 `unhealthy`；
3. `docker-compose.yml` 中 `nginx` 依赖 `condition: service_healthy` → **Nginx 永远起不来**，`docker compose up` 静默卡住（`--profile nginx` 场景）；
4. Dockerfile 的 `HEALTHCHECK` 同样恒失败，k8s/Portainer 会反复重启。

修复（任选其一，建议 A）：

```python
# src/web.py，_register_routes 内新增
@self.app.route('/health')
def health():
    return jsonify({'status': 'ok', 'uptime': time.time() - self.start_time}), 200
```

同时把 `check_disk_space` 里硬编码的 `/app` 改成 `os.getenv('DATA_DIR', '/app')`，否则非容器部署时该项恒失败。

### P0-2 容器重建丢数据库（数据卷与 DB 路径错配）

- `src/config.py:161`：`DATABASE_PATH` 默认 `2rtk.db`（相对路径）；
- `Dockerfile` 未设置 `DATABASE_PATH`，`docker-compose.yml` / `docker-compose.prod.yml` 的 `environment` 里也没有传；
- 容器 `WORKDIR /app`，于是数据库落在 `/app/2rtk.db`；
- 而持久化卷挂的是 `ntrip-data:/app/data`。

即：**用户、挂载点、密码全部写在非持久层**，`docker compose down && up` 后配置清零、重新生成 `admin/admin123`。`.env.example` 里明明写了 `DATABASE_PATH=/app/data/2rtk.db`，但 compose 没把它传进去。

修复：

```yaml
# docker-compose.yml / .prod.yml 的 ntrip-caster.environment 追加
- DATABASE_PATH=/app/data/2rtk.db
- LOG_DIR=/app/logs
```

并在 `main.py:check_environment()` 里对 `DATABASE_PATH` 的目录做 `mkdir(parents=True, exist_ok=True)`（目前只创建了 `LOG_DIR` 和 `Path(DB).parent`，前者恰好覆盖）。

### P0-3 优雅关闭会卡死，`Ctrl+C` / `docker stop` 只能靠 SIGKILL

`src/ntrip.py:1711`：

```python
self.thread_pool.shutdown(wait=True)
```

下载连接的处理函数 `handle_download → _keep_connection_alive()` 是**长驻循环**（`while True: sleep(5)`），只有 socket 出错才退出。而 `main.py:397→403` 的关闭顺序是「先 `ntrip_caster.stop()`，后 `forwarder.stop_forwarder()`」，后者才关客户端 socket。于是：

- `stop()` 关闭监听 socket，但不会关闭已建立的下载 socket；
- `shutdown(wait=True)` 等待所有 worker 退出 → 永久阻塞；
- `SIGTERM` 处理器阻塞 → `docker stop` 10 秒后 SIGKILL，日志停在「正在关闭线程池…」。

更糟的是 `_signal_handler` 里的 `self.stopping` 已在 `stop_all_services` 里置 True，第二次信号会被直接忽略，运维人员按两次 Ctrl+C 也退不出来。

修复：

```python
def stop(self):
    self.running = False
    if self.server_socket:
        self.server_socket.close()
    # 先断客户端，再等线程池
    forwarder.stop_forwarder()          # 关闭所有客户端 socket，唤醒 worker
    if self.thread_pool:
        self.thread_pool.shutdown(wait=True)
```

同时 `ServiceManager.stop_all_services` 里的顺序应调整为「转发器 → NTRIP → Web」，并给 `shutdown` 加超时兜底（`shutdown(wait=False)` + 轮询 `_threads`）。

### P0-4 NTRIP 2.0 下发数据使用 chunked 分帧，但响应头没声明

`src/forwarder.py:405-409`：协议版本为 `ntrip2_0` 时把每块数据包装成 `<hex>\r\n<data>\r\n` 的 chunked 格式；

而 `src/ntrip.py:1381-1388` 的 `send_download_success_response()` 只发了 `Content-Type: application/octet-stream` + `Connection: keep-alive`，**没有 `Transfer-Encoding: chunked`**。

结果：严格的 NTRIP 2.0 客户端（RTKLIB、部分接收机）会把 `00000032\r\n` 当成 RTCM 数据流解析 → 帧同步失败、持续丢数据。这也是项目里"2.0 有时能连、有时解不出"类问题的常见根因。

修复：`ntrip2_0` 分支补 `Transfer-Encoding: chunked`，或者干脆去掉 chunk 包装按裸流发送（NTRIP 2.0 允许 `Content-Type` + 长连接裸流，兼容性更好，建议后者）。

### P0-5 响应头 `Content-Length` 按字符数计算，中文响应会破帧

`src/ntrip.py:1501`：`headers.append(f"Content-Length: {len(content)}")`，随后 `response.encode('utf-8')`。中文错误消息（如「挂载点密码错误」6 字符 = 18 字节）会声明 `Content-Length: 6`。

实测（本地构造 NTRIP 2.0 401 响应）：

```
HTTP/1.1 401 Unauthorized
...
Content-Type: text/plain
Content-Length: 6      <-- 实际 body 18 字节
挂载点密码错误
```

客户端按 6 字节收完就断，剩余字节污染下一条响应/数据流。修复：`len(content.encode('utf-8'))`。

### P0-6 日志泄露 Basic 认证凭据（明文口令进日志库）

`src/ntrip.py:1089`：

```python
logger.log_info(f"... auth_header={auth_header[:50] if auth_header else 'None'}")
```

`Authorization: Basic <base64(user:pass)>` 前 50 字符基本就是完整凭据（base64 可直接解出 `用户名:密码`）。而 `_sanitize_request_for_logging()` 只处理了请求行和 header 行的打印，这条日志绕过了脱敏。

同类问题：`src/web.py:788` 的 `GET /api/mounts` 直接把 `mount[2]`（**挂载点明文密码**）返回给前端；`src/database.py:302` 挂载点密码本身就是明文入库。

修复优先级：① 删除/降级 1089 这条日志，只保留 `is_valid` 结果；② `/api/mounts` 返回掩码（`******`）或干脆不回传，编辑时留空表示不改；③ 挂载点密码改用 `hash_password()`（NTRIP 1.0 校验走 `verify_password`），兼容期保留明文回退。

### P0-7 Digest 认证实际恒失败

链路：`src/ntrip.py:773` 用 `db_manager.get_user_password()` 取密码 → `src/database.py:470` 直接返回库里的 **PBKDF2 哈希串** `salt$hash` → `_validate_digest_response()` 用它当作"密码"算 `HA1 = md5(user:realm:salt$hash)`，而客户端算的是 `md5(user:realm:真实密码)` → 永不相等。

第二个坑：`_validate_digest_response` 的 `uri` 参数传的是去斜杠的挂载点名（`MOUNT`），客户端算 HA2 用的是请求行里的 `/MOUNT` → 同样对不上。

第三：`send_auth_challenge()` 每次都发新 `nonce`，但服务端从不校验 nonce 来源/时效 → 一旦修好上面的问题，会立刻暴露**重放漏洞**（抓包可无限复用同一个 `response`）。

修复：用户表增加 `password_plain_for_digest` 不可行（等于明文）；正确做法是 Digest 场景下存 `HA1 = md5(user:realm:password)`（realm 固定为 `NTRIP`），验证时直接用 HA1 算期望值，同时引入 nonce 缓存 + 过期（如 300s）+ `nc` 单调校验。若不想做，就**明确关闭 Digest**，只支持 Basic（NTRIP 2.0 允许），比现在"发了 challenge 但永远验不过"要好。

### P0-8 修改挂载点密码会静默解绑所属用户（越权）

`src/web.py:949-954` 调用 `update_mount(mount_id, new_name, new_password, new_user_id)`，而 `new_user_id` 在请求体未携带 `user_id`/`username` 时是 `None`。

`src/database.py:329`：`new_user_id = user_id if user_id != 'keep_current' else old_user_id` —— `None != 'keep_current'` 成立 → 直接写成 `NULL`。

后果：管理员只想改个密码，挂载点却变成"未绑定"。而 `verify_mount_and_user` 的 2.0 分支只在 `bound_user_id is not None` 时才校验绑定（`database.py:132`）→ **任何注册用户都能向该挂载点推流/取流**。

修复：web 层未传绑定信息时传哨兵值 `'keep_current'`；`update_mount` 内部改成 `if user_id is _UNSET` 显式语义，不要用魔法字符串。

### P0-9 SocketIO 全量数据无鉴权广播

`src/web.py:1135-1145` 的 `@self.socketio.on('connect')` 只做 `join_room('data_push')`，**不检查 `session['admin_logged_in']`**。任何人不登录、拿浏览器控制台就能：

- 收到 `system_stats_update`（CPU/内存/带宽/连接数）；
- 收到 `online_users_update`（**全部在线用户名、IP、连接数**）；
- 收到 `online_mounts_update`（挂载点、源站 IP、数据率）；
- 收到 `log_message`（运行日志，含异常堆栈摘要）。

配合下面 P0-10 的无鉴权 REST 接口，等于把一个 NTRIP 运营节点的运行态势完全公开。修复：

```python
@self.socketio.on('connect')
def handle_connect(auth=None):
    if not session.get('admin_logged_in'):
        return False          # 直接拒绝握手
    join_room('data_push')
```

### P0-10 多个敏感 REST 接口缺少 `@require_login`

| 接口 | 位置 | 泄露内容 |
|---|---|---|
| `GET /api/system/stats` | `web.py:1034` | CPU/内存/带宽/连接数/总流量 |
| `GET /api/str-table` | `web.py:1051` | 全部挂载点 STR 表（含源站坐标、设备型号） |
| `GET /api/mounts/online` | `web.py:1075` | 挂载点 + 源 IP + 用户数 + 数据统计 |
| `GET /api/app_info` | `web.py:551` | 版本号（低危，可留） |

对比同文件其他接口都加了 `@self.require_login`，这四处属于漏加。修复：统一加上装饰器，或改用 `before_request` 白名单（`/login`、`/api/login`、`/static/*`、`/health`）做默认拒绝，避免以后新增接口再漏。

---

## 三、P1 重要问题

### 3.1 连接与转发（性能核心）

**P1-1 广播持锁发送，一个慢客户端拖死全场**
`forwarder.py:340-344`：`_broadcast_data()` 在 `with self.client_lock:` 内部循环 `_send_data_to_clients` → `sendall()`。任一客户端 TCP 窗口打满，`sendall` 阻塞（该 socket 超时是 `SOCKET_TIMEOUT=120s`），**整个广播线程卡住，所有挂载点、所有用户同时停流**。这是 NTRIP caster 最典型的雪崩点。
修复：锁内只做快照（已有 `clients[:]` 拷贝，把 `send` 移到锁外），并把客户端发送改为非阻塞 + 每客户端待发队列，队列超阈值直接踢人。

**P1-2 线程模型不可扩容**
`ntrip.py:1578` 起 `ThreadPoolExecutor(max_workers=5000)`，下载连接在线程内 `while True: sleep(5)` 常驻。项目自带压测报告（`tests/2000_connections_test_report.md`）已给出实证：**2002 连接 / 10.7 GB 内存 ≈ 5.3 MB/连接**，CPU 峰值 32%。按此推算 5000 连接需 26 GB+，与 `config.ini` 里 `max_memory_usage = 2048` 的设想严重不符。
另外 5000 个 Python 线程争抢 GIL 后，广播线程的 `sleep(0.01)` 抖动会明显放大。
建议：单进程 asyncio（`asyncio.start_server` + `StreamWriter`），或 `selectors` 事件循环 + 固定大小工作池。这是唯一能把单机连接数从 2k 拉到 2w 的路径，但属重构，建议独立排期。

**P1-3 `get_since()` 每客户端每轮全量扫描**
`forwarder.py:366` + `connection.py` 的环形缓冲实现：每 10ms、每个客户端都从 deque 头遍历 60 个元素比对时间戳。2000 客户端 × 100 Hz × 60 ≈ 1200 万次比较/秒，这正是 32% CPU 的主要来源，且随连接数线性恶化。
修复：环形缓冲改为「单调递增序号 + 每客户端游标」，`buf[cursor:]` 直接切片，O(新增块数)。

**P1-4 环形缓冲按"条数"而非"时长"**
`config.py:225` 注释写的是秒，`RingBuffer` 实际 `deque(maxlen=60)` = 60 个数据块。10ms 广播 + 典型 1 s/块的 RTCM 输出 → 缓冲仅覆盖 1～2 秒，新客户端几乎拿不到历史；若上游突发小包，又会瞬间填满。建议按秒估算 `maxlen = 时长 / 平均块间隔`，或改成「保留最近 N 秒」的时间窗口实现。

**P1-5 `except OSError` 写在 `except socket.timeout` 之前，超时分支永远走不到**
`ntrip.py:1217-1226`：`socket.timeout` 是 `OSError` 子类，第一个分支先命中 → `break`，上游 120 秒无数据会被**直接断开**（`config.socket_timeout=120`），而不是按设计 `continue` 继续等。
更糟的是该分支里 `if e.winerror == 10038` —— `winerror` 是 **Windows 专有属性**，Linux 上抛 `AttributeError`（被外层 `except Exception` 捕获，日志刷错误堆栈）。
修复：先 `except socket.timeout: continue`，再 `except OSError as e:`，且用 `getattr(e, 'winerror', None) == 10038`。

**P1-6 `send()` 而非 `sendall()`**
`ntrip.py:1332 / 1356 / 1509`：SOURCETABLE 与响应体用 `send()`，大 STR 表（几百个挂载点）在拥塞时可能只发一部分 → 客户端拿到截断表。批量改为 `sendall()`。

**P1-7 请求只 `recv()` 一次就解析**
`ntrip.py:128`：假设整个请求头一次到达。TCP 分段、NTRIP 2.0 客户端分两次写 `GET` + `Authorization` 时会解析失败返回 400。应循环读到 `\r\n\r\n`（带上限如 8 KB）。

**P1-8 `handle_download` 不校验挂载点是否在线**
只查 `check_mount_exists_in_db()`，挂载点离线时客户端连上后一直干等，不符合 NTRIP 规范（应 404 或先给 `SOURCETABLE`）。且 `add_user_connection` 成功但 `forwarder.add_client` 失败时未回滚计数 → 计数漂移。

**P1-9 连接计数的键位用错（把 IP 当 connection_id 传）**
`ntrip.py:1522`：`remove_user_connection(self.username, self.client_address[0], self.mount)` —— 第二参数是 `connection_id`，实际传了 IP；`forwarder.py:267` 同样。因为 `mount_name` 也传了，最终按「用户 + 挂载点」整批删除：同用户在同挂载点的多条连接，关一条会把计数一起减掉，`MAX_CONNECTIONS_PER_USER` 限流随之失真。应统一传真正的 `connection_id`。

### 3.2 认证与数据库

**P1-10 下载鉴权不校验用户-挂载点绑定**
`database.py:485-510` 的 `verify_download_user` 注释直言"只验证用户名密码，不验证挂载点绑定关系"。于是绑定到 A 站点的用户可以直接拉 B 站点数据，`mounts.user_id` 形同虚设。若这是有意设计（所有付费用户可拉全部数据），请写进 README 并在 Web 端明确展示；否则补上 `bound_user_id` 判断。

**P1-11 全库串行锁 + 每次新建连接**
`database.py:12` 的模块级 `db_lock` 把所有 DB 操作串成单线程；每个函数都 `sqlite3.connect()` → 用完 `close()`。`verify_download_user`、`check_mount_exists_in_db`、`get_user_password` 甚至**不加锁**。取流路径上每次连接要做 2～3 次 SQLite 建连/查询，高并发下是隐藏的延迟尖刺。`DB_POOL_SIZE`/`DB_TIMEOUT` 配了但完全没用。
建议：`sqlite3.connect(..., check_same_thread=False)` + `threading.local` 连接复用，开 WAL，读操作不加全局锁。

**P1-12 密码校验非常量时间、Legacy 明文回退**
`database.py:26-34`：`'$' not in stored_password → return stored_password == provided_password`（兼容遗留明文），且哈希比较用 `==` 而非 `hmac.compare_digest`。建议 `compare_digest`，并把明文回退限制为「首次登录自动迁移为哈希」。

**P1-13 登录无限暴力破解**
`/login`、`/api/login` 无频率限制、无失败计数、无锁定；`config.SESSION_TIMEOUT` 配了但从未使用（会话永不过期）。Nginx 侧虽有 `limit_req`，但接口直连 5757 端口可绕过。建议：IP + 用户名双维度失败计数（5 次锁 5 分钟），`session.permanent = True` + `PERMANENT_SESSION_LIFETIME = SESSION_TIMEOUT`。

**P1-14 默认弱口令与硬编码密钥入库**
- `config.ini:65` `secret_key = 8f4a9c2e...`（且 `config.py:185` 把它当 fallback 写死在代码里）；
- `config.ini:72` `admin / admin123`；
- `install.sh`、`tests/*.py` 里同样硬编码 `admin123`；
- 仓库**没有 `.gitignore`**，`2rtk.db`、`logs/`、`.env` 都可能被误提交（当前尚无，但风险敞口在）。

修复：删掉 `config.ini` 的入库（只留 `config.ini.example`，`config.py` 优先读 `config.ini` 不存在则读 example 并提示）、密钥改为启动时随机生成落盘、首次启动强制改密。

### 3.3 Web 层

**P1-15 多处异常处理里引用未定义变量**
`web.py:1099`：`except Exception as e: log_error(f"获取挂载点{mount_name}历史数据失败: {e}")` —— 该函数作用域内没有 `mount_name`（是从 `api_get_rtcm_history` 复制粘贴过来的），`NameError` 会在异常分支里再抛一次，客户端拿到未处理的 500 而非约定的 JSON。

**P1-16 `/classic` 路由必然 404**
`web.py:172` 加载 `templates/index.html`，但 `templates/` 下只有 `login.html` 和 `spa.html`。`_load_template` 会返回 `<h1>模板文件未找到: index.html</h1>`。要么补模板，要么删路由。

**P1-17 前端 `innerHTML` 拼接未转义（存储型 XSS）**
`static/app.js:2073`、`1448-1455` 等直接用模板字符串拼 `mount_name`、`username`、`user_agent`、`ip_address` 后 `innerHTML`。用户名/挂载点在创建时有 `^[a-zA-Z0-9_-]+$` 白名单，但**挂载点名来自 NTRIP 请求路径**（`ntrip.py:1057` 只做 `lstrip('/')`，无字符校验），会原样进入在线挂载点列表并被渲染。
利用条件：需要一个有效挂载点口令（或存在 P0-8 的绑定解绑缺陷 + 任意注册用户），即可在管理员浏览器里执行脚本 → 直接调 `/api/users` 窃取列表/改管理员密码。修复：加 `escapeHtml()` 统一转义，或改 `textContent`/`createElement`（日志区 `2289` 行已用 `textContent`，说明作者知道这个套路，只是没贯彻）。

**P1-18 状态变更接口无 CSRF 保护**
所有 `POST/PUT/DELETE` 只依赖 `session` Cookie，无 CSRF token、无 `SameSite` 显式设置、无 `Origin/Referer` 校验。虽然 `request.get_json()` 过滤掉了传统表单跨站提交，但 `nginx` 里那条 `add_header Access-Control-Allow-Origin "*"`（`ntrip.conf` 的 `/api/` 段）又把跨域读取的口子开回来了。建议：删除该 CORS 头（项目已声明"同域部署不需要 CORS"），并加 `SESSION_COOKIE_SAMESITE='Lax'` + `SESSION_COOKIE_SECURE=True`（HTTPS 场景）。

**P1-19 重启接口直接 `os._exit(0)`**
`web.py:309`：管理员点一下「重启」就是硬退出，靠容器 `restart: unless-stopped` 拉起；裸机部署（`install.sh` 的 systemd）虽然也能拉起，但没有排空连接、没有写日志落盘确认。建议改为 `signal.raise_signal(SIGTERM)` 走正常关闭流程。

**P1-20 WebManager 里创建了一个无人使用的第二个 ConnectionManager**
`web.py:53-54`：`global rtcm; rtcm = connection.ConnectionManager()`，之后全文件都用 `connection.get_connection_manager()`。多一个实例就是多一份易混淆状态，且 `rtcm` 是模块级变量，任何误用都会读到空表。直接删。

### 3.4 部署与配置

**P1-21 `cleanup_zombie_connections()` 每次源站上线都 fork `netstat`**
`connection.py:164`：`subprocess.run(['netstat','-an'], shell=True)`，在 `handle_upload` 里**每来一个源站连接就执行一次**。两个问题：

1. 性能：进程创建 + 全表解析，源站重连风暴时会明显拖慢；
2. 正确性：`:2101` 是**硬编码端口**（`connection.py:172`），一旦 `config.ini` 改端口，`established_ips` 恒为空 → **每次有源站重连，都会把所有在线挂载点判定为"僵尸"并强杀**。

修复：删掉这套启发式（应用层已有 `last_data_time` 空闲判定）；若确实需要，改用 `psutil.net_connections(kind='tcp')` 且端口从配置读，并把调用从热路径移到定时任务。

**P1-22 建库路径与日志目录、`check_port` 顺序**
`main.py:441` 先 `check_environment()` 再 `config.init_config()`，而 `load_from_env()` 在 `init_config()` 里才执行 → 端口检查用的是**配置文件里的端口**，环境变量 `NTRIP_PORT`/`WEB_PORT` 的覆盖对检查无效，可能出现"检查通过但真正 bind 失败"，反之亦然。把 `init_config()` 提前。

**P1-23 NTRIP 监听地址忽略配置**
`ntrip.py:1574` 硬编码 `bind(('0.0.0.0', NTRIP_PORT))`，`config.HOST`（注释声明"Web/NTRIP 监听地址"）对 NTRIP 端口不生效。想只在内网/本机暴露 NTRIP 时无法配置。

**P1-24 依赖不可复现 + 内置库与 pip 包重复**
- `requirements.txt`：`werkzeug>=3.0.6`、`pyrtcm>=1.1.0`、`pyproj>=3.6.1`、`reverse-geocode>=1.6.0` 全为下限约束，无 hash/lock，两个时间点构建出的镜像可能不同；
- 仓库又内置了一份 `pyrtcm/`（1.1.8），且 `main.py` 把项目根插到 `sys.path[0]` → **本地副本永远覆盖 pip 安装的版本**。升级 pip 包无效，安全补丁也进不来。建议删内置副本，锁定 `pyrtcm==1.1.x` 精确版本。

---

## 四、P2 改进项（不影响正确性，影响长期可维护性）

| 项 | 位置 | 说明 |
|---|---|---|
| 死配置一堆 | `config.py` | `SESSION_TIMEOUT`、`DATA_SEND_TIMEOUT`、`CLIENT_HEALTH_CHECK_INTERVAL`、`MOUNT_TIMEOUT`、`CLIENT_TIMEOUT`、`DB_POOL_SIZE`、`THREAD_POOL_SIZE`、`MAX_USER_CONNECTIONS_PER_MOUNT` 全部只读不用（grep 零引用）。要么接上，要么删掉——留着会让运维产生错误预期 |
| 空函数/死代码 | `connection.py:120` `print_active_connections()` 全被注释、`force_refresh_connections()` 算完 `invalid_mounts` 就丢弃；`web.py:1127` `_ensure_forwarder_started` 空实现 | 清理 |
| 大段注释掉的 print | `ntrip.py`、`web.py`、`forwarder.py` 随处可见 | 用 `log_debug` 替代，靠 `LOG_LEVEL` 控制 |
| 除零/边界 | `main.py:238` `sum(... for mount in conn_stats.get('mounts', []))` 若 `mounts` 是 dict 会 TypeError（`get_system_stats` 里又按 dict 用） | 统一 `mounts` 的数据结构 |
| 时区 | `datetime.utcnow()` 在 3.12+ 已弃用 | 改 `datetime.now(timezone.utc)` |
| 日志级别语义 | `forwarder.py:394` 用 `"Connection" not in str(e)` 判断网络错误 | 改 `isinstance(e, (BrokenPipeError, ConnectionResetError))` |
| 测试可维护性 | `tests/*.py` 硬编码 `192.168.1.4`、`admin123` | 改环境变量；`test_users.json` 含测试账号 |
| 前端单文件 | `static/app.js` 111 KB / `spa.html` 44 KB | 无构建、无模块化，后续维护成本高 |
| 文档一致性 | `README` 声称能力 vs 实际缺失的 `/health`、`/classic` | 修完问题后同步更新 |

---

## 五、优先修复清单（建议执行顺序）

**第 1 批（半天，阻塞上线）**
1. 补 `/health` 路由（P0-1）
2. compose 传 `DATABASE_PATH=/app/data/2rtk.db`（P0-2）
3. `Content-Length` 改字节数（P0-5）、`send`→`sendall`（P1-6）
4. 删除记录 `auth_header` 的日志（P0-6 前半）
5. 4 个无鉴权 API 加 `require_login`、SocketIO `connect` 校验 session（P0-9/P0-10）

**第 2 批（1～2 天，可用性）**
6. 关闭流程重排 + `shutdown` 兜底（P0-3）
7. 2.0 裸流或补 `Transfer-Encoding`（P0-4）
8. `update_mount` 哨兵值修复（P0-8）
9. `/api/mounts` 密码掩码（P0-6 后半）
10. `except` 顺序与 `winerror`（P1-5）
11. `/classic` 模板缺失、`mount_name` NameError（P1-15/16）

**第 3 批（1 周，安全加固）**
12. Digest 改正或明确关闭（P0-7）
13. 登录限流 + 会话超时 + Cookie 安全属性 + 删 Nginx CORS 头（P1-13/18）
14. 前端 `escapeHtml`（P1-17）
15. 默认口令/密钥移出仓库 + `.gitignore`（P1-14）
16. 挂载点密码哈希化（P0-6 第三点）

**第 4 批（重构，1～2 周）**
17. 广播锁外发送 + 每客户端队列（P1-1）
18. 环形缓冲游标化（P1-3/4）
19. 线程模型 → asyncio（P1-2）
20. SQLite 连接复用 + WAL（P1-11）

---

## 六、复核用的关键证据

- `healthcheck.py:40` 探测路径 `/health`，`src/web.py` 全文无该路由（`grep -rn "health" --include=*.py` 仅命中 `healthcheck.py` 自身）。
- 动态验证 1：构造 `Mozilla/5.0` 的 `GET / HTTP/1.1` 走 `_determine_ntrip_version`，落到 `ntrip1_0`（该分支第 429 行 `path` 为未定义变量，只因 `"ntrip" in user_agent` 短路才没炸——属潜伏 Bug）。
- 动态验证 2：构造 NTRIP 2.0 401 中文响应，线上报文 `Content-Length: 6` / 实际 18 字节，确认破帧。
- 静态验证：`grep` 确认 `SESSION_TIMEOUT` 等 8 个配置项零引用；确认 `templates/index.html` 不存在；确认 `web.py:1099` 的 `mount_name` 未定义。
- 压测数据：项目自带 `tests/2000_connections_test_report.md` —— 2002 连接 / 10.7 GB 内存 / CPU 峰值 32%，作为线程模型成本的实证。
