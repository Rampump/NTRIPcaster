# NTRIPcaster 修复记录

对应 `CODE-REVIEW.md` 的《五、优先修复清单》，按批次逐项落地。
全部改动均通过 `python -m compileall`、`node --check static/app.js` 以及端到端实测（起服务 → 登录 → 建挂载点 → NTRIP 推流鉴权 → 关闭）。

改动规模（两轮合计）：20 个文件，+1357 / −594 行；新增 `.gitignore`；移除内置 `pyrtcm/` 副本、版本库中的 `config.ini` 与 `tests/test_users.json`。

> 第一轮：P0 全部 + 大部分 P1（可用性、安全、性能瓶颈）。
> 第二轮（第 5 轮）：补齐 P1-1 完整版（每客户端发送队列 + 发送线程池）、P1-11 连接池、
> 配置示例重写、compose 合并校验、测试脚本去硬编码、文档同步。

---

## 第 1 批：阻塞上线项

| 项 | 状态 | 落点 | 说明 |
|---|---|---|---|
| P0-1 健康检查必然失败 | ✅ | `src/web.py` `/health` | 新增无需登录、不查库的探活端点，返回 `{status, version, uptime}`；`healthcheck.py` 改为读取 `WEB_PORT`/`NTRIP_PORT`/`DATA_DIR` 环境变量，磁盘检查不再硬编码 `/app` |
| P0-2 重建容器丢库 | ✅ | `docker-compose.yml`、`.prod.yml`、`config.py`、`main.py` | compose 传 `DATABASE_PATH=/app/data/2rtk.db`、`LOG_DIR=/app/logs`、`DATA_DIR=/app/data`；`config.py` 改为「环境变量 > 配置文件」；`check_environment()` 用 `resolve()` 建目录并校验可写 |
| P0-5 Content-Length 按字符算 | ✅ | `src/ntrip.py::_send_response` | 改为 `len(content.encode('utf-8'))`；实测「挂载点密码错误」→ `Content-Length: 21`（7 字 × 3 字节） |
| P1-6 `send()` → `sendall()` | ✅ | `src/ntrip.py` | 7 处全部替换，含 SOURCETABLE 与各响应体 |
| P0-6 ① 凭据进日志 | ✅ | `src/ntrip.py::handle_upload` | 删除 `auth_header[:50]`，只记录认证方案名（`Basic`/`Digest`/`None`） |
| P0-9 SocketIO 无鉴权 | ✅ | `src/web.py::handle_connect` | 未登录直接 `return False` 拒绝握手 |
| P0-10 敏感 API 漏鉴权 | ✅ | `src/web.py` | 4 个接口补 `@require_login`，并新增 `before_request` 默认拒绝：`/api/*` 除 `/api/login`、`/api/app_info` 外一律要求会话，防止以后新增接口再漏 |

## 第 2 批：可用性

| 项 | 状态 | 落点 | 说明 |
|---|---|---|---|
| P0-3 优雅关闭卡死 | ✅ | `ntrip.py`、`main.py` | 新增全局 `SHUTDOWN_EVENT`；`_keep_connection_alive` 用 `Event.wait(5)` 代替 `sleep`；`stop()` 顺序改为「置事件 → 关监听 → 停转发器 → shutdown 活跃 socket → `shutdown(wait=False)` + 超时轮询（`config.SHUTDOWN_TIMEOUT`，默认 10s）」；`stop_all_services` 顺序改为转发器 → NTRIP → Web；第二次信号直接 `os._exit(1)`。实测 `stop()` 由「永久阻塞」降到 **0.2s** |
| P0-4 NTRIP 2.0 chunked 未声明 | ✅ | `forwarder.py::_send_data_simple` | 去掉 chunk 包装，2.0/1.0 统一裸 RTCM 流；下载成功响应补 `Cache-Control: no-store` 并注明「无 Content-Length 的流式语义」 |
| P0-8 改密码静默解绑 | ✅ | `database.py`、`web.py` | 引入 `KEEP_USER_BINDING` 哨兵，`update_mount(..., user_id=KEEP_USER_BINDING)` 默认保持原绑定；Web 层用 `binding_provided = 'user_id' in data or 'username' in data` 判断。实测：只改密码后绑定仍在，显式传空才解绑 |
| P0-6 ② `/api/mounts` 回传明文口令 | ✅ | `web.py::api_mounts` | 返回 `password: "******"` + `password_set` 布尔值；前端本来就是「留空表示不改」 |
| P1-5 except 顺序 + winerror | ✅ | `ntrip.py::_receive_rtcm_data` | `socket.timeout` 提到 `OSError` 之前并 `continue`；`getattr(e,'winerror',None)` 兼容 Linux |
| P1-15 `mount_name` NameError | ✅ | `web.py::api_online_mounts_detailed` | 异常分支改用无变量文案 |
| P1-16 `/classic` 必然 404 | ✅ | `web.py` | 改为 302 跳转 `/`，不再加载不存在的 `index.html` |

