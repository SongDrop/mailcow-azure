def generate_setup(
    DOMAIN_NAME,
    ADMIN_EMAIL,
    MAILCOW_ADMIN_PASSWORD
):
    # Prepare domain variants for nginx server_name
    base_domain = DOMAIN_NAME.split('.', 1)[-1] if DOMAIN_NAME.count('.') > 1 else DOMAIN_NAME
    mail_domain = DOMAIN_NAME
    nginx_conf_filename = f"/etc/nginx/sites-available/{base_domain}"

    # Nginx config text with placeholders replaced
    # Added location for HTTP challenge at port 80 to allow certbot verification
    nginx_config = f"""
server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;

    server_name {base_domain} {mail_domain} autodiscover.{base_domain} autoconfig.{base_domain};
    client_max_body_size 1G;

    location / {{
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    ssl_certificate /etc/letsencrypt/live/{base_domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{base_domain}/privkey.pem;

    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:50m;
    ssl_session_tickets off;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers 'ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-GCM-SHA256';

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Permissions-Policy "geolocation=(), microphone=()" always;
    add_header X-XSS-Protection "1; mode=block" always;
}}

server {{
    listen 80;
    listen [::]:80;

    server_name {base_domain} {mail_domain} autodiscover.{base_domain} autoconfig.{base_domain};

    # Allow Let's Encrypt ACME HTTP challenge
    location /.well-known/acme-challenge/ {{
        root /var/www/html;
    }}

    # Redirect everything else to HTTPS
    location / {{
        return 301 https://$host$request_uri;
    }}
}}
"""

    certbot_install = """
echo "Installing certbot..."
apt-get install -y certbot
"""

    certbot_command = f"""
echo "Requesting Let's Encrypt certificate via HTTP challenge..."
# Ensure webroot exists
mkdir -p /var/www/html

certbot certonly --webroot -w /var/www/html \\
  -d {base_domain} -d {DOMAIN_NAME} -d autodiscover.{base_domain} -d autoconfig.{base_domain} \\
  --agree-tos --no-eff-email --email {ADMIN_EMAIL} --non-interactive
"""

    script_template = f"""#!/bin/bash

set -e

# Validate DOMAIN_NAME is a proper FQDN
if [[ ! "{DOMAIN_NAME}" =~ ^[a-zA-Z0-9.-]+\\.[a-zA-Z]{{2,}}$ ]]; then
    echo "ERROR: {DOMAIN_NAME} is not a valid FQDN (e.g., {DOMAIN_NAME})"
    exit 1
fi

DOMAIN_NAME="{DOMAIN_NAME}"
ADMIN_EMAIL="{ADMIN_EMAIL}"
MAILCOW_ADMIN_PASSWORD="{MAILCOW_ADMIN_PASSWORD}"
MAILCOW_DIR="/opt/mailcow-dockerized"
MAILCOW_GIT="https://github.com/mailcow/mailcow-dockerized.git"

# Required ports for Mailcow (ensure these are open on your firewall/NSG):
# 22 (SSH), 25 (SMTP), 465 (SMTPS), 587 (Submission), 110 (POP3), 995 (POP3S),
# 143 (IMAP), 993 (IMAPS), 4190 (Sieve), 80 (HTTP), 443 (HTTPS), 8080 (Mailcow UI)

echo "Updating system and installing dependencies..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y curl docker.io git netcat-openbsd ufw nginx

echo "Installing docker-compose..."
DOCKER_COMPOSE_VERSION="v2.24.5"
curl -SL https://github.com/docker/compose/releases/download/$DOCKER_COMPOSE_VERSION/docker-compose-linux-x86_64 -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose
ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose

echo "Enabling and starting Docker service..."
systemctl enable docker
systemctl start docker

echo "Creating Mailcow directory..."
mkdir -p "$MAILCOW_DIR"
cd "$MAILCOW_DIR" || exit 1

if [ -d ".git" ]; then
    echo "Mailcow repo already exists, pulling latest changes..."
    git pull
else
    echo "Cloning Mailcow repository..."
    git clone "$MAILCOW_GIT" .
fi

echo "Generating Mailcow configuration..."
# Non-interactive input:
# 1) Hostname = DOMAIN_NAME
# 2) Timezone = Etc/UTC
# 3) Branch selection = 1 (master)
printf '%s\nEtc/UTC\n1\n' "${DOMAIN_NAME}" | ./generate_config.sh

echo "Configuring Mailcow admin credentials..."
sed -i "s/^MAILCOW_ADMIN_PASS=.*/MAILCOW_ADMIN_PASS=${{MAILCOW_ADMIN_PASSWORD}}/" mailcow.conf
sed -i "s/^MAILCOW_ADMIN_EMAIL=.*/MAILCOW_ADMIN_EMAIL=${{ADMIN_EMAIL}}/" mailcow.conf

# Add additional SANs if needed
echo "ADDITIONAL_SAN=webmail.${{DOMAIN_NAME}},admin.${{DOMAIN_NAME}}" >> mailcow.conf

echo "Allowing required ports through UFW..."
ufw allow 22/tcp
ufw allow 25/tcp
ufw allow 465/tcp
ufw allow 587/tcp
ufw allow 110/tcp
ufw allow 995/tcp
ufw allow 143/tcp
ufw allow 993/tcp
ufw allow 4190/tcp
ufw allow 80/tcp
ufw allow 443/tcp

# Enable UFW if not enabled
ufw status | grep -qw inactive && echo "Enabling UFW firewall..." && ufw --force enable

ufw reload || true

echo "Pulling and starting Mailcow containers..."
docker-compose pull


echo "Certbot install..."   
{certbot_install}
echo "Certbot commands..."   
{certbot_command}

echo "Docker starting"
docker-compose up -d

echo "Writing Nginx configuration file to {nginx_conf_filename}..."
cat > {nginx_conf_filename} <<EOF
{nginx_config}
EOF

echo "Enabling Nginx site and reloading service..."
ln -sf {nginx_conf_filename} /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx

echo "Mailcow setup completed successfully with Nginx reverse proxy and SSL!"

echo ""
echo "IMPORTANT DNS Records to configure for {DOMAIN_NAME} (replace YOUR_IPV4 and YOUR_IPV6 accordingly):"
echo "A Record: {DOMAIN_NAME} -> Your Server IP"
echo "CNAME: autodiscover -> {DOMAIN_NAME}"
echo "CNAME: autoconfig -> {DOMAIN_NAME}"
echo "MX Record for {base_domain}: {DOMAIN_NAME} with highest priority"
echo "SRV Record: _autodiscover._tcp -> 0 5 443 {DOMAIN_NAME}"
echo "TXT Record (SPF): \"v=spf1 ip4:YOUR_IPV4 ip6:YOUR_IPV6 -all\""
echo "TXT Record (_DMARC): \"v=DMARC1; p=quarantine; adkim=s; aspf=s\""
echo ""
echo "After Mailcow startup, add these DNS records from Mailcow UI:"
echo "- TLSA for _25._tcp.{DOMAIN_NAME}"
echo "- TXT for dkim._domainkey.{base_domain}"
echo ""
echo "Mailcow is now accessible at: https://{base_domain}/"
echo "Admin email: {ADMIN_EMAIL}"
echo "Admin password: {MAILCOW_ADMIN_PASSWORD}"
"""
    return script_template