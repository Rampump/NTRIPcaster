# 修复后整体复审报告（第 2 轮）

审查对象：`NTRIPcaster` 修复后代码（第 1~5 轮改动落地后的状态）
审查方式：全量通读 `src/`（7 个模块 ≈ 6400 行）+ `main.py` + `healthcheck.py` + 部署文件（Dockerfile / compose / nginx）+ 前端关键路径，再以自建端到端测试 `tests/test_smoke.py`（37 项断言）验证真实行为。
审查日期：2026-10-28

---

## 一、结论摘要

| 维度 | 上一轮评价 | 本轮评价 |
|---|---|---|
| 可用性 | 健康检查、数据卷、优雅关闭已修好 | 好。另修掉 3 个会**长期隐性发作**的功能缺陷（改名清口令、重连被延迟清理踢掉、带宽字段恒为 0） |
| 协议正确性 | 分帧、响应头、NTRIP2.0 裸流已修 | 好。Digest 死代码全部移除，认证只剩 Basic 一条明确路径 |
| 并发/性能 | 广播与慢客户端隔离已修 | 中上。修掉「解析线程反压源站」「重连持锁 5 秒」「连接池无上限」三处；**线程模型仍是每连接一线程** |
| 安全 | 鉴权默认拒绝、XSS、限流已修 | 好。补了调试接口的越权面（幽灵挂载点）与 3 处前端插值转义 |
| 可运维性 | 配置示例、compose 已修 | 中上。修了镜像单跑写不进卷、健康检查走代理、前端缓存一年不更新 |

本轮**新发现 22 项**，其中 P0 1 项、P1 6 项、P2 9 项、P3 6 项，**全部已修复并验证**。
仍有一项大工程未做：**P1-2 线程模型 → asyncio**（重写级，见第五节）。

---

## 二、已修复问题清单

### P0（功能性阻断）

**R2-01 用户改名会把口令改成「哈希的哈希」，改名后无法登录**
- 症状：管理页改用户名 → 该用户的 NTRIP 口令立即失效，但界面提示"用户名更新成功"。
- 根因：`web.py` 把库里的 `salt$hash` 当"当前口令"传给 `update_user()`；`update_user()` 里 `verify_password(哈希, 哈希)` 必然为 False，于是走 `hash_password(哈希串)`，把口令替换成哈希值的哈希。
- 修复：`update_user(user_id, username, password=None)`，`None` 语义 = 只改名字、保留原口令；调用方传 `None`。
- 验证：用例 10 —— 改名后 `verify_password(库内哈希, 原口令)` 仍为 True。

### P1（数据/可用性/资源）

**R2-02 源站断开后的 1.5s 延迟清理会误删重连后的新连接**
- 症状：源站闪断重连后，管理页显示挂载点离线、订阅端断流，但数据其实还在推。
- 根因：`_receive_rtcm_data` 的 `finally` 里起 1.5s 定时器，回调里无条件 `remove_mount_buffer(mount)` + `remove_mount_connection(mount)`，而这两个都是**按名字**操作，会把新连接创建的状态一起删掉。
- 修复：定时器启动时记下当时的 `RingBuffer` 对象，回调只删"还是同一个对象"的缓冲；并先 `is_mount_online()` 判断，已重连则跳过在线记录清理。
- 验证：用例 7 —— 断开 0.2s 后重连、等 2.2s（旧定时器已触发），挂载点仍在线且新客户端能收数。

**R2-03 源站重连会持锁等待解析线程 join（最长 5s），期间全站鉴权阻塞**
- 症状：源站重连瞬间，其它客户端的取流鉴权、`/api/mounts`、SOURCETABLE 全部变慢，最坏 5 秒。
- 根因：`add_mount_connection` 在 `mount_lock` 内调用 `start_str_correction` → `rtcm2_manager.start_parser` → `stop_parser` → `RTCMParserThread.stop()` → `join(timeout=5)`；解析线程只在循环顶部检查 `running`，正阻塞在 pipe 读上，必须等满读超时。
- 修复：三层拆解 —— ① `stop()` 先 `pipe_w.shutdown(SHUT_WR)` 让读端立刻 EOF，`join` 上限降到 2s；② `rtcm2_manager` 只在锁内摘引用，`parser.stop()` 放到锁外；③ `add_mount_connection` 把 `start_str_correction` 移出 `mount_lock`。
- 验证：用例 7 —— 重连响应耗时断言 `< 2000ms` 通过（修复前实测约 5s 量级）。

