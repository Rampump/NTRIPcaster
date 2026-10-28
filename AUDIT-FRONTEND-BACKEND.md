# 前后端接口契约审计（第 7 轮）

**审计目标**：前端页面上的"新增/保存/删除"是否真的把值写进了数据库（用户口中的"注册表"），
以及除写入之外，还有没有前后端字段/路径对不上的地方。

**结论**：写入本身（SQL 语句、哈希、外键维护）基本正确，但**接口层有 8 处会让"页面显示成功、
库里没变或变错"的问题**，另有 2 处状态同步问题。全部已修复，并补了一套直接查库核对的回归测试。

---

## 一、写入路径全景

系统没有独立的"配置注册表"，需要落盘的只有 3 张 SQLite 表（`src/database.py`）：

| 表 | 写入者 | 前端入口 |
|---|---|---|
| `admins` | `update_admin_password` | 设置页 → Change Admin Password |
| `users` | `add_user` / `update_user` / `update_user_password` / `delete_user` | 用户管理 → Add / Edit / Delete |
| `mounts` | `add_mount` / `update_mount` / `delete_mount` | 挂载点管理 → Add / Edit / Delete |

对应 HTTP 接口只有 6 个（`src/web.py`）：

```
POST   /api/users              → add_user
PUT    /api/users/<username>   → update_user / update_user_password / update_admin_password
DELETE /api/users/<username>   → delete_user
POST   /api/mounts             → add_mount
PUT    /api/mounts/<mount>     → update_mount
DELETE /api/mounts/<mount>     → delete_mount
```

`config.ini` **没有任何写入口**（只有 `configparser.read`），所以"改配置项"这件事目前只能改文件；
下面第 5 条是唯一与配置相关的写入问题。

---

## 二、发现的问题（全部已修复）

### 1. 编辑用户时同时改名 + 改口令 → 口令被静默丢弃

`src/web.py::api_user_detail` 原来的分支是：

```python
if new_username:      # 改名
    ...update_user(user_id, new_username, None)
elif new_password:    # 改口令
    ...update_user_password(...)
```

编辑用户弹窗（`editUser`）两个输入框在同一次提交里一起发，于是走 `if` 分支，`password` 永远不生效，
返回消息还写着"用户名从 a 更新为 b"，使用者以为两样都改了。

**修复**：合并为一条路径，先把用户名、口令分别校验完，再一次 `update_user(user_id, new_name or old_name, new_password or None)`；
返回消息按实际变化拼装（"用户名从 a 更新为 b；口令已更新"）。

### 2. 新增挂载点传 `username` 被忽略，落库成"未绑定"

`POST /api/mounts` 只读 `user_id`；`PUT /api/mounts/<m>` 却支持 `username`/`user_id` 两种。
传 `username` 时接口返回 201，但 `mounts.user_id` 是 NULL —— 而 NTRIP 2.0 的鉴权是
"挂载点未绑定 → 任何注册用户都能取流"，等于把挂载点放开了。仓库自带的 `tests/test_smoke.py`
就一直是这么建的（`{'mount': ..., 'username': ...}`），之前没被发现。

**修复**：POST 也支持 `username`，查不到该用户直接 400。

### 3. 改在线挂载点的口令 → 源站继续推流，口令修改等于没生效

`PUT /api/mounts/<m>` 原来只调 `forwarder.force_disconnect_mount()`，它只踢**下载端**和删缓冲区，
**不关闭源站的上传 socket**。结果：

- 用旧口令连上的源站继续往这个挂载点写数据；
- `online_mounts` 里那条记录还在，管理页显示"在线"、STR 表照旧输出；
- 库里口令已经变了，但鉴权早就发生过了，不影响现有连接。

**修复**：DB 更新成功后按"先断源站（`remove_mount_connection`，会 close socket）→ 再清下载端与缓冲区
（`force_disconnect_mount`）"的顺序清理；并且只在**确实发生变化**时才踢，避免"点一下保存就把在线挂载点打断"。

### 4. 挂载点改名后，在线表 / STR 表残留旧名字

同一个根因：`online_mounts` 以挂载点名做 key，改名只改了库，内存里还是旧 key。
管理页会出现一条"注册表里并不存在的挂载点"，而新名字显示为离线。

**修复**：随第 3 条一并清理旧 key；改名时清理原因记为"挂载点已重命名为 X"。

### 5. 设置页把管理员用户名写死成 `admin`

`changePassword()` 固定打 `PUT /api/users/admin`。而 `[admin] username` 在 `config.ini` 里是可改的，
一旦部署改成别的名字，这个请求会掉进"普通用户"分支（`users` 表里没有该管理员），返回"用户不存在"，
**管理员在界面上永远改不了自己的口令**。

**修复**：新增 `PUT /api/admin/password`，以会话里的 `admin_username` 为准；设置页改调它。
（`/api/users/admin` 保留兼容。）

