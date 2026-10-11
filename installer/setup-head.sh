#!/bin/sh
# Roasted (coffee log) installer for Alpine Linux (OpenRC) — CT 210, next to Cookbooks / Dinner Log / wish list.
# Re-runnable: upgrades the app in place; never touches existing data, and only *adds*
# missing settings to existing config files.
#
#   sh setup-roasted.sh               install / upgrade
#   ROASTED_PORT=8768 sh setup-roasted.sh
#   sh setup-roasted.sh --uninstall   remove app + service (keeps /var/lib/roasted and backups)
set -eu
APP_DIR=/opt/roasted
DATA_DIR=/var/lib/roasted
CONF=/etc/conf.d/roasted
OPS_CONF=/etc/conf.d/ct210-ops
SVC_USER=roasted
PORT="${ROASTED_PORT:-8768}"

if [ "$(id -u)" != 0 ]; then echo "Run as root"; exit 1; fi
command -v apk >/dev/null || { echo "This installer is for Alpine (apk)."; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  rc-service roasted stop 2>/dev/null || true
  rc-update del roasted default 2>/dev/null || true
  rm -f /etc/init.d/roasted /etc/periodic/daily/roasted-backup
  rm -rf "$APP_DIR"
  echo "Removed app, service and backup job. Data kept in $DATA_DIR, config in $CONF, backups in /var/backups/ct210."
  exit 0
fi

# an existing install keeps its port
[ -f "$CONF" ] && PORT=$(sed -n 's/^PORT=//p' "$CONF" | head -n1) && PORT=${PORT:-8768}

echo "==> Installing packages"
apk add --no-cache python3 py3-flask py3-gunicorn py3-pillow py3-jwt py3-cryptography sqlite >/dev/null

[ -x /etc/init.d/roasted ] && rc-service roasted stop >/dev/null 2>&1 || true
if netstat -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$PORT\$"; then
  echo "Port $PORT is already in use by something else. Re-run with ROASTED_PORT=<free port>."; exit 1
fi

echo "==> Creating user and folders"
id "$SVC_USER" >/dev/null 2>&1 || adduser -S -D -H -h "$DATA_DIR" -s /sbin/nologin "$SVC_USER"
mkdir -p "$APP_DIR" "$DATA_DIR/photos" /var/backups/ct210

echo "==> Unpacking app"
TMP=$(mktemp -d)
sed -n '/^__PAYLOAD_BELOW__$/,$p' "$0" | tail -n +2 | base64 -d | tar xz -C "$TMP"
rm -rf "$APP_DIR.new" && mv "$TMP/roasted" "$APP_DIR.new"
rm -rf "$APP_DIR.old"; [ -d "$APP_DIR" ] && mv "$APP_DIR" "$APP_DIR.old"
mv "$APP_DIR.new" "$APP_DIR"; rm -rf "$APP_DIR.old" "$TMP"
chown -R root:root "$APP_DIR"
chown -R "$SVC_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

# ---------------------------------------------------------------- config
add_setting() {  # add_setting FILE KEY VALUE COMMENT — only if KEY isn't there yet
  grep -q "^$2=" "$1" 2>/dev/null && return 0
  printf '\n# %s\n%s=%s\n' "$4" "$2" "$3" >> "$1"
  ADDED="${ADDED:-} $2"
}
from_cookbooks() { sed -n "s/^$1=//p" /etc/conf.d/cookbooks 2>/dev/null | head -n1; }

if [ ! -f "$CONF" ]; then
  echo "==> Writing $CONF"
  cat > "$CONF" <<EOF
# Roasted (coffee log) configuration — after editing: rc-service roasted restart
PORT=$PORT
DATA_DIR=$DATA_DIR
EOF
fi
add_setting "$CONF" CF_TEAM_DOMAIN "$(from_cookbooks CF_TEAM_DOMAIN)" "Cloudflare Access team domain, e.g. yourteam.cloudflareaccess.com"
add_setting "$CONF" CF_AUD "" "AUD tag of the Roasted Access application (path: admin)"
add_setting "$CONF" ADMIN_EMAILS "$(from_cookbooks ADMIN_EMAILS)" "Only these emails may edit (comma separated); empty = anyone the Access policy lets in"
add_setting "$CONF" LAN_EDIT 1 "1 = direct LAN requests (not through Cloudflare) may edit"
add_setting "$CONF" PUBLIC_VIEW 1 "1 = anyone can browse coffees/reviews at / (no prices, notes or GPS); 0 = everything behind /admin"
add_setting "$CONF" ANTHROPIC_API_KEY "$(from_cookbooks ANTHROPIC_API_KEY)" "Claude API key for reading bag labels from photos (console.anthropic.com)"
add_setting "$CONF" ANTHROPIC_MODEL claude-sonnet-5-5 "Model used for label reading"
add_setting "$CONF" DEFAULT_GRINDER '"Baratza Encore (M2 burrs)"' "Pre-filled grinder on new reviews"
add_setting "$CONF" DEFAULT_CURRENCY USD "Currency for new bags (change per bag when buying abroad)"
add_setting "$CONF" MCP_TOKEN "$(head -c 24 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')" \
  "Secret for private MCP tools: https://coffee.nickandrade.com/mcp/<MCP_TOKEN>"
add_setting "$CONF" MCP_PUBLIC 1 "1 = /mcp without a token serves public read-only tools"
chmod 640 "$CONF"; chown root:"$SVC_USER" "$CONF"

# health checks: add Roasted to the CT 210 list (app-health reads HEALTH_CHECKS from ct210-ops)
if [ -f "$OPS_CONF" ] && ! grep -q '^HEALTH_CHECKS=' "$OPS_CONF"; then
  cat >> "$OPS_CONF" <<EOF

# Apps checked every 15 min (name|url|service); added by setup-roasted.sh
HEALTH_CHECKS="Cookbooks|http://127.0.0.1:8767/healthz|cookbooks
Dinner Log|http://127.0.0.1:8765/|dinner-log
Wish list|http://127.0.0.1:8766/healthz|wishlist
Roasted|http://127.0.0.1:$PORT/healthz|roasted"
EOF
  HC=added
elif [ -f "$OPS_CONF" ] && ! grep -q '|roasted' "$OPS_CONF"; then
  HC=manual
fi

# ---------------------------------------------------------------- service
cat > /etc/init.d/roasted <<'EOF'
#!/sbin/openrc-run
name="roasted"
description="Roasted coffee log"
: ${PORT:=8768}
: ${DATA_DIR:=/var/lib/roasted}
export HOME=/var/lib/roasted  # newer gunicorn keeps a small control socket under $HOME
export PORT DATA_DIR CF_TEAM_DOMAIN CF_AUD ADMIN_EMAILS LAN_EDIT PUBLIC_VIEW ANTHROPIC_API_KEY ANTHROPIC_MODEL DEFAULT_GRINDER DEFAULT_CURRENCY MCP_TOKEN MCP_PUBLIC
supervisor="supervise-daemon"
respawn_delay=5
respawn_max=10
respawn_period=600
command="/usr/bin/gunicorn"
command_args="--preload --workers 2 --threads 4 --timeout 180 --bind 0.0.0.0:${PORT} --chdir /opt/roasted --access-logfile /var/log/roasted.log --error-logfile /var/log/roasted.log app:app"
command_user="roasted"
depend() { need net; after firewall; }
start_pre() { checkpath -f -o roasted /var/log/roasted.log; }
EOF
chmod +x /etc/init.d/roasted

# ---------------------------------------------------------------- backups
echo "==> Installing nightly backup"
mkdir -p /etc/periodic/daily
install -m 755 "$APP_DIR/ops/roasted-backup" /etc/periodic/daily/roasted-backup
rc-update add crond default >/dev/null 2>&1 || true
rc-service crond start >/dev/null 2>&1 || true

echo "==> Starting service"
rc-update add roasted default >/dev/null 2>&1 || true
rc-service roasted start
i=0
until wget -qO- "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; do
  i=$((i+1)); [ $i -gt 25 ] && { echo "Service didn't come up; see /var/log/roasted.log"; tail -n 20 /var/log/roasted.log; exit 1; }
  sleep 1
done

echo "==> Taking a first backup"
/etc/periodic/daily/roasted-backup || echo "    (backup reported a problem; see /var/log/ct210-ops.log)"

IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)
echo
echo "Roasted is running:  http://$IP:$PORT/        (editing on the LAN: http://$IP:$PORT/admin)"
echo "Health: $(wget -qO- http://127.0.0.1:$PORT/healthz)"
[ -n "${ADDED:-}" ] && echo "New settings added:${ADDED}"
grep -q '^ANTHROPIC_API_KEY=$' "$CONF" && echo "  - Label reading is off until you set ANTHROPIC_API_KEY in $CONF"
grep -q '^CF_AUD=$' "$CONF" && echo "  - Set CF_AUD in $CONF after creating the Access app for coffee.nickandrade.com/admin"
[ "${HC:-}" = added ] && echo "  - Added Roasted to HEALTH_CHECKS in $OPS_CONF"
[ "${HC:-}" = manual ] && echo "  - $OPS_CONF already has a custom HEALTH_CHECKS: add  Roasted|http://127.0.0.1:$PORT/healthz|roasted"
[ -f "$OPS_CONF" ] || echo "  - No $OPS_CONF (Cookbooks' ops jobs): backups still run nightly into /var/backups/ct210"
echo "  - Tunnel: coffee.nickandrade.com -> http://$IP:$PORT"
echo "  - Private MCP URL: https://coffee.nickandrade.com/mcp/$(sed -n 's/^MCP_TOKEN=//p' "$CONF")"
echo "Config: $CONF   Data: $DATA_DIR   Logs: /var/log/roasted.log, /var/log/ct210-ops.log"
exit 0