## 第 3 批：安全加固

| 项 | 状态 | 落点 | 说明 |
|---|---|---|---|
| P0-7 Digest 恒失败 + 重放风险 | ✅（明确关闭） | `ntrip.py` | 认证挑战默认只发 `WWW-Authenticate: Basic`；`_verify_digest_auth` 直接返回「Digest disabled, use Basic」并告警。理由写在代码注释：库中只有 PBKDF2 哈希，无法得出客户端期望的 HA1；nonce 无缓存/时效，修好校验反而暴露重放 |
| P1-13 登录无限爆破 / 会话不过期 | ✅ | `web.py`、`config.py` | 按 `(IP, 用户名)` 计数，5 次失败锁 300s（可配），`/login` 与 `/api/login` 共用；成功后清零；`session.permanent=True` + `PERMANENT_SESSION_LIFETIME=SESSION_TIMEOUT`，`SESSION_TIMEOUT` 从「只读不用」变成真正生效 |
| P1-18 CSRF / Cookie / Nginx CORS | ✅ | `web.py`、`nginx/conf.d/ntrip.conf` | Cookie 加 `HttpOnly` + `SameSite=Lax`（可选 `Secure`），删除 `/api/` 段的 `Access-Control-Allow-Origin "*"` 与整段 OPTIONS 放行 |
| P1-17 前端 innerHTML XSS | ✅ | `static/app.js`、`ntrip.py` | 新增 `escapeHtml()` 并覆盖用户列表、挂载点列表、在线挂载点卡片、STR 表、基站信息；服务端补挂载点名白名单 `^[A-Za-z0-9_-]{1,50}$`（上传/下载两条路径），从源头挡住 `GET /<img onerror=...>` 这类名字 |
| P1-14 弱口令 / 硬编码密钥 / 无 .gitignore | ✅ | `config.py`、`.gitignore`、`database.py` | 新增 `.gitignore`（db、logs、.env、config.ini、密钥、缓存）；`config.ini` 用 `git rm --cached` 移出版本库（本地文件保留），缺失时自动回退 `config.ini.example`；`secret_key` 若仍是仓库里的示例值，启动时生成随机密钥并落盘 `data/.secret_key`（0600）；首次建库打印/记录强口令告警 |
| P0-6 ③ 挂载点口令明文入库 | ✅ | `database.py` | `add_mount` / `update_mount` / `update_mount_password` 一律 PBKDF2 存储，`verify_mount_and_user` 改用 `verify_password`（兼容历史明文行）。实测：新口令 `e2epass123` 可正常 `SOURCE` 推流，错口令 401 |
| P1-12 非常量时间比较 | ✅ | `database.py` | 全部改用 `hmac.compare_digest`，明文回退保留（历史行兼容） |

## 第 4 批：性能重构

| 项 | 状态 | 落点 | 说明 |
|---|---|---|---|
| P1-1 广播持锁发送 | ✅ | `forwarder.py` | 广播线程只做「挂载点切片一次 → 逐客户端入队」，写 socket 交给 **8 个发送线程**（`sender_threads`）；socket 改非阻塞，`send()` 写不完的部分留在 `pending` 里退避重试，任何慢客户端都不会占住线程；队列有块数上限（`client_send_queue_size`）与字节上限（`client_pending_max_bytes`），超限丢最老、保留最新。实测：100 客户端 × 200 块 = 20000 条 **全部完整送达、零丢弃**；灌 500×8KB 时单轮广播仍为 **0.0001s** |
| P1-3 每轮全量扫描 | ✅ | `forwarder.py::RingBuffer` | 新增 `current_index()` / `get_since_index(cursor)`，客户端持游标做切片，复杂度 O(新增块数)，替换 `get_since(timestamp)` 的 60 元素全遍历；进一步改成每个挂载点只切一次、所有客户端共享 |
| P1-4 缓冲按条数而非时长 | ✅ | `forwarder.py`、`config.py` | `RingBuffer` 同时受「`RING_BUFFER_SIZE` 秒时间窗口」与新增 `ring_buffer_max_chunks`（默认 3000）硬上限约束 |
| P1-11 SQLite 串行 + 每次建连 | ✅ | `database.py` | 新增有界连接池 `_ConnectionPool`（`db_pool_size`），17 处 `sqlite3.connect()` / `conn.close()` 全部改为 `_acquire()` / `_release()`；放回池时 `rollback()` 清事务状态，库路径变化自动丢弃旧连接；连接统一开 WAL + `synchronous=NORMAL` + `busy_timeout`。实测 20 线程 × 200 次混合读写无异常、`PRAGMA integrity_check = ok` |
| P1-2 线程模型 → asyncio | ❌ 未做 | — | 属重写级改造（`asyncio.start_server` + `StreamWriter`），建议单独立项：先按现有实现跑一次 2000 连接基线，再迁移。当前 P1-1 已把「慢客户端拖死全场」消除，单机 1 连接 1 线程的内存占用（实测 ~5.3 MB/连接）仍是 5000 上限的硬约束 |

