# 2RTK NTRIP Caster

**Language:** **English** · [简体中文](README-zh.md)

A lightweight NTRIP caster written in Python, supporting NTRIP v1.0 and v2.0, administered through a web interface.

Add users and mount points in your browser, and watch live caster connection info — no config file editing, no CLI.

- Web-based administration — deploy on any cloud host
- SQLite storage for users and mount points
- Upload RTCM data to a mount point and the STR table entry is generated and corrected automatically (RTCM parsed via `pyrtcm`)
- High concurrency (tested with 2 RTCM sources and 2000 concurrent downloaders)
- Runs on Debian / Ubuntu / CentOS / Armbian and any system with Python 3.9+
- Docker support
- Bilingual web UI: English / 中文 (`?lang=en|zh` also works)

## Official Site & Demo Server

- Website: **https://2rtk.com**
- Public test server: **http://2rtk.net:5757** — log in with `admin` / `admin123` and try it out.
  (The demo instance runs in demo mode, where writes are limited to creating new records.)

## One-click Install (Debian 11+ / Ubuntu 22.04+)

Installs all dependencies, registers a **systemd service**, and enables **auto-start on boot**.

> ⚠️ **Not supported:** Ubuntu 20.04 and Debian 10. They ship Python 3.8, while `pyrtcm==1.1.8` requires Python 3.9+. Use Debian 11+ / Ubuntu 22.04+ (or newer).

The script downloads the source, installs dependencies, and creates the service. It auto-detects your network (tries GitCode first, falls back to GitHub; force it with `REPO_SOURCE`) and switches to a mainland-China PyPI mirror for faster installs.

Run with `sudo bash install.sh` as a normal user; if you are already logged in as **root**, use `bash install.sh` (no sudo).

**International** (GitHub):

```bash
git clone https://github.com/Rampump/NTRIPcaster.git && cd NTRIPcaster && sudo bash install.sh
```

**Mainland China** (GitCode):

```bash
git clone https://gitcode.com/rampump/NTRIPcaster.git && cd NTRIPcaster && sudo bash install.sh
```

The script reuses a local source directory and skips duplicate downloads. On network trouble, force a source: `REPO_SOURCE=github` or `REPO_SOURCE=gitcode`.

## Docker Deployment (Recommended)

One command, no manual configuration.

**International** (Docker Hub):

```bash
docker run -d --name ntrip-caster -p 2101:2101 -p 5757:5757 2rtk/ntripcaster:latest
```

**Mainland China** (Tencent Cloud mirror — no login, fast direct connection):

```bash
docker run -d --name ntrip-caster -p 2101:2101 -p 5757:5757 ccr.ccs.tencentyun.com/2rtk/ntripcaster:latest
```

## Access

| | |
|---|---|
| Web admin: | `http://yourserverip:5757` |
| NTRIP service: | `ntrip://yourserverip:2101` |
| Default account: | `admin` / `admin123` — **change this password immediately** |

## External Caster Relay (optional)

Off by default. Set `EXTERNAL_CASTER_ENABLED=true` and add upstream casters in **Settings → External casters** (host, port, credentials, account pool). `EXTERNAL_CASTER_UI_ENABLED=false` keeps the backend relay running while hiding the configuration page — useful for test servers.

- Pulls each upstream SOURCETABLE and caches its mount points locally; external mounts are merged into the STR table with local-first de-duplication, and local / external stations are listed separately in the web UI.
- Subscribing to an external mount opens an upstream relay on demand — the first subscriber starts it and the last one leaving closes it after a grace period. Web-based RTCM parsing reuses the same relay.
- Supports an account pool, randomized User-Agents and optional GGA forwarding. Relay/parser limits and switch throttling protect upstreams from aggressive polling.
- Demo mode friendly: writes return `403 demo_readonly` while read APIs and relay keep working.

Upstream SOURCETABLEs refresh every 6 hours with exponential backoff on failure to avoid upstream IP bans. Detailed design: `docs/external-caster-relay-design.md`.

## Acknowledgments

Built on these excellent open source projects:

| Library | Version | Purpose | License |
|---|---|---|---|
| [Flask](https://flask.palletsprojects.com/) | 3.0.3 | Web framework | BSD-3-Clause |
| [Flask-SocketIO](https://flask-socketio.readthedocs.io/) | 5.3.6 | Real-time WebSocket updates | MIT |
| [python-socketio](https://github.com/miguelgrinberg/python-socketio) | 5.8.0 | Socket.IO protocol | MIT |
| [psutil](https://github.com/giampaolo/psutil) | 5.9.5 | System resource monitoring | BSD-3-Clause |
| [pyrtcm](https://github.com/semuconsulting/pyrtcm) | 1.1.8 | RTCM 3.x message parsing (MSM) | BSD-3-Clause |
| [pyproj](https://github.com/pyproj4/pyproj) | 3.5.0 | Coordinate transformation | MIT |
| [reverse-geocode](https://github.com/thampiman/reverse-geocode) | 1.6.6 | Offline reverse geocoding | MIT |

Special thanks to **semuconsulting** for `pyrtcm`, which does the heavy lifting of RTCM parsing and STR table correction.

Contributions are welcome — issues and pull requests are appreciated.

## License

[Apache License 2.0](LICENSE). Third-party libraries keep their original licenses.

---

If this project helps you, please give it a **Star** ⭐