**R2-04 同一块数据被统计两次，`total_bytes` / `data_rate` 翻倍**
- 根因：`forwarder.upload_data()` 内部调 `connection.update_mount_data_stats()`，`_receive_rtcm_data` 每收到一块又调一次。
- 修复：删除 `_receive_rtcm_data` 里的重复调用（统计下沉到转发器，任何调用 `upload_data` 的路径都能覆盖）。
- 验证：用例 5 —— 推 12800 字节后挂载点 `total_bytes ∈ [12800, 12800+4×块]`。

**R2-05 `upload_data` 的 "判断—写入" 分离，并发删缓冲时 KeyError 打断源站接收**
- 根因：`if mount not in self.mount_buffers: create_mount_buffer()` 之后在另一把锁里 `self.mount_buffers[mount].append(...)`；期间 `force_disconnect_mount` 可能已删除该 key。
- 修复：合并到一次 `with self.buffer_lock`，`get` 不到就现场创建。
- 验证：用例 5/9 数据流正常；`force_disconnect_mount` 并发场景不再抛 KeyError。

**R2-06 RTCM 解析订阅端用阻塞 send，解析慢会反压源站**
- 症状：解析线程（Python 解析 RTCM 相对慢）跟不上时，管道写满 → 源站上传线程阻塞在 `send()` → 源站 TCP 窗口关闭 → **该挂载点全部订阅端断流**。
- 修复：`register_subscriber` 把写端设为非阻塞；`_send_to_subscribers` 遇 `BlockingIOError` 丢当前块并计入 `subscriber_dropped_bytes`，不再阻塞、也不把订阅者判死。
- 验证：用例 5/6/9 —— STR 修正解析正常启动，推流/取流不受影响。

**R2-07 SQLite 连接池"有界"名不副实，连接风暴可开上千条连接**
- 根因：容量只限制**空闲队列**长度，`acquire()` 在队列空时无条件 `sqlite3.connect()`；每条连接默认带 2MB 页缓存，2000 并发首次鉴权会瞬间放大内存。
- 修复：用 `BoundedSemaphore(DB_POOL_SIZE)` 限制并发借出；1 秒拿不到名额则开一条**临时连接**（归还即关闭），保证不阻塞也不膨胀；重复归还只告警不抛错；每条路径都保证名额释放。
- 验证：200 线程 × 30 次鉴权，0 异常，`semaphore` 归位到 10，无残留临时连接；端到端 60 并发用例通过。

### P2（正确性/可运维性）

| 编号 | 问题 | 修复 |
|---|---|---|
| R2-08 | `add_mount_connection` 抛异常时仍回 `ICY 200 OK` 开始收流（静默失败：管理页看不到、订阅端拿不到） | 异常分支改为回 500 并关闭连接 |
| R2-09 | 下载保活只 `getsockopt(SO_ERROR)`，检测不到对端 FIN；无数据下发时僵尸连接永久占住一个工作线程 | 每 5s 追加 `recv(1, MSG_PEEK)`：`b''` 即对端已关，`BlockingIOError` 属正常 |
| R2-10 | `online_mounts` 多处无锁读写（`update_mount_data_stats`、`get_mount_info`、`get_mount_statistics`、`get_mount_str_data`） | 统一加 `mount_lock`；`update_mount_data_stats` 改为 `get()` 判空 |
| R2-11 | `/api/system/stats` 的 `network_bandwidth.sent_rate/recv_rate` 恒为 0（后端产出字符串，前端按 dict 取） | 后端改返回 `{sent_rate, recv_rate, sent_mbps, recv_mbps, display}`，单位 bytes/s |
| R2-12 | `RingBuffer.get_since_index` 每 10ms 把 3000 块的 deque 全量拷一遍 | 改为从尾部 `islice(reversed(...), n)` 取增量，复杂度只与新增块数相关 |
| R2-14 | `healthcheck.py` 在容器里走 `HTTP(S)_PROXY` 探 `127.0.0.1`（实测本机直接 502）；且用宿主机 `/proc/meminfo` 判断容器健康（宿主吃紧时容器被误判 unhealthy，连带 `depends_on: service_healthy` 的服务起不来） | 探活显式 `ProxyHandler({})`；内存优先读 cgroup v2/v1 限额，读不到才回退宿主机口径；实测真实实例退出码 0 |
| R2-15 | nginx 对静态资源 `expires 1y; Cache-Control immutable`，而 `app.js` 文件名无内容指纹 → 升级后浏览器一年不取新代码 | nginx 降到 `1h + must-revalidate`；`spa.html` 改为 `/static/app.js?v={{ app_version }}` |
| R2-17 | 镜像不经 compose 单独运行时，`DATABASE_PATH` 默认 `2rtk.db` 落在 `/app`（卷外），重建即丢库 | Dockerfile 增加默认 `DATABASE_PATH=/app/data/2rtk.db`、`LOG_DIR=/app/logs`、`DATA_DIR=/app/data` |
| R2-19 | `/api/mount/initialize` 可不校验名称就登记"在线"挂载点；该接口不接真实源站，会在 SOURCETABLE 里造出永远没数据的幽灵挂载点 | 加挂载点白名单校验；并且只在 `debug_mode=true` 时开放（生产 403）。同时 `/api/mount/<m>/simulate` 原来只打日志却返回 success，改为 501 明确不支持 |