## 顺手修掉的 P1 / P2

| 项 | 落点 | 说明 |
|---|---|---|
| P1-7 只 `recv()` 一次 | `ntrip.py::handle_request` | 循环读到 `\r\n\r\n`，上限 16KB。实测「请求头分两次发送」不再返回 400 |
| P1-8 不校验挂载点在线 / 计数漂移 | `ntrip.py::handle_download` | 离线直接 404；`add_client` 失败时回滚 `remove_user_connection` |
| P1-9 连接计数键位用错 | `ntrip.py`、`forwarder.py`、`connection.py` | 统一按 `connection_id` 精确移除；`connection_id` 加随机后缀（原来秒级时间戳会撞 ID）；`forwarder.remove_client` 加幂等标记，避免多条清理路径重复扣减 |
| P1-10 取流不校验绑定 | `database.py`、`config.py` | 新增 `download_enforce_user_binding` 开关（默认 False = 保持旧行为），开启后校验 `mounts.user_id` |
| P1-19 重启 `os._exit(0)` | `web.py` | 改为 `signal.raise_signal(SIGTERM)` 走正常关闭，失败才回退硬退出 |
| P1-20 多余的 ConnectionManager | `web.py` | 删除 `global rtcm` 实例，统一用单例 |
| P1-21 僵尸清理 fork netstat | `connection.py` | 改为纯应用层 `MOUNT_TIMEOUT` 空闲判定，不再 `subprocess`、不再硬编码 2101 |
| P1-22 环境变量覆盖不生效 | `main.py`、`ntrip.py` | `config.init_config()` 提到 `check_environment()` 之前；NTRIP 端口改为运行时读 `config.NTRIP_PORT`（原来导入期缓存，`NTRIP_PORT` 环境变量对监听端口完全无效，实测已修） |
| P1-23 NTRIP 只监听 0.0.0.0 | `ntrip.py::_start_ntrip_server` | 跟随 `config.NTRIP_HOST` |
| P1-24 依赖不可复现 / 内置库覆盖 | `requirements.txt`、`Dockerfile`、`pyrtcm/` | 锁定 `pyrtcm==1.1.8`、`pyproj==3.7.2`、`reverse-geocode==1.6.6`；删除仓库内置 `pyrtcm/` 副本与 Dockerfile 的 COPY 行，改由 pip 包提供（已验证 `rtcm2` 正常导入） |
| P2 `mounts` 结构不一致 | `main.py` | `get_system_stats` 里同时兼容 dict/list，避免 TypeError |
| P2 `datetime.utcnow()` 弃用 | `ntrip.py` | 改 `datetime.now(timezone.utc)` |
| P2 用字符串判断网络错误 | `forwarder.py` | 改 `isinstance(e, (BrokenPipeError, ConnectionResetError, ...))` |
| P2 死代码 | `connection.py` | `force_refresh_connections` 不再空转计算 `invalid_mounts` |

---

## 实测记录

| 场景 | 结果 |
|---|---|
| `/health`（容器内 127.0.0.1） | `200 {"status":"ok","version":"2.2.0","uptime":...}` |
| 匿名访问 `/api/system/stats`、`/api/str-table`、`/api/mounts/online` | 均 `401`；登录后 `200` |
| 连续 6 次错误登录 | 第 6 次 `429 登录失败次数过多，请 300 秒后再试` |
| NTRIP 2.0 中文 401 | `Content-Length: 21`（原为 6） |
| 非法挂载点 `GET /bad%20name` | `400 Bad Request` |
| 合法但离线挂载点 | `404 Not Found` |
| NTRIP 1.0 `SOURCE <pwd> /MOUNT`（哈希口令） | 正确 → `ICY 200 OK`；错误 → `SOURCETABLE 401 Unauthorized` |
| SIGTERM 期间断开连接 | `NTRIPCaster.stop()` 0.2s 返回（原实现永久阻塞） |
| 请求头分两次到达 | 正常解析（不再 400） |

> Windows 上 `subprocess.send_signal(SIGTERM)` 走 `TerminateProcess`，无法验证进程级优雅退出；该路径在 Linux/容器下才生效，已用 `NTRIPCaster.stop()` 单测覆盖等价逻辑。

---

## 第 5 轮：收尾项（本次补完）

