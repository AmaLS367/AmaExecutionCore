# HTTPS Setup Guide

This document covers how to set up and maintain HTTPS with Let's Encrypt for AmaExecutionCore.

## Prerequisites

- VPS with Docker + Docker Compose v2
- Domain pointing to your server (DNS A record)
- Ports 80 and 443 open in firewall
- `.env` file configured

## Required `.env` Variables

```bash
DOMAIN_NAME=your-actual-domain.com
ADMIN_WHITELIST_IP=your.public.ip.here
CERTBOT_EMAIL=you@example.com     # optional, defaults to admin@DOMAIN_NAME
```

## Initial Setup

```bash
cd /opt/amaexecutioncore   # or wherever your project lives
bash scripts/setup_https.sh
```

The script will:

1. Validate `.env` values and DNS
2. Install `certbot` if needed
3. Stop nginx temporarily to free port 80
4. Issue a certificate via standalone challenge
5. Patch certbot to use webroot for future renewals
6. Install a deploy hook that reloads nginx after renewal
7. Enable `certbot.timer` (or add a cron fallback)
8. Start all Docker Compose services
9. Run `certbot renew --dry-run` to verify

## How It Works

### Architecture

```
                     Port 80        Port 443
Internet ──────────► nginx ────────► nginx (SSL)
                       │                 │
                       ├─ /.well-known/  │
                       │  → certbot/www  │
                       │                 ├─ / → ama_frontend:3000
                       └─ * → 301 HTTPS │
                                        ├─ /api/ → bot:8000/
                                        └─ /api/admin/ws/ → bot:8000/admin/ws/
```

### Certificate Renewal Flow

1. `certbot.timer` (or cron) runs `certbot renew` twice daily
2. Certbot uses **webroot** challenge — writes to `certbot/www/.well-known/`
3. Nginx serves the challenge file on port 80 (outside the whitelist block)
4. After successful renewal, the deploy hook runs: `docker exec ama_nginx nginx -s reload`
5. Nginx picks up the new certificate without downtime

### Why standalone for initial + webroot for renewal?

- **Initial**: No certificate exists yet → nginx can't start its 443 block → port 80 is free → standalone works
- **Renewal**: Nginx is running → we can't stop it → webroot challenge via `location /.well-known/` on port 80

## Verification Commands

```bash
# Check certificate details
sudo certbot certificates

# Test auto-renewal (no actual renewal)
sudo certbot renew --dry-run

# Check systemd timer
systemctl list-timers | grep certbot
systemctl status certbot.timer

# Check HTTPS
curl -I https://YOUR_DOMAIN/
curl https://YOUR_DOMAIN/api/health

# Check HTTP → HTTPS redirect
curl -I http://YOUR_DOMAIN/

# Check nginx logs
docker compose logs ama_nginx --tail 100
```

## Troubleshooting

### Nginx won't start (missing certificate)

If the certificate hasn't been issued yet, nginx will fail because it references
`/etc/letsencrypt/live/DOMAIN/fullchain.pem`. Run `setup_https.sh` to issue the cert first.

### Port 80 is in use

```bash
# Find what's using port 80
sudo ss -tlnp | grep ':80'

# Stop nginx if it's running
docker compose stop ama_nginx
```

### Certificate renewal fails

```bash
# Check certbot logs
sudo journalctl -u certbot --no-pager --since "1 hour ago"

# Check renewal config
sudo cat /etc/letsencrypt/renewal/YOUR_DOMAIN.conf

# Verify webroot is accessible
# From another machine:
curl http://YOUR_DOMAIN/.well-known/acme-challenge/test
# Should return 404 (not 403/connection refused)
```

### Rolling back

If nginx is broken after changes:

```bash
# Check what's wrong
docker compose logs ama_nginx --tail 50

# Restart just nginx
docker compose restart ama_nginx

# If template is broken, check generated config
docker compose exec ama_nginx cat /etc/nginx/nginx.conf

# Nuclear option: rebuild
docker compose up -d --force-recreate ama_nginx
```

## Files Overview

| File | Purpose |
|------|---------|
| `nginx/nginx.conf.template` | Nginx config with `${DOMAIN_NAME}` and `${ADMIN_WHITELIST_IP}` variables |
| `certbot/www/` | Webroot directory for ACME challenges (mounted read-only into nginx) |
| `scripts/setup_https.sh` | One-time setup script |
| `/etc/letsencrypt/` | Host-level certificate storage (mounted read-only into nginx) |
| `/etc/letsencrypt/renewal-hooks/deploy/ama-nginx-reload.sh` | Post-renewal hook |
