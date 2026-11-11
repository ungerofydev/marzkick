#!/usr/bin/env bash
# Marzkick (Marzban fork) - installer / updater / uninstaller for Debian & Ubuntu.
#
#   sudo bash -c "$(curl -sL https://raw.githubusercontent.com/ungerofydev/marzkick/main/install.sh)"
#   sudo bash -c "$(curl -sL .../install.sh)" @ update
#   sudo bash -c "$(curl -sL .../install.sh)" @ uninstall
#
# Run again on an installed system and it updates instead. Everything can be preset with
# environment variables (see the table in README.md): PORT, ADMIN_USERNAME, ADMIN_PASSWORD,
# TELEGRAM_API_TOKEN, TELEGRAM_ADMIN_ID, CERT_FILE, KEY_FILE, XRAY_PORT, REPO, BRANCH, APP_NAME ...
set -Eeuo pipefail

REPO="${REPO:-https://github.com/ungerofydev/marzkick.git}"
BRANCH="${BRANCH:-main}"
APP_NAME="${APP_NAME:-marzban}"
INSTALL_ROOT="${INSTALL_ROOT:-/opt}"
APP_DIR="$INSTALL_ROOT/$APP_NAME"
DATA_DIR="/var/lib/$APP_NAME"
ENV_FILE="$APP_DIR/.env"
PORT="${PORT:-8000}"
XRAY_BIN="/usr/local/bin/xray"
XRAY_ASSETS="/usr/local/share/xray"
VENV="$APP_DIR/venv"
CLI_BIN="/usr/local/bin/$APP_NAME-cli"
CMD_BIN="/usr/local/bin/$APP_NAME"
ACTION="${1:-install}"
ASSUME_YES="${ASSUME_YES:-}"

# --------------------------------------------------------------------------- output helpers
if [ -t 1 ]; then C_R=$'\e[91m'; C_G=$'\e[92m'; C_Y=$'\e[93m'; C_B=$'\e[94m'; C_0=$'\e[0m'; else C_R= C_G= C_Y= C_B= C_0=; fi
info() { printf '%s==>%s %s\n' "$C_B" "$C_0" "$*"; }
ok()   { printf '%s ok%s %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '%s !!%s %s\n' "$C_Y" "$C_0" "$*" >&2; }
die()  { printf '%s xx%s %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }
trap 'die "failed at line $LINENO (command: $BASH_COMMAND)"' ERR

# reads from the terminal even when the script itself is piped into bash
ask() { # ask VAR "question" [default]
    local __var="$1" __q="$2" __def="${3:-}" __ans=""
    if [ -n "${!__var:-}" ]; then return; fi
    if [ -n "$ASSUME_YES" ] || ! [ -r /dev/tty ] || ! { : </dev/tty; } 2>/dev/null; then
        printf -v "$__var" '%s' "$__def"; return
    fi
    read -r -p "$__q${__def:+ [$__def]}: " __ans </dev/tty || true
    printf -v "$__var" '%s' "${__ans:-$__def}"
}
confirm() { # confirm "question" -> 0 yes
    [ -n "$ASSUME_YES" ] && return 0
    { [ -r /dev/tty ] && : </dev/tty; } 2>/dev/null || return 1
    local a; read -r -p "$1 [y/N] " a </dev/tty || true
    [[ "$a" =~ ^[Yy]$ ]]
}
random_string() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-16}" || true; }

need_root() { [ "$(id -u)" = "0" ] || die "run as root (use sudo)"; }

# the unit created by older dev installs was called marzban-dev
service_name() {
    if systemctl cat "$APP_NAME" >/dev/null 2>&1; then echo "$APP_NAME"
    elif [ "$APP_NAME" = marzban ] && systemctl cat marzban-dev >/dev/null 2>&1; then echo marzban-dev
    else echo "$APP_NAME"; fi
}
SERVICE="$(service_name)"

# --------------------------------------------------------------------------- steps
check_system() {
    command -v apt-get >/dev/null || die "only Debian/Ubuntu (apt) are supported by this installer"
    command -v systemctl >/dev/null || die "systemd is required"
    [ -d /run/systemd/system ] || die "systemd is not running (containers are not supported, use Docker/manual install)"
    case "$(uname -m)" in x86_64|aarch64) ;; *) die "unsupported CPU architecture: $(uname -m)";; esac
}

install_packages() {
    info "Installing system packages"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq git curl unzip openssl ca-certificates python3 python3-venv python3-dev \
        build-essential libffi-dev libssl-dev >/dev/null
    python3 - <<'PY' || die "Python 3.10+ is required (found $(python3 --version 2>&1))"
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
    ok "python $(python3 -c 'import sys;print("%d.%d.%d" % sys.version_info[:3])')"
}