| 项 | 状态 | 落点 | 说明 |
|---|---|---|---|
| 每客户端发送队列 | ✅ | `forwarder.py`、`config.py` | 见上表 P1-1。新增配置：`sender_threads=8`、`client_send_queue_size=200`、`client_pending_max_bytes=1048576` |
| 加入时刻不丢首包 | ✅ | `forwarder.py::add_client` | 记录 `join_index`，客户端首轮从「加入时的写位置」补一次，实测 40 块序列号 `0..39` 连续无重复 |
| SQLite 连接池 | ✅ | `database.py` | 见上表 P1-11；`close_pool()` 供测试/退出清理 |
| 转发器可观测性 | ✅ | `main.py`、`forwarder.py` | `/api/system/stats` 新增 `forwarder` 段：`dropped_bytes`、`failed_sends`、`send_queue_pending`、`sender_threads`；`dropped_bytes` 持续增长即代表有客户端跟不上 |
| `healthcheck.py` 非 Linux 误判 | ✅ | `healthcheck.py::check_memory_usage` | `/proc/meminfo` 不存在时退回 `psutil`；Windows 上实测 4 项检查全过、退出码 0 |
| `config.ini.example` 与实际配置不符 | ✅ | `config.ini.example` | 原文件是 `[NTRIP]/[WEB]` 大写段的旧格式，与 `config.py` 读取的小写段名**完全对不上**（等于示例从未生效过）。已按代码实际读取的 77 个配置项重写，并补上新增项；`log_format` 按 configparser 规则转义为 `%%` |
| compose 合并校验 | ✅ | `docker-compose.prod.yml` | 修 3 处导致 `docker compose config` 失败的问题：`build: null` 非法、`security_opt` 与基础文件合并后重复、卷同时声明 `external` 与 `driver` 冲突；删除过时的 `version` 字段。现在 `-f docker-compose.yml -f docker-compose.prod.yml config` 零告警通过 |
| 测试脚本硬编码 | ✅ | `tests/*.py` | 改读 `NTRIP_WEB_URL`、`NTRIP_ADMIN_USER`、`NTRIP_ADMIN_PASSWORD`、`NTRIP_HOST`、`NTRIP_PORT`、`NTRIP_MOUNTS`、`NTRIP_WEB_PORT`；`tests/test_users.json`（含 500 个测试账号）`git rm --cached` 并已在 `.gitignore` 中 |
| 文档同步 | ✅ | `README.md`、`README-zh.md` | 配置示例更新为新段名与新参数，新增「安全说明」：只支持 Basic、Digest 已停用及原因、口令只可重置不可回读、`/classic` 已跳转、`config.ini` 不再入库、反代需开 `trust_proxy_headers` |
| `.gitattributes` 损坏二进制 | ✅ | `.gitattributes` | 原来只有一条 `* text eol=lf`，导致 Windows 下 checkout 把 PNG/ICO 里的 `0D 0A` 当换行删掉——本次核对时 5 个图片（`img/*.png`、`static/favicon.ico`）各少了 1~4 字节。已补 `*.png binary` 等规则，并把这些文件恢复到版本库原始内容 |

### 本轮实测

| 场景 | 结果 |
|---|---|
| 100 客户端 × 200 块（24B/块） | 100/100 完整收到，20000 条零丢弃，耗时 2.06s |
| 500 块 × 8KB 突发洪泛 + 1 个不读的慢客户端 | 单轮广播 0.0001s；快客户端持续收数；慢客户端积压被限幅（丢弃最老、保留最新 1MB） |
| 40 块序列号连续性 | `0..39` 连续、无重复 |
| 20 线程 × 200 次数据库混合读写 | 0 异常，`integrity_check = ok` |
| 镜像文件集合自检（`main.py`/`healthcheck.py`/`src`/`static`/`templates` + `config.ini.example`） | 正常启动，`/health` 200，`healthcheck.py` 退出码 0 |
| `docker compose config` | base / prod / 两者合并 均通过（无告警） |

> Docker 构建未执行：本机 Docker Desktop 守护进程未运行
> （`open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified`），
> 无法 `docker build`。已用「复制 Dockerfile 实际 COPY 的文件 → 独立目录启动 → 跑 healthcheck.py」等价验证镜像内容完整性。
> 待守护进程可用后执行：`docker build -t ntrip-caster:2.2.0 . && docker compose up -d`。

---

## 第 6 轮：Digest 移除 + 修复后整体复审

本轮前置需求：**不再支持 Digest**，并在此基础上把修复后的代码与框架整体复审一遍。
完整复审报告见 `CODE-REVIEW-ROUND2.md`（22 项新发现，P0 1 / P1 6 / P2 9 / P3 6，全部修复）。

