#!/usr/bin/env bash
set -e

DOMAIN="vmi3245942.contaboserver.net"
EMAIL="admin@$DOMAIN" # replace with actual email if needed

echo "Setting up Let's Encrypt SSL certificates for $DOMAIN"

if ! command -v certbot &> /dev/null; then
    echo "Installing certbot..."
    sudo apt-get update
    sudo apt-get install -y certbot
fi

# Stop any service on port 80 if necessary, or use webroot
echo "Requesting certificate. Ensure port 80 is open and not used by another service."
sudo certbot certonly --standalone -d $DOMAIN --non-interactive --agree-tos -m $EMAIL

echo "Certificate obtained. Nginx container will mount /etc/letsencrypt directly."