### P3（清理/防御）

| 编号 | 问题 | 修复 |
|---|---|---|
| R2-13 | `_flush_client` 每写一次就 `del pending[:sent]`，1MB 积压时退化成 O(n²) 内存搬移 | 改 `memoryview` + 偏移量，最后一次性裁剪（注意先 `release()` 再改 bytearray） |
| R2-16 | Dockerfile `pip install setuptools>=78.1.1` 未加引号，shell 把 `>` 当重定向（等价于装无版本约束的 setuptools，还生成 `=78.1.1` 垃圾文件） | 两处均加引号 |
| R2-18 | `spa.html` 引入两份 socket.io 客户端（4.0.1 + 4.0.0），后者覆盖前者 | 只保留与 python-socketio 5.8 / Engine.IO v4 匹配的 4.0.1 |
| R2-20 | `get_online_users()` 直接返回内部连接表（含 `client_socket` 键），一旦非 None 会让整条 `online_users_update` 序列化失败 | 返回时剔除 socket 字段 |
| R2-21 | 前端 3 处插值未转义（星座名、卫星名、通道） | 统一走 `escapeHtml()` |
| R2-22 | Digest 残留死代码：`_verify_digest_auth` / `_parse_digest_auth` / `_validate_digest_response` 三个方法、4 处认证分支、`send_auth_challenge` 里的 nonce 生成与 `auth_type` 参数 | **全部删除**。认证挑战恒定只发 `WWW-Authenticate: Basic`；收到 Digest 请求在 `verify_user` 入口显式拒绝并提示改用 Basic（带防刷屏日志）。README 中英文说明同步 |

---

## 三、Digest 移除后的认证行为

| 客户端行为 | 结果 |
|---|---|
| 首次无凭据请求 | `401` + `WWW-Authenticate: Basic realm="NTRIP"`（不再带 Digest/nonce） |
| 带 Basic 凭据 | 正常鉴权；NTRIP 1.0 校验挂载点口令，2.0 校验用户口令 + 绑定关系 |
| 仍配置 Digest | 直接 `401`（`Digest authentication is not supported, use Basic`），日志按 IP 去重告警；不会落到"缺密码"分支误导排查 |
| 请求头里带 Digest 凭据 | 日志脱敏仍生效（`Authorization: Digest [REDACTED]`），不会写进日志文件 |

---

## 四、验证证据

`tests/test_smoke.py`（新增，自带临时实例：临时目录 + 临时库 + 独立端口 21011/25757，不依赖外部服务）：