| 项 | 状态 | 落点 | 说明 |
|---|---|---|---|
| Digest 支持移除 | ✅ | `src/ntrip.py`、`tests/test_ntrip_concurrent.py`、`README*.md` | 删除 `_verify_digest_auth` / `_parse_digest_auth` / `_validate_digest_response` 三个方法、4 处认证分支、`send_auth_challenge` 的 nonce 与 `auth_type` 参数。现在恒定只发 `WWW-Authenticate: Basic`；收到 Digest 在 `verify_user` 入口显式拒绝（带按 IP 去重的告警），不再落到"缺密码"分支误导排查 |
| 用户改名清空口令（P0） | ✅ | `src/web.py`、`src/database.py` | 原来把库里的 `salt$hash` 当口令再哈希一次，改名后该用户立即无法登录。`update_user(..., password=None)` 语义改为"保留原口令" |
| 源站重连被延迟清理踢掉 | ✅ | `src/ntrip.py::_receive_rtcm_data` | 1.5s 定时器改为"只清理本次连接遗留的缓冲区对象"，并先判 `is_mount_online()`，不再误删新连接 |
| 重连持锁 join 解析线程 5s | ✅ | `src/connection.py`、`src/rtcm2.py`、`src/rtcm2_manager.py` | `stop()` 先 shutdown 管道写端再 join(2s)；管理器锁内只摘引用；`start_str_correction` 移出 `mount_lock`。实测重连响应 <2s |
| 数据统计翻倍 | ✅ | `src/ntrip.py` | 删除 `_receive_rtcm_data` 里与 `forwarder.upload_data` 重复的 `update_mount_data_stats` |
| `upload_data` 缓冲 KeyError | ✅ | `src/forwarder.py` | 判断与 append 合并进同一把锁，缺失时现场创建 |
| 解析线程反压源站 | ✅ | `src/forwarder.py` | 订阅端写端设非阻塞；写满丢当前块并计入 `subscriber_dropped_bytes`，不阻塞源站 |
| 连接池并发无上限 | ✅ | `src/database.py` | 加 `BoundedSemaphore(DB_POOL_SIZE)`；名额用尽开临时连接（归还即关），不阻塞也不膨胀 |
| 注册失败仍回 200 | ✅ | `src/ntrip.py::handle_upload` | 异常分支改回 500 并断连，避免"源站以为在推、管理页看不到" |
| 僵尸下载连接 | ✅ | `src/ntrip.py::_keep_connection_alive` | 每 5s `recv(1, MSG_PEEK)` 探测对端 FIN，不再只依赖 `SO_ERROR` |
| `online_mounts` 无锁访问 | ✅ | `src/connection.py` | 4 处读写补 `mount_lock` |
| 带宽字段恒为 0 | ✅ | `main.py` | 后端改返回 `{sent_rate, recv_rate, ...}`（bytes/s），与前端 `formatBytes()` 对齐 |
| RingBuffer 增量读取 O(n) | ✅ | `src/forwarder.py::get_since_index` | 改从尾部反向取增量，不再每轮全量拷贝 3000 块 |
| `_flush_client` O(n²) | ✅ | `src/forwarder.py` | `memoryview` + 偏移量写出，最后一次性裁剪积压 |
| `healthcheck.py` 走代理 / 宿主内存口径 | ✅ | `healthcheck.py` | 探活显式绕过 `HTTP(S)_PROXY`（实测原实现直接 502）；内存优先读 cgroup v2/v1 限额 |
| 静态资源缓存一年 | ✅ | `nginx/conf.d/ntrip.conf`、`templates/spa.html` | 缓存降到 1h + must-revalidate；`app.js` 加 `?v={{ app_version }}` |
| Dockerfile | ✅ | `Dockerfile` | `setuptools>=...` 加引号（原来被 shell 当重定向）；补默认 `DATABASE_PATH/LOG_DIR/DATA_DIR`，镜像单跑也能落进卷 |
| socket.io 重复引入 | ✅ | `templates/spa.html` | 去掉覆盖用的 4.0.0，只留与 python-socketio 5.8 匹配的 4.0.1 |
| 调试接口越权面 | ✅ | `src/web.py` | `/api/mount/initialize` 加名称白名单 + 仅 DEBUG 开放（避免幽灵在线挂载点）；`/api/mount/<m>/simulate` 不再假成功，改 501 |
| 前端插值转义 | ✅ | `static/app.js` | 星座名、卫星名、通道统一 `escapeHtml()` |
| 端到端回归 | ✅ 新增 | `tests/test_smoke.py` | 自带临时实例（临时库/目录 + 独立端口），10 组用例 37 项断言，覆盖 Digest 拒绝、统计口径、重连竞态、NTRIP2.0 裸流、单用户连接上限、60 路并发、改名保口令 |

### 本轮实测

| 场景 | 结果 |
|---|---|
| `tests/test_smoke.py` 全量 | **37 通过 / 0 失败** |
| 60 路并发（独立账号）× 150 块 | 60/60 收齐且序号连续，`dropped_bytes=0` |
| 源站闪断 0.2s 后重连 | 响应 <2s；旧定时器触发后仍在线；新客户端可收数 |
| 推 12800 字节后的挂载点统计 | `total_bytes` 与实发一致（不再翻倍） |
| 200 线程 × 30 次鉴权 | 0 异常，连接池名额无泄漏 |
| `healthcheck.py --json`（真实实例） | 4 项全过，退出码 0 |
| `py_compile` / `node --check` / `pyflakes` | 通过 / 通过 / 无 undefined name |
| `docker compose config`（base、prod、合并） | 均通过 |

