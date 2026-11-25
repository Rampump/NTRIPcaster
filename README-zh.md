# 2RTK NTRIP Caster

**语言 / Language:** [English](README.md) · **简体中文**

一个使用 Python 编写的简易 NTRIP Caster，支持 NTRIP v1.0 和 v2.0 协议，通过 Web 管理页面进行管理。

在浏览器里就能添加用户和挂载点，并查看 NTRIP Caster 的实时连接信息 —— 不用改配置文件，不用敲命令。

- Web 可视化管理，任意云主机都能部署
- 使用 SQLite 存储用户与挂载点信息
- 在网页上添加挂载点并上传 RTCM 数据，STR 表信息会自动生成并校验修正（RTCM 解析基于 `pyrtcm`）
- 高并发（实测 2 个 RTCM 数据源 + 2000 并发下载）
- 支持 Debian / Ubuntu / CentOS / Armbian 等，任何有 Python 3.9+ 的系统都能跑
- 支持 Docker 部署
- Web 界面中英文双语，页头可切换（也支持 `?lang=zh|en`）

## 官网与测试服务器

- 官网：**https://2rtk.com**
- 公开测试服务器：**http://2rtk.net:5757** —— 默认账号 `admin` / `admin123`，可直接登录试用。
  （演示实例开启了演示模式，写操作仅限新增记录。）

## 一键安装（Debian 11+ / Ubuntu 22.04+）

自动安装依赖、创建 **systemd 服务**、设置**开机自启**。

> ⚠️ **不支持**：Ubuntu 20.04 和 Debian 10。它们自带 Python 3.8，而 `pyrtcm==1.1.8` 要求 Python 3.9+。请使用 Debian 11+ / Ubuntu 22.04+（或更新的发行版）。

脚本会自动下载源码、安装依赖、创建服务；自动适配网络（先试 GitCode，失败回退 GitHub，也可用 `REPO_SOURCE` 指定）；自动切换 pip 国内镜像加速依赖安装。

普通用户用 `sudo bash install.sh`；已是 root 登录的直接用 `bash install.sh`（无需 sudo）。

**国际服务器**（从 GitHub 拉取）：

```bash
git clone https://github.com/Rampump/NTRIPcaster.git && cd NTRIPcaster && sudo bash install.sh
```

**中国区服务器**（从 GitCode 拉取）：

```bash
git clone https://gitcode.com/rampump/NTRIPcaster.git && cd NTRIPcaster && sudo bash install.sh
```

脚本会自动检测本地源码、跳过重复下载；如遇网络问题，可加 `REPO_SOURCE=github` 或 `REPO_SOURCE=gitcode` 强制指定源。

## Docker 部署（推荐）

一键部署，无需手动配置。

**国际服务器**（Docker Hub 官方镜像）：

```bash
docker run -d --name ntrip-caster -p 2101:2101 -p 5757:5757 2rtk/ntripcaster:latest
```

**中国区服务器**（腾讯云镜像，无需登录、直连快）：

```bash
docker run -d --name ntrip-caster -p 2101:2101 -p 5757:5757 ccr.ccs.tencentyun.com/2rtk/ntripcaster:latest
```

## 访问地址

| | |
|---|---|
| Web 管理界面： | `http://yourserverip:5757` |
| NTRIP 服务： | `ntrip://yourserverip:2101` |
| 默认账号： | `admin` / `admin123` —— **请立即修改默认密码** |

## 外部 Caster 中继（可选）

默认关闭。设置 `EXTERNAL_CASTER_ENABLED=true` 后，管理员可在「设置 → 外部 Caster」配置上游地址、端口、账号与账号池。`EXTERNAL_CASTER_UI_ENABLED=false` 可在测试服务器上只开后端中继、隐藏前端配置入口。

- 拉取各上游 SOURCETABLE 并缓存挂载点，融合进对外 STR 表（同名以本地优先去重）；Web 页面将本地与外部基准站分开列出。
- 按需中继：第一个订阅者建立上游连接，最后一个离开后在宽限期内自动关闭；Web 端 RTCM 解析复用同一条中继。
- 支持账号池、随机 User-Agent 与可选的 GGA 转发；中继/解析限额与切换节流防止对上游频繁请求。
- 兼容演示模式：写操作返回 `403 demo_readonly`，读接口与中继不受影响。

上游 STR 表默认每 6 小时刷新一次（失败指数退避），避免频繁请求导致 IP 被封。详细设计见 `docs/external-caster-relay-design.md`。

## 致谢

本项目基于以下优秀的开源项目构建：

| 库 | 版本 | 用途 | 许可证 |
|---|---|---|---|
| [Flask](https://flask.palletsprojects.com/) | 3.0.3 | Web 框架 | BSD-3-Clause |
| [Flask-SocketIO](https://flask-socketio.readthedocs.io/) | 5.3.6 | WebSocket 实时推送 | MIT |
| [python-socketio](https://github.com/miguelgrinberg/python-socketio) | 5.8.0 | Socket.IO 协议实现 | MIT |
| [psutil](https://github.com/giampaolo/psutil) | 5.9.5 | 系统资源监控 | BSD-3-Clause |
| [pyrtcm](https://github.com/semuconsulting/pyrtcm) | 1.1.8 | RTCM 3.x 电文解析（MSM） | BSD-3-Clause |
| [pyproj](https://github.com/pyproj4/pyproj) | 3.5.0 | 坐标转换 | MIT |
| [reverse-geocode](https://github.com/thampiman/reverse-geocode) | 1.6.6 | 离线反向地理编码 | MIT |

特别感谢 **semuconsulting** 的 `pyrtcm`，RTCM 解析与 STR 表修正的重活都由它完成。

欢迎提交 Issue 和 Pull Request。

## 开源许可

[Apache License 2.0](LICENSE)。所有第三方库保留其原有开源许可证。

---

如果这个项目对你有帮助，欢迎点个 **Star** ⭐
