# 前端 UI 重设计 + 前后端数据链路审计

日期：2026-10-28
范围：`templates/spa.html`、`templates/login.html`、`static/theme.css`（新增）、`static/app.js`、
`src/connection.py`、`src/forwarder.py`、`src/ntrip.py`、`src/web.py`、`main.py`、`tests/test_stats_push.py`（新增）

---

## 一、UI 重设计：对齐 2RTKserver / NTRIPserver

风格基准取自同一作者的 `Rampump/2RTKserver`（gitcode 上的 `NTRIPserver`）仓库的
`static/theme.css` 与 `templates/index.html`，直接沿用它的设计令牌与组件语义，
而不是"看着像蓝色"地另做一套。

### 1. 设计令牌（与基准一致）

| 类别 | 值 |
|---|---|
| 页面底色 | `#f4f6fa` + 顶部一层极淡径向蓝光 |
| 表面 / 次级面 | `#ffffff` / `#f7f9fc` / `#eef2f8` |
| 描边 | `#e3e8f0`（弱）/ `#ccd6e4`（强） |
| 文字 | `#0f172a` / `#334155` / `#64748b` / `#94a3b8` |
| 强调色 | `#2563eb`（hover `#1d4ed8`，浅底 `#eef4ff`），辅色 `#0d9488` |
| 语义色 | 成功 `#0e9f6e`、警告 `#c2760a`、危险 `#dc2626` |
| 圆角 / 阴影 | 14px / 10px；两级极浅阴影 |
| 字体 | 系统栈（含 PingFang / 微软雅黑）+ `ui-monospace`；品牌名 Orbitron |

### 2. 结构变化

- 原来的 `<style>` 内联样式有 **1563 行**、`app.js` 里还有 **161 行**内联样式，
  两套颜色（紫色渐变 + 半透明白卡）互相覆盖。现在全部移除，只保留
  `static/theme.css` 一个来源。
- 顶栏由"大标题 + 一排导航"改为基准项目的 **sticky 玻璃顶栏**：
  品牌（图标 + `2RTK NTRIP` + `Caster Console`）/ 胶囊导航 / 右侧实时通道状态灯。
- 页面容器统一 `max-width: 1180px`，卡片白底 + 顶部 2px 蓝青渐变细线（基准项目唯一保留的"科技感"装饰）。
- 登录页改为居中 `auth-card`，与基准的登录/改密页同构。
- 数字卡、键值表、数据表、按钮、表单、弹窗、日志终端全部按基准的组件规范重写；
  `app.js` 里的历史类名（`.dashboard-card` `.stat-item` `.card-content` `.info-row` 等）
  在 `theme.css` 里做了映射，**不需要重写三千行渲染代码**也能整体换肤。

### 3. 顺手修掉的界面问题

| 问题 | 影响 | 处理 |
|---|---|---|
| 引入 Font Awesome **4.7**，而代码用的是 FA5 的 `fas` 类 | 全站图标渲染成空方块（tofu） | 换成 FA 5.15.4；并在字体未加载（内网/离线）时给 `<html>` 加 `.no-icons`，隐藏图标而不是显示方块 |
| 顶栏"连接状态"写在 `#connection-status`，页面上根本没有这个元素 | WebSocket 断线时界面毫无提示 | 改绑 `#realtime-status` / `#realtime-dot`，断线转红并停止呼吸动画 |
| 设置页把按钮写成 `style="background:#f39c12"` 且文字是警告色 | 橙底橙字，几乎看不清 | 去掉内联样式，改用 `.btn-warning` 语义样式 |
| 弹窗里的 `<input>` 没有 `form-control` | 输入框只有默认 180px 宽，和卡片不协调 | 批量补 `.form-control`，并补 `:disabled` 样式 |
| 挂载点详情用 emoji（👤 📈 ⏱️ ⚙️） | 与基准的图标语言不一致、跨平台字形不一 | 换成 Font Awesome 图标 + 数字等宽对齐 |
| 仪表盘"User Connections"标注含糊 | 看不出是账号数还是连接数 | 拆成 `Online Users`（账号）+ `N connections`（连接），数据由后端 `counts` 提供 |

---

## 二、前后端数据链路审计

### 1. 上线/下线推送时效

原实现只有一个 3 秒的推送循环。

| 场景 | 原实现 | 现在 | 实测 |
|---|---|---|---|
| 挂载点上线 | ≤3s（受循环相位影响） | 拓扑变化立即推，轮询粒度 0.5s | **≤1.5s** |
| 挂载点下线 | ≤3s，且受 1.5s 延迟清理叠加 | 同上 | **≤2.5s** |
| 用户上线 | ≤3s | 同上 | **≤1.5s** |
| 用户下线 | ≤3s + 保活探测最多 5s | 同上（探测逻辑未改） | **≤3s** |

实现方式（`src/web.py::_push_data_loop`）：