> `docker build` 仍未执行（本机 Docker Desktop 守护进程未启动）。Dockerfile 改动为 `RUN` 引号修正与环境变量默认值，已按"复制 COPY 的文件集合启动 + 跑 healthcheck"等价验证。

---

## 第 7 轮：前后端接口契约审计（写入是否真的落库）

目标：核对页面上的"新增 / 保存 / 删除"是否真的写进了数据库，以及其余字段/路径是否对得上。
完整报告见 `AUDIT-FRONTEND-BACKEND.md`（10 项问题，全部修复）。

| 项 | 状态 | 落点 | 说明 |
|---|---|---|---|
| 改名+改口令时口令被丢弃 | ✅ | `src/web.py::api_user_detail` | 原来 `if 用户名 / elif 口令`，同时提交时口令永不生效还提示改名成功；改为一次 `update_user(id, new_name or old, pwd or None)` |
| 新增挂载点忽略 `username` | ✅ | `src/web.py::api_mounts` | 只认 `user_id`，传 `username` 返回 201 但 `user_id` 为 NULL → 挂载点等于对全部 NTRIP 2.0 用户开放；现支持两种写法 |
| 改在线挂载点口令后源站继续推流 | ✅ | `src/web.py::api_mount_detail` | `force_disconnect_mount` 只踢下载端；改为先 `remove_mount_connection`（关源站 socket）再清缓冲区/客户端 |
| 改名后在线表与 STR 表残留旧名 | ✅ | 同上 | 内存 key 未跟随；现改名后清理旧条目 |
| 设置页硬编码 `/api/users/admin` | ✅ | `src/web.py` 新增 `PUT /api/admin/password`、`static/app.js` | `[admin] username` 改成别的名字后管理员无法在界面改口令；现以会话内管理员名为准 |
| 可创建与管理员同名的普通用户 | ✅ | `src/web.py` | `admins`/`users` 两张表互不影响；建出来的同名"用户"点编辑会改到真正管理员的口令；现新增与改名都禁止 |
| `update_admin_password` 不看 rowcount | ✅ | `src/database.py` | 账号不存在也返回成功；现返回 False |
| 编辑挂载点无条件提交 `username` | ✅ | `static/app.js::editMount/submitEditMount` | 反查 `/api/users` 失败时会把绑定清空；改用 `/api/mounts` 自带字段 + 仅变化时提交 |
| 登录后 `?page=` 无人消费 | ✅ | `static/app.js` | 被拦去登录后总是回 dashboard；现读取参数并跳转 |
| 改口令缺字符校验 | ✅ | `src/web.py` | 与新增用户统一为 `^[a-zA-Z0-9_-]+$` |
| 写入一致性回归 | ✅ 新增 | `tests/test_web_crud.py` | 直接查 SQLite 核对，4 组 64 项断言 |

### 本轮实测

| 场景 | 结果 |
|---|---|
| `tests/test_web_crud.py` | **64 通过 / 0 失败** |
| `tests/test_smoke.py` | **37 通过 / 0 失败** |
| 改名+改口令同一次提交 | 用户名与新口令均落库，返回"用户名从 a 更新为 b；口令已更新" |
| 在线挂载点改口令 | 旧口令源站被 socket 断开，在线表/STR 表不再残留 |
| 在线挂载点改名 | 库内新名，在线表与 STR 表旧名消失 |
| `[admin] username = boss` 场景 | 设置页改口令成功（旧实现必然失败） |
| 与管理员同名的用户 | 返回 400，`users` 表无新增行 |

> 注：`config.ini` 目前**没有任何写入口**，只能在文件里改；若需要"页面改配置并落盘"，
> 得新增配置写回服务（原子替换 + 热加载），本轮未实现。

---

## 第 8 轮：前端 UI 重设计 + 数据链路审计

完整说明见 `UI-REDESIGN.md`。风格基准为同作者的 `Rampump/2RTKserver`（gitcode: `NTRIPserver`）。

### UI

