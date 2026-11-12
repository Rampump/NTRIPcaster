# Running a Public Demo Site (demo_mode)

This document covers exactly one scenario: **turning this caster into a public trial service** — anyone can sign in, create an account, add a mount point and push their own data to watch the parser work, but **nobody can edit or delete anyone else's data**.

You do not need this file for a normal production deployment. How the docs divide up:

| Document | Covers |
|---|---|
| [README.md](README.md) | Feature overview, configuration reference, upgrades |
| [INSTALL-TUTORIAL-EN.md](INSTALL-TUTORIAL-EN.md) | Installing on a bare Linux box (system prep, dependencies, systemd, firewall) |
| [DOCKER-TUTORIAL-EN.md](DOCKER-TUTORIAL-EN.md) | Container details (image, volumes, reverse proxy container) |
| **This file** | Demo-specific: flipping the switch, the required companions, acceptance checks, and the traps |

> System requirements, dependency installation and basic firewall commands are not repeated here — they live in INSTALL-TUTORIAL-EN.md.

---

## 1. How a demo site differs from production

Read this before choosing how to install. A demo site is not "production plus a flag"; it is a **separate deployment shape**.

| Item | Production | Demo |
|---|---|---|
| Instance | Dedicated machine | Prefer a dedicated machine or container, **never share a database** |
| Database | Long-lived business data | Disposable, pruned after 60 days |
| Admin password | Must be changed before going live | **Must stay `admin123`** (cannot be changed in demo mode) |
| Write operations | All allowed | Add-only; edit/delete/password change/restart return 403 |
| Public signup | Usually closed | Open by default, bounded by limits and pruning |
| Data trust | Real data | **Treat everything as untrusted** — no real station coordinates or credentials |
| Resources | Sized for real load | Cap the parser thread count (default 4) |

In one line: **the demo database is a trash can. Nothing you want to keep may live in it.**

---

## 2. Three decisions before you start

**① Dedicated instance, or coexist with production?**