- 每 0.5 秒取一次**轻量签名**（挂载点名集合 + 每用户连接数），变化才推送完整快照；
- 每 `REALTIME_PUSH_INTERVAL`（默认 3s）仍全量推一次，作为统计、STR 表与心跳；
- 签名只读名字、不构造 `to_dict()`，3000 挂载点下比"每 0.5 秒全量深拷贝"便宜两个数量级。

### 2. 统计口径：哪些数字原来是错的

| # | 问题 | 现象 | 修复 |
|---|---|---|---|
| 1 | `total_users` 实际是**连接数** | "在线用户"和"连接数"两个数字相同，同一账号开 3 条连接显示 3 个用户 | `get_statistics()` 拆成 `total_users`（账号）与 `total_connections`（连接） |
| 2 | 前端靠 `Object.keys().length` 猜数量 | 后端换结构就静默变成 0/错值 | `/api/system/stats` 新增 `counts: {mounts, users, connections}`，前端优先用 |
| 3 | 整份统计来自 **10 秒缓存** | 运行时长每 10 秒跳一格；连接数最多滞后 10 秒 | 运行时长、NTRIP 计数、在线列表改为**每次调用实时取**，只有 psutil（CPU/内存/网卡）仍走 10 秒缓存 |
| 4 | `psutil.cpu_percent(interval=1)` 在请求线程里跑 | 首屏/缓存为空时，接口阻塞 1 秒 | 改 `interval=None` 并在启动时打底，采样仍由后台线程做 |
| 5 | 统计线程**先 sleep 再采集** | 启动后前 10 秒任何请求都落到"缓存为空 → 现场采集" | 改为先采集再睡 |
| 6 | `max_concurrent` 读了不存在的键 | "Max Connections" 恒为 0 | 改读 `max_connections` |
| 7 | 每客户端 `bytes_sent` 把累计值当增量反复累加 | 一路 5KB 的流，界面显示"已发送 180KB"，随运行时间平方级膨胀 | 改为传增量（`bytes_reported` 记账） |
| 8 | `data_rate = total_bytes / uptime` | 是"自连上以来的平均值"，停推了也显示正常速率 | 改成 10 秒滑动窗口的实时速率 |
| 9 | `active_connections` 在 `submit()` **之后**才 +1 | 短连接先执行了 -1，计数器永久漂成负数，在线数越跑越少 | 先计数再提交，提交失败回滚 |
| 10 | 挂载点下线只删 `online_mounts` | `mount_connection_count` 留旧值，重连后连接数从旧值继续累加 | 下线时一并清理计数与速率样本 |
| 11 | 重复清理会把计数减成负数 | 界面出现 -1 个连接 | 计数下限钳到 0 |
| 12 | `get_user_connection_count` / `get_mount_connection_count` 无锁 | 与写线程并发时读到中间值 | 补锁 |

### 3. 口径定义（现在写清楚了）

- **Uptime**：进程运行时长，`/api/system/stats.uptime`（秒）。
- **Online Mounts**：`online_mounts` 的条目数（源站推流中的挂载点）。
- **Online Users**：`online_users` 的**账号**数；**N connections**：拉流连接总数。
- **User connections**（Runtime 里）：同上，连接数。
- **Data Transfer**：所有在线挂载点 `total_bytes` 之和（源站入向字节）。
- **Mount uptime**：`now - mount.connect_time`，每次读取实时计算。
- **Data rate**：最近 10 秒窗口内的字节数 / 窗口秒数。

---

## 三、验证

新增 `tests/test_stats_push.py`（自带临时实例，端口 21021/25767），
用真实 Socket.IO 客户端监听推送并计时：

```
[1] 连接后立即收到首轮推送
[2] 挂载点上下线推送时效          上线 ≤1.5s / 下线 ≤2.5s
[3] 用户上下线推送时效            上线 ≤1.5s / 下线 ≤3s
[4] 统计口径与运行时长            账号 1 / 连接 2 / 挂载点 1；字节与实发一致；速率>0
[5] 统计推送频率                  8.5s 内 ≥2 次，间隔 ≤4s，Δuptime≈Δt
[6] 接口两次调用间运行时长增长
结果: 21 通过 / 0 失败
```

回归：

```
tests/test_smoke.py       37 通过 / 0 失败
tests/test_web_crud.py    64 通过 / 0 失败
py_compile / node --check  通过
```

> 依赖说明：`tests/test_stats_push.py` 需要 `requests`（python-socketio 客户端走 HTTP 轮询），
> 缺失时会打印提示并跳过，不影响其它测试。

---

## 四、仍未做

1. **前端外部 CDN 依赖**：socket.io、OpenLayers（JS+CSS）、Font Awesome、Orbitron 字体。
   内网部署时地图与图标会缺失（图标已做隐藏降级，地图会显示占位框）。建议 vendor 到 `static/`。
2. **界面语言**：仍为英文（与原项目一致）。基准项目是中文 + 中英切换，如需一致可加一层 i18n。
3. **P1-2 线程模型**：仍是"1 连接 1 线程"。本次只保证统计与推送准确，未动线程模型。
4. **仪表盘"Uptime"含义**：当前是进程运行时长；若更想看"最近一次重启时间"，可在卡片副标题补上启动时刻。