| 项 | 状态 | 落点 | 说明 |
|---|---|---|---|
| 设计系统统一 | ✅ | `static/theme.css`（新增） | 直接沿用基准项目的令牌（#f4f6fa 底 / #2563eb 主色 / 14px 圆角 / 卡片顶部蓝青细线 / Orbitron 品牌字） |
| 删除重复样式 | ✅ | `templates/spa.html`、`static/app.js` | 移除模板里 1563 行 + 脚本里 161 行内联样式，样式只剩一个来源 |
| 顶栏改成 sticky 玻璃栏 | ✅ | `templates/spa.html` | 品牌 + 胶囊导航 + 右侧实时通道状态灯，与基准同构 |
| 登录页 | ✅ | `templates/login.html` | 居中 auth-card，保留原表单 id 与校验逻辑 |
| 图标字体版本 | ✅ | 两个模板 | 引入 FA 4.7 但代码用 FA5 的 `fas` → 全站图标变空方块；改 5.15.4，并加"字体加载失败就隐藏图标"的降级 |
| 连接状态指示器失效 | ✅ | `static/app.js` | 原绑 `#connection-status`（页面无此元素）；改绑 `#realtime-status`/`#realtime-dot` |
| 弹窗输入框过窄 | ✅ | `static/app.js` | 10 个 `<input>` 缺 `form-control`，批量补齐；补 `:disabled` 样式 |
| 设置页按钮看不清 | ✅ | `static/app.js` | 去掉橙底橙字的内联样式，改用 `.btn-warning` |

### 数据链路

| 项 | 状态 | 落点 | 说明 |
|---|---|---|---|
| 上下线推送延迟 3s | ✅ | `src/web.py::_push_data_loop` | 改双档：0.5s 比对轻量签名，变化即推；每 3s 全量推统计/STR。实测上线 ≤1.5s、下线 ≤2.5s |
| 推送每轮全量深拷贝 | ✅ | `src/connection.py::get_online_signature` | 新增只读名字的签名接口，3000 挂载点下不再每 0.5s 构造 3000 个 dict |
| "在线用户"实为连接数 | ✅ | `src/connection.py::get_statistics` | 拆成 `total_users`（账号）/ `total_connections`（连接） |
| 前端自己数 length 猜口径 | ✅ | `main.py`、`static/app.js` | `/api/system/stats` 新增 `counts`，前端优先用 |
| 统计整份 10s 缓存 | ✅ | `main.py::get_system_stats` | 运行时长/NTRIP 计数/在线列表实时取，只有 psutil 走缓存 |
| `cpu_percent(interval=1)` 阻塞请求线程 | ✅ | `main.py` | 改 `interval=None` + 启动打底，实测首屏不再卡 1s |
| 统计线程先睡后采 | ✅ | `main.py::_stats_monitor_worker` | 改为先采再睡 |
| Max Connections 恒 0 | ✅ | `main.py` | 读了不存在的 `max_concurrent`，改 `max_connections` |
| 每客户端已发送字节膨胀 | ✅ | `src/forwarder.py` | 把累计值当增量反复累加，5KB 的流显示 180KB；改为传增量 |
| 速率是"自连上以来的平均值" | ✅ | `src/connection.py` | 改 10 秒滑动窗口实时速率 |
| `active_connections` 计数漂移 | ✅ | `src/ntrip.py::_connection_handler` | submit 之后才 +1，短连接先 -1 → 永久负数；改为先计数后提交 |
| 下线不清理连接计数 | ✅ | `src/connection.py::remove_mount_connection` | 一并清 `mount_connection_count` 与速率样本 |
| 计数可能为负 | ✅ | `src/connection.py` | 用户/挂载点计数下限钳到 0 |
| 计数读取无锁 | ✅ | `src/connection.py` | 两个 getter 补锁 |
| 推送与统计回归 | ✅ 新增 | `tests/test_stats_push.py` | 真实 Socket.IO 客户端监听并计时，6 组 21 项断言 |

### 本轮实测

| 场景 | 结果 |
|---|---|
| `tests/test_stats_push.py` | **21 通过 / 0 失败** |
| `tests/test_smoke.py` | 37 通过 / 0 失败 |
| `tests/test_web_crud.py` | 64 通过 / 0 失败 |
| 挂载点上线/下线推送 | ≤1.5s / ≤2.5s |
| 用户上线/下线推送 | ≤1.5s / ≤3s |
| 同一账号两条连接 | 账号数 1、连接数 2（原实现显示 2/2） |
| 25 帧 × 64B | 挂载点 `total_bytes` = 1600，与实际一致；`data_rate` ≈ 640 B/s（10Hz × 64B） |
| 统计推送节奏 | 相邻 3.0s，Δuptime 与 Δt 差 <0.1s（原为 10s 跳变） |

---

## 第 9 轮：中英双语 + 打赏入口

目标：把 UI 做成 2RTKserver 那样的中英切换（默认跟随浏览器语言），并加一个"支持作者"入口，
展示支付宝 / 微信收款码。

### 改动清单