fetch_code() {
    local url="$REPO"
    if [ -n "${GITHUB_TOKEN:-}" ] && [[ "$url" == https://github.com/* ]]; then   # private repositories
        url="https://x-access-token:${GITHUB_TOKEN}@${url#https://}"
    fi
    if [ -d "$APP_DIR/.git" ]; then
        info "Updating code in $APP_DIR"
        git -C "$APP_DIR" fetch -q "$url" "$BRANCH"
        git -C "$APP_DIR" merge -q --ff-only FETCH_HEAD \
            || die "local changes in $APP_DIR prevent a fast-forward update; commit/stash them first"
    else
        [ -e "$APP_DIR" ] && [ -n "$(ls -A "$APP_DIR" 2>/dev/null)" ] && die "$APP_DIR exists and is not a git checkout"
        info "Downloading $REPO ($BRANCH) to $APP_DIR"
        git clone -q --branch "$BRANCH" "$url" "$APP_DIR"
        git -C "$APP_DIR" remote set-url origin "$REPO"   # never keep a token in the config
    fi
    ok "at $(git -C "$APP_DIR" rev-parse --short HEAD)"
}

setup_python() {
    info "Installing Python dependencies (this takes a minute)"
    [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip wheel
    "$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"
    ok "dependencies installed"
}

install_xray() {
    if [ -x "$XRAY_BIN" ] && [ -z "${UPDATE_XRAY:-}" ]; then
        ok "xray already installed ($("$XRAY_BIN" version | head -1 | cut -d' ' -f1-2)); set UPDATE_XRAY=1 to refresh"
        return
    fi
    info "Installing Xray-core"
    local arch tmp base
    case "$(uname -m)" in x86_64) arch=64;; aarch64) arch=arm64-v8a;; esac
    base="https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-$arch.zip"
    tmp="$(mktemp -d)"
    curl -fsSL -o "$tmp/xray.zip" "$base"
    curl -fsSL -o "$tmp/xray.dgst" "$base.dgst"
    local want have
    want="$(awk '/SHA2-256/ {print $NF}' "$tmp/xray.dgst" | head -1)"
    have="$(sha256sum "$tmp/xray.zip" | awk '{print $1}')"
    [ -n "$want" ] && [ "$want" = "$have" ] || die "Xray checksum mismatch (expected '$want', got '$have')"
    unzip -qo "$tmp/xray.zip" -d "$tmp/x"
    install -m 755 "$tmp/x/xray" "$XRAY_BIN"
    mkdir -p "$XRAY_ASSETS"
    cp "$tmp/x/geoip.dat" "$tmp/x/geosite.dat" "$XRAY_ASSETS/"
    rm -rf "$tmp"
    ok "$("$XRAY_BIN" version | head -1 | cut -d' ' -f1-2)"
}

# set_env KEY VALUE [mode] - replace (or add) KEY in .env, keeping the documented template layout.
# mode: (empty) KEY = "VALUE" | raw KEY = VALUE | nospace KEY="VALUE" (the format `marzban core-update` edits)
set_env() {
    ENV_FILE="$ENV_FILE" KEY="$1" VALUE="$2" MODE="${3:-}" python3 - <<'PY'
import os, re
path, key, value, mode = os.environ["ENV_FILE"], os.environ["KEY"], os.environ["VALUE"], os.environ.get("MODE", "")
line = {"raw": f'{key} = {value}', "nospace": f'{key}="{value}"'}.get(mode, f'{key} = "{value}"')
text = open(path).read().split("\n")
pat = re.compile(r"^#? ?" + re.escape(key) + r" ?=")
for i, t in enumerate(text):
    if pat.match(t):
        text[i] = line
        break
else:
    text.append(line)
open(path, "w").write("\n".join(text))
PY
}

write_config() {
    mkdir -p "$DATA_DIR"
    if [ -f "$ENV_FILE" ]; then
        ok "keeping existing $ENV_FILE"
    else
        info "Writing configuration"
        cp "$APP_DIR/.env.example" "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        set_env UVICORN_HOST "0.0.0.0"
        set_env UVICORN_PORT "$PORT" raw
        set_env SQLALCHEMY_DATABASE_URL "sqlite:///$DATA_DIR/db.sqlite3"
        set_env XRAY_JSON "$DATA_DIR/xray_config.json"
        set_env XRAY_EXECUTABLE_PATH "$XRAY_BIN" nospace
        set_env XRAY_ASSETS_PATH "$XRAY_ASSETS"
        if [ -n "${CERT_FILE:-}" ] && [ -n "${KEY_FILE:-}" ]; then
            [ -r "$CERT_FILE" ] && [ -r "$KEY_FILE" ] || die "CERT_FILE / KEY_FILE are not readable"
            set_env UVICORN_SSL_CERTFILE "$CERT_FILE"
            set_env UVICORN_SSL_KEYFILE "$KEY_FILE"
            # Marzban refuses self-signed certificates unless the CA type is "private"
            if [ "$(openssl x509 -noout -issuer -in "$CERT_FILE" | sed 's/^issuer= *//')" =                  "$(openssl x509 -noout -subject -in "$CERT_FILE" | sed 's/^subject= *//')" ]; then
                set_env UVICORN_SSL_CA_TYPE "private"
                warn "$CERT_FILE is self-signed: UVICORN_SSL_CA_TYPE=private was set (browsers will warn)"
            fi
            ok "panel will use HTTPS with $CERT_FILE"
        fi
        configure_telegram
    fi
    if [ ! -f "$DATA_DIR/xray_config.json" ]; then
        cp "$APP_DIR/xray_config.json" "$DATA_DIR/xray_config.json"
        if [ -n "${XRAY_PORT:-}" ]; then
            sed -i -E "s/\"port\": 1080/\"port\": $XRAY_PORT/" "$DATA_DIR/xray_config.json"
        fi
    fi
}

configure_telegram() {
    local token="${TELEGRAM_API_TOKEN:-}" admin="${TELEGRAM_ADMIN_ID:-}"
    ask token "Telegram bot token from @BotFather (Enter to skip)" ""
    [ -z "$token" ] && return
    local me
    me="$(curl -fsS -m 15 "${TELEGRAM_API_URL:-https://api.telegram.org}/bot$token/getMe" 2>/dev/null || true)"
    if ! grep -Eq '"ok": ?true' <<<"$me"; then
        warn "Telegram rejected this token, bot left disabled (set it later with: $APP_NAME edit-env)"
        return
    fi
    ok "bot $(grep -o '"username":"[^"]*"' <<<"$me" | head -1)"
    ask admin "Your numeric Telegram id (message @userinfobot to get it)" ""
    if ! [[ "$admin" =~ ^[0-9]+([,[:space:]]+[0-9]+)*$ ]]; then
        warn "no valid admin id given, bot left disabled (set TELEGRAM_API_TOKEN and TELEGRAM_ADMIN_ID with: $APP_NAME edit-env)"
        return
    fi
    set_env TELEGRAM_API_TOKEN "$token" raw
    set_env TELEGRAM_ADMIN_ID "$admin" raw
    ok "Telegram bot enabled"
}

install_commands() {
    info "Installing commands"
    cat >"$CLI_BIN" <<EOF
#!/bin/sh
cd "$APP_DIR" && exec env MARZBAN_CLI_SOCKET="$DATA_DIR/cli.sock" "$VENV/bin/python" marzban-cli.py "\$@"
EOF
    cat >"$CMD_BIN" <<EOF
#!/bin/sh
exec env APP_NAME="$APP_NAME" "$APP_DIR/scripts/marzban.sh" "\$@"
EOF
    chmod 755 "$CLI_BIN" "$CMD_BIN" "$APP_DIR/scripts/marzban.sh"
    ok "$CMD_BIN, $CLI_BIN"
}

install_service() {
    info "Installing systemd service '$SERVICE'"
    cat >"/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Marzkick (Marzban fork) - $APP_NAME
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$APP_DIR
ExecStartPre=$VENV/bin/alembic upgrade head
ExecStart=$VENV/bin/python main.py
Restart=always
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable -q "$SERVICE"
}

migrate_db() {
    info "Applying database migrations"
    (cd "$APP_DIR" && "$VENV/bin/alembic" upgrade head >/dev/null 2>&1) || die "database migration failed (run: cd $APP_DIR && $VENV/bin/alembic upgrade head)"
    ok "database is up to date"
}

create_admin() {
    local user="${ADMIN_USERNAME:-}" pass="${ADMIN_PASSWORD:-}"
    if (cd "$APP_DIR" && MARZBAN_CLI_NO_FAST=1 "$VENV/bin/python" marzban-cli.py admin list </dev/null 2>/dev/null | grep -q '│'); then
        ADMIN_SUMMARY="(existing admins were kept)"
        return
    fi
    ask user "Panel admin username" "admin"
    [ -z "$user" ] && user=admin
    if [ -z "$pass" ]; then pass="$(random_string 16)"; GENERATED_PASS=1; fi
    (cd "$APP_DIR" && MARZBAN_CLI_NO_FAST=1 MARZBAN_ADMIN_PASSWORD="$pass" \
        "$VENV/bin/python" marzban-cli.py admin create -u "$user" --sudo -tg 0 -dc "" </dev/null >/dev/null)
    ADMIN_SUMMARY="username: $user   password: $pass${GENERATED_PASS:+   (generated, change it in the panel)}"
    ok "admin '$user' created"
}

start_service() {
    info "Starting $SERVICE"
    systemctl restart "$SERVICE"
    local i p; p="$(panel_port)"; p="${p:-$PORT}"
    for i in $(seq 1 40); do
        if curl -fs -o /dev/null -m 2 "http://127.0.0.1:$p/dashboard/" 2>/dev/null \
           || curl -fsk -o /dev/null -m 2 "https://127.0.0.1:$p/dashboard/" 2>/dev/null; then
            ok "panel is up"; return
        fi
        systemctl is-active -q "$SERVICE" || break
        sleep 1
    done
    warn "the panel did not answer yet; check: journalctl -u $SERVICE -n 50"
}

panel_port() { grep -E '^UVICORN_PORT' "$ENV_FILE" 2>/dev/null | head -1 | tr -dc '0-9' || true; }

summary() {
    local port; port="$(panel_port)"; port="${port:-$PORT}"
    local scheme=http; grep -qE '^UVICORN_SSL_CERTFILE' "$ENV_FILE" && scheme=https
    echo
    printf '%s================================================================%s\n' "$C_G" "$C_0"
    printf '%s Marzkick is installed%s\n' "$C_G" "$C_0"
    printf '%s================================================================%s\n' "$C_G" "$C_0"
    if [ "$scheme" = https ]; then
        echo " Panel:      https://<your-domain>:$port/dashboard/"
    else
        echo " Panel:      http://127.0.0.1:$port/dashboard/   (without HTTPS the panel only listens on localhost)"
        echo "             from your computer:  ssh -L $port:127.0.0.1:$port root@<server-ip>   then open the URL above"
        echo "             or set CERT_FILE/KEY_FILE (see README) to serve it over HTTPS"
    fi
    [ -n "${ADMIN_SUMMARY:-}" ] && echo " Admin:      $ADMIN_SUMMARY"
    echo " Commands:   $APP_NAME help     |  $APP_NAME logs  |  $APP_NAME edit-env  |  $APP_NAME update"
    echo " CLI:        $APP_NAME cli --help"
    grep -qE '^TELEGRAM_API_TOKEN' "$ENV_FILE" && echo " Telegram:   send /start to your bot, then use Settings" \
        || echo " Telegram:   not enabled (set TELEGRAM_API_TOKEN and TELEGRAM_ADMIN_ID with: $APP_NAME edit-env)"
    echo
}

# --------------------------------------------------------------------------- actions
do_install() {
    need_root; check_system
    local fresh=1; [ -d "$APP_DIR/.git" ] && fresh=0
    install_packages
    fetch_code
    setup_python
    install_xray
    write_config
    install_commands
    install_service
    migrate_db
    [ "$fresh" = 1 ] && create_admin || ADMIN_SUMMARY="(existing install: admins and settings were kept)"
    start_service
    [ "$fresh" = 1 ] && summary || { echo; ok "update finished"; }
}

do_uninstall() {
    need_root
    [ -d "$APP_DIR" ] || die "$APP_DIR does not exist - nothing to uninstall"
    confirm "Remove $APP_NAME (code in $APP_DIR, service $SERVICE)?" || die "aborted"
    info "Removing"
    systemctl disable --now "$SERVICE" 2>/dev/null || true
    pkill -f "$VENV/bin/python -m cli.fast_server" 2>/dev/null || true
    rm -f "/etc/systemd/system/$SERVICE.service" "$CLI_BIN" "$CMD_BIN"
    systemctl daemon-reload
    rm -rf "$APP_DIR"
    if [ -n "${PURGE:-}" ] || confirm "Also delete the data in $DATA_DIR (database, Xray config, certificates)?"; then
        rm -rf "$DATA_DIR"; ok "data removed"
    else
        ok "data kept in $DATA_DIR"
    fi
    ok "uninstalled (Xray in $XRAY_BIN and system packages were left in place)"
}

case "$ACTION" in
    install|update|upgrade) do_install ;;
    uninstall|remove)       do_uninstall ;;
    *) die "unknown command '$ACTION' (use: install | update | uninstall)" ;;
esac