- A second machine / VPS → use [3.1](#31-path-a-dedicated-machine-recommended); simplest by far
- Only one machine → use [3.2](#32-path-b-coexisting-on-one-machine-docker); every port and volume must be offset
- Just want to look at the UI → use [3.3](#33-path-c-run-locally-no-public-access)

**② Domain and HTTPS**

The demo is for strangers, and both login sessions and the `ntrip_cid` identifier travel in cookies. **HTTPS is mandatory**, otherwise cookies cross the public internet in clear text. See [section 8](#8-nginx--https-mandatory).

**③ The database must be newly created**

In demo mode the admin password cannot be changed. If you point the demo at an old database whose password was already changed, the result is **nobody can log in and there is no UI to fix it**. A fresh database avoids this entirely — see [section 5](#5-database-and-the-admin-password).

---

## 3. Deployment

### 3.1 Path A: a dedicated machine (recommended)

System requirements, dependencies and firewall basics are in [INSTALL-TUTORIAL-EN.md](INSTALL-TUTORIAL-EN.md). Only the demo-specific commands are here.

```bash
# 1. Clone and install (Debian 11+ / Ubuntu 22.04+)
git clone https://github.com/Rampump/NTRIPcaster.git
cd NTRIPcaster
sudo bash install.sh
```

The installer places the config at `/etc/2rtk/config.ini`, the service is named `2rtk`, data lives in `/opt/2rtk/data/2rtk.db`.

```bash
CFG=/etc/2rtk/config.ini

# 2. Enable demo mode (rewrite the value if present, otherwise insert it)
if sudo grep -q '^demo_mode' "$CFG"; then
    sudo sed -i 's/^demo_mode *=.*/demo_mode = true/' "$CFG"
else
    sudo sed -i '/^\[security\]/a demo_mode = true' "$CFG"
fi

# 3. Cap parser concurrency (same pattern, insert under [web] if missing)
if sudo grep -q '^max_web_parsers' "$CFG"; then
    sudo sed -i 's/^max_web_parsers *=.*/max_web_parsers = 4/' "$CFG"
else
    sudo sed -i '/^\[web\]/a max_web_parsers = 4' "$CFG"
fi

# 4. Restart and verify
sudo systemctl restart 2rtk
curl -s http://127.0.0.1:5757/health
```

If the response contains `"demo_mode": true`, the switch is live.

> That `if/else` is not overkill: **two `demo_mode` lines in the same section make the program fail to start** (configparser raises on duplicate keys). So either rewrite the value or insert it — never append blindly.

> Upgraded from an older release? The keys `demo_mode` / `max_web_parsers` may not exist at
> all. Instead of hand-editing, run the merge script once — it only fills in missing keys and
> never touches values you already set:
>
> ```bash
> sudo /opt/2rtk/venv/bin/python /opt/2rtk/scripts/merge_config.py --apply \
>     --config /etc/2rtk/config.ini --example /opt/2rtk/config.ini.example
> ```
>
> Then run steps 3 and 4 above (`grep` now matches, so the "rewrite the value" branch is used).

> Prefer an editor? Editing `/etc/2rtk/config.ini` by hand is identical — just remember `systemctl restart 2rtk` afterwards.

### 3.2 Path B: coexisting on one machine (Docker)

Production already holds 2101 / 5757 and the service name `2rtk`, so every one of those must be offset. **Do not run `install.sh` a second time** — it hardcodes the install dir, config dir and service name, and will overwrite your production config.

Create `docker-compose.demo.yml` next to the `Dockerfile`:

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
      - "2102:2101"     # external NTRIP (offset from production 2101)
      - "5758:5757"     # external web (offset from production 5757)
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
      - DEMO_MODE=true          # the only change needed inside the container
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
    name: ntrip-demo-net       # own network; do not reuse the production ntrip-network

volumes:
  ntrip-demo-data:
    name: ntrip-demo-data      # rename all three, or you will mount production data
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

On first boot the container copies `config.ini.example` to `/app/config/config.ini`, so **`DEMO_MODE=true` is the only thing you need**; there is no file to edit inside the container. When the volume already holds an older `config.ini`, the entrypoint merges in the missing keys (again without touching existing values), so upgrading the image needs no manual config migration.

Three things that must be offset (getting them wrong hurts):

| Item | Production | Demo | What happens if they collide |
|---|---|---|---|
| Ports | 2101 / 5757 | 2102 / 5758 | Container fails to start, or clients reach production |
| Volume names | `ntrip-data` etc. | `ntrip-demo-data` etc. | **Both instances share one database**; the cleanup script deletes production data |
| Container name | `ntrip-caster` | `ntrip-caster-demo` | `docker rm -f ntrip-caster` takes down production |

### 3.3 Path C: run locally (no public access)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config.ini.example config.ini

# one edit only — in the [security] section of config.ini:
#   demo_mode = true

DEMO_MODE=1 python main.py
```

Open `http://127.0.0.1:5757` and sign in with `admin / admin123`. No service, no autostart, and a restart gives you a clean state — ideal for checking the UI.

---

## 4. Enabling demo mode

One switch, three equivalent ways to set it:

| Situation | How |
|---|---|
| Source / installer deployment (**recommended**) | add `demo_mode = true` under `[security]` in `/etc/2rtk/config.ini` |
| Quick experiment, no config edit | start with the environment variable `DEMO_MODE=1` |
| Docker | `DEMO_MODE=true` in the compose `environment` block (or in `.env`) |

```ini
[security]
# ... leave everything above untouched ...
demo_mode = true
```

The environment variable wins: if `DEMO_MODE` is one of `1/true/yes/on`, a config value of `false` is still treated as enabled.

**Verify the switch actually took effect** — don't skip this; most "demo mode does nothing" reports turn out to be a missing restart:

```bash
curl -s http://127.0.0.1:5757/health
# {"status":"ok","version":"2.3.6","uptime":12.3,"demo_mode":true}
```

Still `false`? Check in this order:

1. Right file — installer deployments read `/etc/2rtk/config.ini`, **not** `/opt/2rtk/config.ini` (that is a symlink; editing it works but is easy to get wrong)
2. Service restarted — `sudo systemctl restart 2rtk`
3. Any `DEMO_MODE=0` in the environment overriding it

---

## 5. Database and the admin password

Demo mode deliberately does **not** reset the password. At startup it only tries `admin / admin123` once and warns if that fails:

```
[WARNING] demo mode is enabled but the password of admin is not the default admin123.
          Passwords cannot be changed from the UI in demo mode; make sure this is a
          freshly created database, or temporarily disable demo_mode and set it back.
```

Treat that warning as a stop sign: the site is effectively closed to visitors.

**The correct approach: start from an empty database**

```bash
# source install: stop the service, move the old DB aside (don't delete it)
sudo systemctl stop 2rtk
sudo mv /opt/2rtk/data/2rtk.db /opt/2rtk/data/2rtk.db.bak
sudo systemctl start 2rtk      # creates a fresh DB with admin/admin123
```

The DB path is `[database] path` in `config.ini`; installer deployments default to `/opt/2rtk/data/2rtk.db`.

**Already locked out?**

The password endpoint returns 403 in demo mode and the UI has no entry point. Two ways back:

```bash
# Option 1: turn the switch off, change the password, turn it back on
sudo sed -i 's/^demo_mode *=.*/demo_mode = false/' /etc/2rtk/config.ini
sudo systemctl restart 2rtk
# browser → Settings → change admin password → set it back to admin123
sudo sed -i 's/^demo_mode *=.*/demo_mode = true/' /etc/2rtk/config.ini
sudo systemctl restart 2rtk

# Option 2: delete the admins row; a default admin is recreated on next boot
sudo systemctl stop 2rtk
sqlite3 /opt/2rtk/data/2rtk.db "DELETE FROM admins;"
sudo systemctl start 2rtk
```

> The "users" visitors create are NTRIP mount-point credentials (the `users` table) — **unrelated to the admin account** used to sign in (the `admins` table). No amount of self-registered accounts grants backend access.

---

## 6. What demo mode actually blocks

The rule is **fail-closed**: everything except the whitelist is rejected. That makes "what is blocked" the more important list — any future write endpoint lands on the blocked side by default.

**Allowed (the complete list)**

| Endpoint | Purpose |
|---|---|
| `POST /api/login` | Sign in |
| `POST /api/users` | Create an NTRIP account |
| `POST /api/mounts` | Create a mount point |
| `POST /api/mount/<name>/rtcm-parse/start` | Subscribe to parsing |
| `POST /api/mount/<name>/rtcm-parse/stop` | Unsubscribe |
| `POST /api/mount/rtcm-parse/{stop,heartbeat}` | Unsubscribe all / heartbeat |
| `POST /api/public/*` | Anonymous data page (see section 7) |
| All `GET / HEAD / OPTIONS` | Reads |

**Always 403 (the common ones)**

| Endpoint | Would have done |
|---|---|
| `PUT /api/users/<name>`, `DELETE /api/users/<name>` | Change password / delete account |
| `PUT /api/mounts/<name>`, `DELETE /api/mounts/<name>` | Edit / delete mount point |
| `PUT /api/admin/password` | Change admin password |
| `POST /api/system/restart` | Restart the service |
| `POST /api/mount/initialize`, `POST /api/bypass/stop-all`, `POST /api/mount/<name>/simulate` | Initialize / stop all parsing / inject simulated data |

Every rejection uses the same JSON shape:

```json
{"error": "Demo mode: adding is allowed, editing and deleting are not", "code": "demo_readonly"}
```

The frontend keys off `code` and shows a proper "demo mode does not allow this" message instead of a generic error. If you script against the API, match on `code`, not on the message text.

**Why RTCM parsing is allowed**: it only manipulates in-memory viewer threads and never writes to the database. Since v2.3.6 parser threads are shared per mount point and each client holds its own subscription, so a visitor hitting "stop" affects only themselves.

**Login is restricted too**: in demo mode only `admin` may sign in. Leftover admin accounts from an older database are refused, which prevents "several admins, one of them with a password someone else set".

---

## 7. Three companion settings (enable all of them)

The real risk on a public demo isn't who signs into the backend, it's **endpoints being hammered by scripts** and **data piling up forever**. README only lists these names; here is how to set them.

### 7.1 Parser concurrency cap (must tune)

Every web parser thread decodes MSM in real time and consumes a CPU core. A few clicks from the internet will saturate a 2-core box.

```ini
[web]
max_web_parsers = 4              # concurrent parsed mount points; 3-4 on 2C2G
parser_client_ttl = 45           # heartbeat timeout (s) — catches "browser closed"
parser_grace_seconds = 5         # grace period after last unsubscribe, avoids rebuild churn
parser_ip_rate_per_minute = 10   # subscribe attempts per IP per minute
```

Exceeding them returns **429** and the UI shows "parsing slots are full, try again later". The numbers are not arbitrary: `max_web_parsers` follows your core count, and `parser_ip_rate_per_minute` follows how often a normal visitor switches stations — human clicking rarely exceeds ten per minute.

### 7.2 Open the data page to anonymous visitors (optional)

By default anonymous visitors are redirected to the login page. If you want them to see the STR table and live satellite view without signing in (usually the main attraction of a demo):

```ini
[web]
public_access = true
```

With it enabled:

- anonymous requests only use the `/api/public/` namespace; user lists, source IPs and logs are never sent
- anonymous visitors may start parsing themselves — otherwise the satellite view has no data
- user management, mount management and settings **still require login**
- consider lowering `max_web_parsers` one notch: anonymous traffic is harder to reason about

You don't need this switch — visitors can sign in as `admin/admin123` and see the same thing, with one extra step.

### 7.3 60-day pruning (mandatory in public)

If anyone can add data, someone has to clean it. The `users` / `mounts` tables gain a `created_at` column automatically at startup (old databases upgrade in place, no manual migration).

```bash
# Preview what would be deleted (this is the default — nothing is changed)
sudo -u 2rtk /opt/2rtk/venv/bin/python /opt/2rtk/scripts/demo_reset.py

# Then delete for real
sudo -u 2rtk /opt/2rtk/venv/bin/python /opt/2rtk/scripts/demo_reset.py --apply --days 60
```

The script touches only `users` and `mounts`, **never `admins`**; it unbinds `mounts.user_id` before deleting users; rows with an empty `created_at` (data inherited from an older database) are kept unless you pass `--include-no-date`.

Schedule it:

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

Three details that are easy to get wrong:

- **`User=2rtk` is not optional.** Running as root leaves root-owned database files in the data directory, and the service (running as `2rtk`) then fails with permission errors.
- **Use the project's own venv interpreter** (`/opt/2rtk/venv/bin/python`), not the system `python3` — the script does `from src import config`, so it needs the full project environment.
- **Set `NTRIP_CONFIG_FILE` explicitly.** Otherwise the script looks for `config.ini` inside the source tree; on installer deployments that is a symlink, and once it breaks the DB path cannot be resolved.

For Docker, run it inside the container (the image already ships `scripts/`):

```bash
docker compose -f docker-compose.demo.yml exec ntrip-demo \
    python /app/scripts/demo_reset.py --apply --days 60
```

---

## 8. Nginx + HTTPS (mandatory)

A demo site serves strangers; cookies and passwords must not travel in clear text. Minimal working config — **save the whole block as `/etc/nginx/conf.d/demo.conf`** (those files are included from `http {}`, so `limit_req_zone` is legal there):

```nginx
# Rate-limit write endpoints only — cheaper than doing it in the app
limit_req_zone $binary_remote_addr zone=demo_write:10m rate=20r/m;

server {
    listen 443 ssl;
    server_name demo.example.com;

    ssl_certificate     /etc/letsencrypt/live/demo.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/demo.example.com/privkey.pem;

    # Proxy settings live at server level and are inherited by every location.
    # Not using proxy_params: the official nginx image does not ship that file.
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade           $http_upgrade;   # required by Socket.IO
    proxy_set_header Connection        "upgrade";
    proxy_read_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:5758;      # 5758 for Docker, 5757 for a source install
    }

    # Throttle only the create endpoints; GET stays unlimited (the data page polls)
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

Behind the proxy, enable these two in `config.ini`, otherwise every visitor looks like `127.0.0.1` and rate limiting collapses into one bucket:

```ini
[security]
trust_proxy_headers = true       # trust X-Forwarded-For
session_cookie_secure = true     # only send cookies over HTTPS
```

`trust_proxy_headers` **must only be enabled behind a trusted proxy**, otherwise clients can forge `X-Forwarded-For` and bypass the limits.

NTRIP ports (2101 / 2102) are plain TCP and bypass Nginx — open them in the firewall / security group directly.

---

## 9. Acceptance checklist

Run these after deployment. Every line must match before you call it done. On a Docker deployment, replace `5757` with `5758`.

```bash
BASE=http://127.0.0.1:5757

# 1. Switch is live
curl -s $BASE/health | grep -o '"demo_mode":true'

# 2. Sign in (keep the session in a cookie jar)
curl -s -c /tmp/demo.jar -X POST $BASE/api/login \
     -H 'Content-Type: application/json' \
     -d '{"username":"admin","password":"admin123"}' -o /dev/null -w "login=%{http_code}\n"
# expect 200

# 3. Creating a user is allowed
curl -s -b /tmp/demo.jar -X POST $BASE/api/users \
     -H 'Content-Type: application/json' \
     -d '{"username":"guest_test","password":"guest123"}' -o /dev/null -w "create_user=%{http_code}\n"
# expect 201

# 4. Editing is blocked
curl -s -b /tmp/demo.jar -X PUT $BASE/api/users/guest_test \
     -H 'Content-Type: application/json' \
     -d '{"password":"newpass123"}' -w "  edit_user=%{http_code}\n"
# expect 403 {"code":"demo_readonly",...}

# 5. Deleting is blocked
curl -s -b /tmp/demo.jar -X DELETE $BASE/api/users/guest_test \
     -w "  delete_user=%{http_code}\n"
# expect 403

# 6. Admin password change is blocked
curl -s -b /tmp/demo.jar -X PUT $BASE/api/admin/password \
     -H 'Content-Type: application/json' -d '{}' -w "  admin_pwd=%{http_code}\n"
# expect 403

# 7. Restart is blocked
curl -s -b /tmp/demo.jar -X POST $BASE/api/system/restart -w "  restart=%{http_code}\n"
# expect 403

# 8. A non-default admin name cannot sign in
curl -s -X POST $BASE/api/login -H 'Content-Type: application/json' \
     -d '{"username":"someone","password":"whatever"}' -o /dev/null -w "other_admin=%{http_code}\n"
# expect 401, or 200 with the login page and an error message — never a backend session

# Clean up the test row
sqlite3 /opt/2rtk/data/2rtk.db "DELETE FROM users WHERE username='guest_test';"
```

Three things to confirm in the browser too:

1. The users and mounts pages show **no** Edit / Delete buttons, and carry a yellow notice at the top
2. In Settings, the "change admin password" and "restart service" panels are replaced by a demo explanation
3. The login page shows the demo credentials `admin / admin123` so visitors don't have to guess

---

## 10. Troubleshooting

**Works in one browser, empty in another**

Expected. Sessions are per browser; each one signs in separately. To browse data without signing in, enable `public_access` (section 7.2).

**Visitors report "the button is grey / clicking does nothing"**

Almost always a 429: no free parser slot. Look for `parser limit reached` in the log. Either wait for others to unsubscribe or raise `max_web_parsers` — check CPU headroom first.

**A visitor says "the mount point I added isn't in the list"**

The mount list shows only stations with a live connection; offline ones are hidden. Ask them to push data and refresh, or check `/api/public/mount/rtcm-parse/status`.

**The Edit / Delete buttons are gone — is it broken?**

That is the demo UI by design. To confirm the backend lock is still there, hit the endpoints with the curl commands in section 9 and expect 403.

**Two visitors watching different stations — do they interfere?**

No. Since v2.3.6 parser threads are shared per mount point and each client holds its own subscription: A switching stations does not interrupt B, and data is routed by room. A single visitor watches one station at a time (the UI is single-select anyway).

**Will the cleanup script delete the admin account?**

No. It only handles `users` / `mounts`; the `admins` table is never touched — that is the demo's door.

**Someone hammered the site with tens of thousands of rows**

Stop the service (`systemctl stop 2rtk`), run `demo_reset.py --apply --days 0` to wipe non-admin data, then tighten `parser_ip_rate_per_minute` and the Nginx `limit_req`, and consider blocking the source at the firewall.

---

## 11. Turning demo mode off

```bash
sudo sed -i 's/^demo_mode *=.*/demo_mode = false/' /etc/2rtk/config.ini
sudo systemctl restart 2rtk
curl -s http://127.0.0.1:5757/health | grep -o '"demo_mode":false'
```

Full admin behaviour returns immediately, including the Edit / Delete buttons. **Data does not roll back**, though — whatever was created or pruned during the demo stays that way.

Before converting this machine into production, do three more things:

1. Change the `admin123` password (Settings, or edit the DB)
2. Turn `public_access` off if you want login-only access again
3. Remove test accounts and mount points left over from the demo, or simply start from a fresh database

---

## Appendix: one-page cheat sheet

```ini
# /etc/2rtk/config.ini
[security]
demo_mode = true                 # master switch
trust_proxy_headers = true       # only behind a trusted proxy
session_cookie_secure = true     # required with HTTPS

[web]
public_access = true             # optional: anonymous data page
max_web_parsers = 4              # parser concurrency cap
parser_client_ttl = 45           # heartbeat timeout (s)
parser_grace_seconds = 5         # unsubscribe grace period (s)
parser_ip_rate_per_minute = 10   # subscribe attempts / IP / minute
```

```bash
sudo systemctl restart 2rtk                                  # restart after any edit
curl -s http://127.0.0.1:5757/health                         # check demo_mode
sudo -u 2rtk /opt/2rtk/venv/bin/python /opt/2rtk/scripts/demo_reset.py            # dry run
sudo -u 2rtk /opt/2rtk/venv/bin/python /opt/2rtk/scripts/demo_reset.py --apply    # delete
```

Chinese version: [DEMO-TUTORIAL.md](DEMO-TUTORIAL.md)