### 6. 允许创建与管理员同名的普通用户 → 点编辑会改到管理员口令

`admins` 和 `users` 是两张表，UNIQUE 互不影响，所以 `POST /api/users {"username":"admin"}` 能建成功。
之后用户列表里出现一个 `admin` 行，点 Edit：前端判断 `username === 'admin'` 于是禁用用户名框，
提交走 `PUT /api/users/admin` → 命中**管理员分支** → 改掉的是真正的管理员口令。

**修复**：新增/改名时禁止使用与 `[admin] username` 同名（忽略大小写）的用户名。

### 7. `update_admin_password` 不检查影响行数

```python
c.execute("UPDATE admins SET password = ? WHERE username = ?", ...)
conn.commit(); return True     # 账号不存在也是 True
```

**修复**：`rowcount == 0` 返回 False，接口据此回 500，不再假装成功。

### 8. 前端编辑挂载点：无条件提交用户名，可能顺手解绑

`editMount` 先拉 `/api/mounts`，再**额外**拉 `/api/users` 反查绑定的用户名；`submitEditMount` 则
无条件 `updateData.username = username || ""`。于是：

- `/api/users` 这次请求失败 → `currentUsername` 为空 → 用户只想改口令，保存时却把绑定清掉了；
- 任何一次保存都会带 `username`，让后端认为"绑定被显式提交"。

**修复**：`/api/mounts` 本来就返回 `username` 字段，直接用；表单记住 `data-original`，
**只有值真的变了才提交** `username`。

### 9. 登录后的 `?page=` 参数没人消费

`/login` 认证成功会 `redirect('/?page=users')`，但 `app.js` 全文件没有 `URLSearchParams`，
SPA 初始页永远 `navigateTo('dashboard')`。从"挂载点管理"被拦去登录后，回来还是仪表盘。

**修复**：`DOMContentLoaded` 里读取 `?page=`，命中白名单才跳转。

### 10. 改口令接口的字符校验与新增不一致

`POST /api/users` 会校验 `^[a-zA-Z0-9_-]+$`，`PUT` 改口令却只校验长度，可以改出
"建得出来的账号改不出来"的口令。**修复**：统一校验。

---

## 三、核对过、确认没问题的部分

| 项 | 结论 |
|---|---|
| 口令存储 | 用户口令、挂载点口令、管理员口令全部 PBKDF2-HMAC-SHA256（`salt$hash`），无明文 |
| 列表接口泄露 | `/api/users`、`/api/mounts` 都不返回哈希；挂载点口令固定返回 `******` |
| 删除用户 | 先 `UPDATE mounts SET user_id = NULL`，再删用户；实测绑定被清空 |
| 未提交绑定字段 | `KEEP_USER_BINDING` 哨兵生效，只改口令/改名不会动绑定 |
| 改挂载点名 | 重名被拒、旧名消失、口令与绑定都保留 |
| 非法输入 | 重名、用户不存在、字符非法、长度越界均返回 400 且不写库 |
| 权限 | 全部 `/api/*` 走 `before_request` 白名单 + `require_login`，未登录一律 401 |
| 路由对齐 | 前端 12 个 fetch 路径与后端路由逐一对得上，无 404（`/static/freq_map.json` 存在） |
| Socket 推送 | 前端订阅的 4 个事件名与后端 `emit` 完全一致，`join_room('data_push')` 存在 |
| RTCM 数据类型 | 后端发 `geography`/`device_info`/`msm_satellite`/`bitrate`/`message_stats`，前端均有对应分支 |

---

## 四、新增回归测试

`tests/test_web_crud.py`——直接查库核对，不信任接口返回值：

```
[A] 用户写入            24 项   新增/重复/改口令/改名/改名+改口令/删除/同名管理员
[B] 挂载点写入          20 项   两种绑定方式、改口令、改名、换绑、解绑、重名、删除
[C] 在线挂载点一致性    11 项   改口令后源站被断开、在线表与 STR 表不残留
[D] 管理员与敏感字段     5 项   设置页改口令、列表不泄露哈希
```

运行结果：**64 通过 / 0 失败**；原有 `tests/test_smoke.py` **37 通过 / 0 失败**（合计 101 项）。

---

## 五、仍然存在的建议（未改）

1. **管理员改口令后不失效其它会话**：其它已登录的浏览器不会被强制下线。若要收紧，可在
   `admins` 表加 `password_changed_at`，与 session 里的时间戳比对。
2. **PBKDF2 迭代 10000 偏低**，建议提到 200k 并加 `iter` 前缀做惰性升级（前几轮已记录）。
3. **口令只允许 `[a-zA-Z0-9_-]`**：前后端一致，但这是产品层面的强度限制，不是 bug。
4. `config.ini` 没有写入口：如果确实需要"页面上改配置项并落盘"，需要新增一个配置写回服务
   （`configparser.write` + 原子替换 + 热加载），目前完全没有这块。