```
[1] 健康检查与鉴权边界 ....... /health 200、未登录 /api/* 401、/api/app_info 白名单、管理员登录
[2] 创建用户与挂载点 ......... 通过 API 建用户 + 绑定挂载点
[3] Digest 认证必须被拒绝 ..... 401、只宣告 Basic、不再宣告 Digest
[4] SOURCETABLE .............. 200 + CAS/NET 行
[5] 推流/取流完整性与统计口径 . 字节数精确、帧载荷未破坏、序号 0..199 连续、total_bytes 不翻倍
[6] NTRIP 2.0 取流 ........... 200、Ntrip-Version、无 chunked、裸流帧完整
[7] 断线重连竞态 ............. 重连 <2s、2.2s 后仍在线、新客户端可收数
[8] 单用户连接数限制 ......... 前 3 条允许、第 4 条 401
[9] 并发客户端 ............... 60 路（每路独立账号）全部收齐且有序、dropped_bytes=0
[10] 用户改名保留口令 ........ 改名后原口令仍可校验通过

结果: 37 通过 / 0 失败
```

其它验证：

| 项目 | 结果 |
|---|---|
| `python -m py_compile src/*.py main.py healthcheck.py tests/*.py` | 通过 |
| `node --check static/app.js` | 通过 |
| `pyflakes src/*.py main.py healthcheck.py` | 无 undefined name（仅有未使用 import / 变量，见第五节） |
| `healthcheck.py --json` 对真实实例 | 4 项全过，退出码 0 |
| `docker compose -f docker-compose.yml config` / `+ .prod.yml` | 均通过 |
| 配置一致性（代码读取 77 项 vs `config.ini.example`） | 77/77 齐备，示例中无废弃项 |
| 连接池压测 200 线程 × 30 次 | 0 异常、名额无泄漏、无残留临时连接 |

---

## 五、未处理 / 建议单独立项

| 编号 | 事项 | 说明与建议 |
|---|---|---|
| **P1-2** | **线程模型：每连接一线程** | `ThreadPoolExecutor(max_workers=5000)`，下载连接在整个生命周期内占住一个 worker（`_keep_connection_alive`）。2000 连接 ≈ 2000 线程，GIL 上下文切换是主要开销，Linux 下每线程 8MB 虚拟栈。建议迁移到 `asyncio`（`asyncio.start_server` + 每连接协程 + 单一广播任务），迁移前先跑 2000 连接基线留档 |
| P2-a | 前端依赖 3 个外部 CDN（socket.io、OpenLayers JS+CSS） | 内网/无外网部署时管理页空白。建议把 `socket.io.js`、`ol.js`、`ol.css` vendor 到 `static/`（约 1.2MB），或提供本地 fallback |
| P2-b | 全局 `db_lock` 仍串行化所有鉴权 | 实测 46k 次鉴权/s，2000 连接场景够用；若后续要更高并发，可只对写操作加锁（WAL 下读写不互斥） |
| P2-c | `PBKDF2` 迭代 10000 偏低；`PASSWORD_HASH_ROUNDS` 配置项未被使用 | 建议提到 200k 并引入 `pbkdf2$iter$salt$hash` 格式，登录成功时惰性升级 |
| P2-d | `MAX_CONNECTIONS_PER_USER`(3) 与 `MAX_USERS_PER_MOUNT`(3000) 语义重叠，`max_user_connections_per_mount` 实际未被读取 | 建议删掉未使用的配置项，或在 `add_client` 里真正用上 |
| P3-a | nginx `limit_conn conn_limit_per_ip 10` | 对 NAT 后的办公网偏紧（多标签页 + WebSocket 就占 2~3 条），建议 30~50 或按路径区分 |
| P3-b | `src/` 内仍有未使用 import / 局部变量（pyflakes 约 30 条） | 纯清理项，风险低收益也低，可随下次功能改动一并处理 |
| P3-c | `web.py` 中大段被注释掉的 `print` 调试代码 | 建议清理，降低阅读成本 |

---

## 六、一句话总结

修复后的代码在**功能正确性、协议分帧、鉴权边界**上已经可以上线；本轮又把几个"不报错但会慢慢坏事"的坑（改名清口令、重连被踢、统计翻倍、解析反压、连接池膨胀）一并堵上，并补了一套可重复执行的端到端回归（`tests/test_smoke.py`，37 项）。
唯一还欠的是**线程模型**这件重写级改造，建议单独立项、先用 2000 连接压测留基线，再动 asyncio。