| 项 | 状态 | 落点 | 说明 |
|---|---|---|---|
| 前端国际化 | ✅ 新增 | `static/i18n.js` | 203 个键 × 2 种语言，`t(key, vars)` 插值；缺键回落键名并在控制台告警一次 |
| 语言选择持久化 | ✅ | `static/i18n.js` | localStorage 记住选择；`?lang=zh\|en` 可强制覆盖（便于截图/客服排查） |
| 静态标记 | ✅ | `templates/spa.html`、`templates/login.html` | `data-i18n` / `-ph` / `-title` / `-aria` 四类，切换时统一刷新 |
| 动态文案 | ✅ | `static/app.js` | 所有页面模板、表单、确认/提示弹窗、卫星 tooltip 一律走 `t()`，无硬编码英文 |
| 切换后重绘 | ✅ | `static/app.js`（监听 `i18n:changed`） | 重建当前页 + 状态灯；延后一个任务执行，避免被 i18n.js 的 `applyI18n()` 覆盖状态文案 |
| 后端中文提示英文化 | ✅ | `static/i18n.js::localizeServerMessage` | 后端 `src/web.py` 的提示语是中文硬编码，这里用 33 条正则映射收敛；zh 模式把少量英文串翻回中文 |
| 未授权判定 | ✅ | `static/app.js::handleApiResponse` | 原来靠比对 `'Unauthorized access'` 字符串，文案一翻译就失效；改为 `error.isAuthError` 标记 |
| 弹窗 XSS | ✅ | `static/app.js::showAlert/showConfirmDialog` | `message`/`title` 原样拼进 innerHTML，用户名可注入；补 `escapeHtml` |
| 打赏入口 | ✅ 新增 | 顶栏 `nav-donate` + 页脚 + 登录页 | 三处入口都调 `openDonateModal()`（登录页用同名独立实现，不加载 app.js） |
| 收款码资源 | ✅ 新增 | `static/img/donate-alipay.jpg` / `donate-wechat.jpg` | 122 KB / 138 KB，Dockerfile 整体 COPY `static/`，不依赖外网 |
| 样式 | ✅ | `static/theme.css` | `.lang-switch/.lang-btn`（胶囊分段）、`.donate-*`（两列卡片，≤560px 单列）、`.modal-close` |

### 校验

| 项 | 结果 |
|---|---|
| 字典一致性 | en/zh 各 203 键，无缺失、无死键（脚本比对 `t('…')` / `data-i18n` 与字典） |
| `node --check` | `app.js`、`i18n.js` 通过 |
| 登录页 | 中/英切换正常，标题 `登录 · 2RTK NTRIP Caster` / `Sign in · …` 跟随切换 |
| 总览/用户/挂载点/实时数据/设置 | 双语渲染正常，切换后页面即时重建 |
| 后端提示 | EN 模式下"该用户名已被管理员账号占用"→ `That username is reserved for the administrator account` |
| 打赏弹窗 | 两码并排显示、点击可看原图；登录页与 SPA 均可打开 |
| 回归 | `test_smoke.py` 37/37、`test_web_crud.py` 64/64、`test_stats_push.py` 21/21 |

### 注意

- `donate-alipay.jpg` = 用户提供的第 1 张（weipay.jpeg），`donate-wechat.jpg` = 第 2 张（weixin.jpeg）。
  若两张图放反了，直接对调这两个文件名即可，代码无需改动。

---

## 仍未处理

1. **P1-2 线程模型 → asyncio**：重写级改造，建议单独立项（先跑 2000 连接基线再迁移）。这是目前唯一没动的大项。
2. **`data_send_timeout` / `client_health_check_interval`**：非阻塞发送后这两个参数的实际作用已弱化（TCP 失联靠 Keep-Alive 与写错误检测），文档里保留但未再作为主要判定依据。
3. **Nginx `/health`**：仍只返回 nginx 自身的 200，不检查上游 Flask；如需端到端探活，可改成 `proxy_pass` 到应用 `/health`。
4. **前端外部 CDN 依赖**：socket.io、OpenLayers（JS+CSS）、Font Awesome、Orbitron 字体都从公网 CDN 加载。图标已做"加载失败就隐藏"的降级，但地图会退化成占位框。彻底解决需把这几项 vendor 到 `static/`。
4.1 **界面语言**：✅ 已解决（第 9 轮），前端全量中英双语 + 语言切换。
5. **全局 `db_lock`**：仍串行化所有鉴权（实测 46k 次/s，够用）；要更高并发可只对写加锁。
6. **PBKDF2 迭代 10000 偏低**：建议提到 200k 并引入 `pbkdf2$iter$salt$hash` 格式，登录成功时惰性升级；`PASSWORD_HASH_ROUNDS` 配置项目前未被使用。
7. **配置项冗余**：`MAX_CONNECTIONS_PER_USER`(3) 与 `MAX_USERS_PER_MOUNT`(3000) 语义重叠，`max_user_connections_per_mount` 实际未被读取。
8. **代码清理**：`src/` 内仍有约 30 处未使用 import/变量，`web.py` 有大段注释掉的 print 调试代码（pyflakes 已确认无 undefined name，纯可读性问题）。
