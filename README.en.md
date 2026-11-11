<h1 align="center">Marzkick</h1>

<p align="center">
  A fork of <a href="https://github.com/Gozargah/Marzban">Marzban</a>, the proxy management panel built on
  <a href="https://github.com/XTLS/Xray-core">Xray</a>: faster, with Hysteria 2, notification settings inside the
  Telegram bot and a one-command installer.
</p>

<p align="center">
  <a href="./README.md">Русский</a> · <b>English</b>
</p>

---

## One-command install

On a fresh **Debian 12 / Ubuntu 22.04+** server (systemd, root):

```bash
sudo bash -c "$(curl -sL https://raw.githubusercontent.com/ungerofydev/marzkick/main/install.sh)"
```

The installer installs dependencies and Xray (checksum-verified), downloads the code to `/opt/marzban`,
creates the virtual environment, `.env`, a systemd service and an admin, and starts the panel. After about a
minute it prints the address and the login. It asks for a Telegram bot token on the way (optional).

Preset anything with environment variables to skip the questions:

```bash
sudo env PORT=8080 ADMIN_USERNAME=root ADMIN_PASSWORD='secret' \
  TELEGRAM_API_TOKEN=123456:ABC... TELEGRAM_ADMIN_ID=111222333 \
  bash -c "$(curl -sL https://raw.githubusercontent.com/ungerofydev/marzkick/main/install.sh)"
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8000` | panel port |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / random | first administrator |
| `TELEGRAM_API_TOKEN` / `TELEGRAM_ADMIN_ID` | — | enable the bot (the token is checked with `getMe`) |
| `CERT_FILE` / `KEY_FILE` | — | HTTPS for the panel itself (self-signed certificates are detected) |
| `XRAY_PORT` | `1080` | port of the bundled Shadowsocks inbound |
| `APP_NAME` | `marzban` | install name (`/opt/<name>`, service, command) — several copies can coexist |
| `REPO` / `BRANCH` | this repository / `main` | where to take the code from |
| `GITHUB_TOKEN` | — | for a private repository |
| `UPDATE_XRAY` | — | `1` — download the latest Xray again |
| `ASSUME_YES` | — | `1` — never ask questions |

### Reaching the panel

Without HTTPS the panel only listens on `127.0.0.1` (that is how Marzban works). Use an SSH tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 root@<server-ip>      # then http://localhost:8000/dashboard/
```

For permanent access get a certificate for your domain (for example with `certbot`) and install with
`CERT_FILE=/path/fullchain.pem KEY_FILE=/path/privkey.pem`, or put them into `.env`
(`UVICORN_SSL_CERTFILE`, `UVICORN_SSL_KEYFILE`) with `marzban edit-env`.

### Update and uninstall

```bash
marzban update                      # git pull + dependencies + database migrations + restart
marzban uninstall                   # remove (asks separately about /var/lib/marzban)
```

Running the installer again does the same: `... install.sh)" @ update` or `@ uninstall`. Re-running it on an
installed system **never overwrites anything**: `.env`, the database and the admins stay.

## What is different from Marzban

**Hysteria 2.** A native Xray inbound (`protocol: hysteria`) as a first-class proxy type: users,
subscriptions (`hysteria2://` link, Xray JSON, Clash Meta, sing-box), Host Settings, traffic accounting.
See [HYSTERIA2.md](./HYSTERIA2.md).

**Telegram bot with settings inside.** Notifications are no longer configured through `.env`: the
**⚙️ Settings** menu (`/settings`) manages events, usage and expiry thresholds, the login whitelist, the log
channel, Discord and webhooks, with test buttons. Threshold warnings now reach Telegram too.
See [TELEGRAM_BOT.md](./TELEGRAM_BOT.md).

**Speed** (test VPS 2 vCPU / 2 GB, 5000 users):

| | before | after |
| --- | --- | --- |
| any `marzban cli` command | 3.7 s | ~0.2 s |
| `GET /api/users?limit=50` (dashboard page) | 470 ms | ~40 ms |
| `GET /api/users/usage` | 290 ms | ~11 ms |
| bot: “+GB for all” / “+days for all” | 151 s / 120 s | 0.2 s / 0.2 s |
| bot: delete expired users | 11 s | 1.9 s |
| notifications | block the API request | queue with retries and rate limits |

Achieved with a warm CLI daemon (starts by itself, see `cli/README.md`), fixing N+1 queries, SQL aggregation
and batched transactions.

**Other.** A complete `.env.example` with every variable; the `marzban` command (`up`, `down`, `restart`,
`logs`, `cli`, `backup`, `core-update`, `edit-env`, …) works for a source install; the code is tested on Python
3.11 (part of the upstream Telegram code only ran on 3.12); upstream bugs fixed (a user created without
explicit `inbounds` got no links, expiry notifications were sent even when switched off).

### Subscription links without `/sub/`

By default a link looks like `https://domain/sub/<token>`. For `https://domain/<token>` set
`XRAY_SUBSCRIPTION_URL_PREFIX = "https://domain"` and `XRAY_SUBSCRIPTION_PATH = ""` in `.env`. The panel still answers on
`/sub/<token>`, so a reverse proxy has to map it (nginx example):

```nginx
location ~ "^/([A-Za-z0-9_=-]{20,})(/(?:info|usage|v2ray|v2ray-json|clash|clash-meta|sing-box|outline))?$" {
    proxy_pass http://127.0.0.1:8000/sub/$1$2$is_args$args;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
}
```

Everything else (`/dashboard`, `/api`, `/docs`) is simply not exposed on that host.

## Management

```text
marzban up | down | restart | status | logs      start, stop, state, journal
marzban cli <command>                             admins, users, subscriptions (marzban cli --help)
marzban edit-env                                  edit .env      (then marzban restart)
marzban edit                                      edit the systemd unit
marzban update                                    update to the latest commit
marzban backup | backup-service                   manual / scheduled backup to Telegram
marzban core-update                               pick another Xray version
```

Directories: code in `/opt/marzban`, data (database, Xray config, certificates) in `/var/lib/marzban`.
Edit the Xray config in the dashboard (*Core Settings*) or in `/var/lib/marzban/xray_config.json`.

## Requirements and limitations

- The installer targets Debian/Ubuntu with systemd and Python ≥ 3.10; tested on **Debian 12 (Python 3.11)**.
  Ubuntu 22.04/24.04 should work but were not tested. Elsewhere install manually (`git clone`,
  `python3 -m venv venv`, `pip install -r requirements.txt`, `alembic upgrade head`, `python main.py`).
- Xray cannot drop an established Hysteria 2 session: a disabled or expired user loses access on the next
  connection, not instantly.
- Nodes ([marzban-node](https://github.com/Gozargah/Marzban-node)) need an Xray core with Hysteria support if
  you use that protocol. The fork was not tested with nodes.
- The Docker files come from upstream and are neither maintained nor tested in this fork.

## Development

```bash
git clone https://github.com/ungerofydev/marzkick.git && cd marzkick
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env && ./venv/bin/alembic upgrade head && ./venv/bin/python main.py
bash build_dashboard.sh        # rebuild the dashboard (needs Node.js >= 18)
```

## License and credits

Based on [Marzban](https://github.com/Gozargah/Marzban) (© Gozargah) and distributed under the same terms —
**GNU AGPL-3.0**, see [LICENSE](./LICENSE). Original documentation: <https://gozargah.github.io/marzban/>.
