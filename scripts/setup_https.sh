#!/usr/bin/env bash
# -------------------------------------------------------------------
# setup_https.sh — Initial Let's Encrypt certificate + auto-renew
#
# Strategy:
#   1. First run: standalone challenge (nginx is stopped).
#   2. Patch certbot renewal config → webroot (nginx serves challenges).
#   3. Deploy hook reloads nginx after every renewal.
#   4. Host-level certbot systemd timer handles auto-renew.
#
# Prerequisites:
#   - Run from the project root (where docker-compose.yml lives).
#   - .env must contain DOMAIN_NAME and ADMIN_WHITELIST_IP.
#   - DNS A record for DOMAIN_NAME must point to this server.
#   - Ports 80 and 443 must be reachable from the internet.
# -------------------------------------------------------------------
set -euo pipefail

# ── helpers ────────────────────────────────────────────────────────
red()   { echo -e "\033[0;31m$*\033[0m"; }
green() { echo -e "\033[0;32m$*\033[0m"; }
info()  { echo -e "\033[0;36m→ $*\033[0m"; }

fail() { red "ERROR: $*"; exit 1; }

# ── load .env ──────────────────────────────────────────────────────
[ -f .env ] || fail ".env file not found. Copy .env.example → .env and fill it in."

# Source only the vars we need (avoid eval-ing everything)
DOMAIN_NAME=$(grep -E '^DOMAIN_NAME=' .env | head -1 | cut -d= -f2-)
ADMIN_WHITELIST_IP=$(grep -E '^ADMIN_WHITELIST_IP=' .env | head -1 | cut -d= -f2-)
CERTBOT_EMAIL=$(grep -E '^CERTBOT_EMAIL=' .env | head -1 | cut -d= -f2- || true)

# ── validate ───────────────────────────────────────────────────────
[ -n "$DOMAIN_NAME" ] && [ "$DOMAIN_NAME" != "your-domain.com" ] \
    || fail "DOMAIN_NAME is not set or still has placeholder value in .env"

[ -n "$ADMIN_WHITELIST_IP" ] && [ "$ADMIN_WHITELIST_IP" != "127.0.0.1" ] \
    || fail "ADMIN_WHITELIST_IP is not set or still has placeholder value in .env"

EMAIL="${CERTBOT_EMAIL:-admin@$DOMAIN_NAME}"

command -v docker >/dev/null 2>&1      || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 is not available"

# Check DNS resolves to this server
info "Checking DNS for $DOMAIN_NAME..."
RESOLVED_IP=$(dig +short "$DOMAIN_NAME" A 2>/dev/null | tail -1 || true)
SERVER_IP=$(curl -s4 ifconfig.me 2>/dev/null || true)

if [ -n "$RESOLVED_IP" ] && [ -n "$SERVER_IP" ]; then
    if [ "$RESOLVED_IP" != "$SERVER_IP" ]; then
        red "WARNING: DNS for $DOMAIN_NAME resolves to $RESOLVED_IP but this server is $SERVER_IP"
        red "Let's Encrypt validation will fail if DNS is wrong."
        read -rp "Continue anyway? [y/N] " yn
        [ "$yn" = "y" ] || [ "$yn" = "Y" ] || exit 1
    else
        green "DNS OK: $DOMAIN_NAME → $SERVER_IP"
    fi
else
    red "WARNING: Could not verify DNS (dig or curl unavailable). Proceeding..."
fi

PROJECT_DIR=$(pwd)
WEBROOT_PATH="$PROJECT_DIR/certbot/www"
mkdir -p "$WEBROOT_PATH"

# ── install certbot if missing ─────────────────────────────────────
if ! command -v certbot &>/dev/null; then
    info "Installing certbot..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq certbot
fi

# ── obtain certificate ─────────────────────────────────────────────
if sudo test -d "/etc/letsencrypt/live/$DOMAIN_NAME"; then
    green "Certificate for $DOMAIN_NAME already exists. Skipping issuance."
else
    info "Requesting certificate for $DOMAIN_NAME via standalone challenge..."

    # Free port 80 — stop nginx if running
    if docker ps --format '{{.Names}}' | grep -q '^ama_nginx$'; then
        info "Stopping ama_nginx to free port 80..."
        docker compose stop ama_nginx
    fi

    # Check port 80 is free
    if ss -tlnp 2>/dev/null | grep -q ':80 '; then
        fail "Port 80 is still in use. Free it before running this script."
    fi

    sudo certbot certonly \
        --standalone \
        -d "$DOMAIN_NAME" \
        --non-interactive \
        --agree-tos \
        -m "$EMAIL"

    green "Certificate obtained successfully."

    # ── patch renewal config → webroot ─────────────────────────────
    info "Switching renewal authenticator from standalone → webroot..."
    RENEWAL_CONF="/etc/letsencrypt/renewal/$DOMAIN_NAME.conf"

    if sudo test -f "$RENEWAL_CONF"; then
        sudo sed -i 's/authenticator = standalone/authenticator = webroot/' "$RENEWAL_CONF"

        # Add or update webroot path
        if sudo grep -q '^\[webroot\]' "$RENEWAL_CONF" || sudo grep -q 'webroot_path' "$RENEWAL_CONF"; then
            sudo sed -i "s|webroot_path = .*|webroot_path = $WEBROOT_PATH|" "$RENEWAL_CONF"
        else
            {
                echo ""
                echo "[webroot]"
                echo "webroot_path = $WEBROOT_PATH"
                echo ""
                echo "[[webroot_map]]"
                echo "$DOMAIN_NAME = $WEBROOT_PATH"
            } | sudo tee -a "$RENEWAL_CONF" >/dev/null
        fi
        green "Renewal config patched for webroot."
    else
        red "WARNING: Could not find $RENEWAL_CONF — manual webroot config may be needed."
    fi
fi

# ── deploy hook for nginx reload ───────────────────────────────────
info "Installing certbot deploy hook..."
HOOK_DIR="/etc/letsencrypt/renewal-hooks/deploy"
sudo mkdir -p "$HOOK_DIR"

sudo tee "$HOOK_DIR/ama-nginx-reload.sh" >/dev/null <<'HOOK'
#!/usr/bin/env bash
# Reload nginx inside Docker after cert renewal
docker exec ama_nginx nginx -s reload 2>/dev/null \
    || docker restart ama_nginx 2>/dev/null \
    || echo "WARNING: could not reload/restart ama_nginx"
HOOK
sudo chmod +x "$HOOK_DIR/ama-nginx-reload.sh"
green "Deploy hook installed."

# ── ensure systemd certbot timer is active ─────────────────────────
if systemctl list-unit-files certbot.timer &>/dev/null; then
    if ! systemctl is-active --quiet certbot.timer; then
        info "Enabling certbot.timer..."
        sudo systemctl enable --now certbot.timer
    fi
    green "certbot.timer is active."
else
    info "No certbot.timer found. Setting up cron fallback..."
    CRON_LINE="0 3 * * * certbot renew --quiet"
    (sudo crontab -l 2>/dev/null | grep -v 'certbot renew' || true; echo "$CRON_LINE") \
        | sudo crontab -
    green "Cron job installed (daily 03:00)."
fi

# ── start everything ───────────────────────────────────────────────
info "Starting Docker Compose services..."
docker compose up -d

echo ""
info "Running certbot renew --dry-run to verify auto-renew..."
if sudo certbot renew --dry-run; then
    green "Dry-run successful — auto-renew is working."
else
    red "Dry-run FAILED. Check certbot logs: sudo journalctl -u certbot"
fi

echo ""
green "═══════════════════════════════════════════════════════"
green " HTTPS setup complete for $DOMAIN_NAME"
green "═══════════════════════════════════════════════════════"
echo ""
echo "  Verify:  curl -I https://$DOMAIN_NAME/"
echo "  API:     curl https://$DOMAIN_NAME/api/health"
echo "  Logs:    docker compose logs ama_nginx --tail 50"
echo "  Certs:   sudo certbot certificates"
echo "  Renew:   sudo certbot renew --dry-run"
echo ""
