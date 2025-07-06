def generate_setup(
    DOMAIN_NAME,
    ADMIN_EMAIL,
    MAILCOW_ADMIN_PASSWORD
):
    def get_base_domain(domain):
        domain = domain.strip('.')
        parts = domain.split('.')
        if len(parts) < 2:
            raise ValueError(f"'{domain}' is not a valid FQDN to derive base domain")
        return '.'.join(parts[-2:])

    # Validate and extract base domain
    base_domain = get_base_domain(DOMAIN_NAME)

    if base_domain.startswith('.') or '..' in base_domain or base_domain == '':
        raise ValueError(f"Invalid base domain derived: '{base_domain}' from '{DOMAIN_NAME}'")

    # Validate FQDN in Python instead of Bash
    import re
    fqdn_pattern = re.compile(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    if not fqdn_pattern.match(DOMAIN_NAME):
        raise ValueError(f"{DOMAIN_NAME} is not a valid FQDN (e.g., smtp.example.com)")

    script_template = f"""#!/bin/bash

set -e

echo "DOMAIN_NAME={DOMAIN_NAME}"
echo "base_domain={base_domain}"
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

echo "Updating system and installing dependencies..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y curl docker.io git netcat-openbsd ufw software-properties-common

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
printf '%s\\nEtc/UTC\\n1\\n' "${{DOMAIN_NAME}}" | ./generate_config.sh

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

ufw status | grep -qw inactive && echo "Enabling UFW firewall..." && ufw --force enable

ufw reload || true

echo "Pulling and starting Mailcow containers..."
docker-compose pull

echo "Docker starting"
docker-compose up -d

echo "Installing certbot for wildcard SSL certificate..."
apt-get install -y certbot

echo "Requesting wildcard SSL certificate for ${{DOMAIN_NAME}}..."
certbot certonly --manual \\
  --preferred-challenges=dns \\
  --email "${{ADMIN_EMAIL}}" \\
  --agree-tos \\
  --no-eff-email \\
  -d "${{DOMAIN_NAME}}"

echo "Linking wildcard certificates to Mailcow..."
ln -sf /etc/letsencrypt/live/${{DOMAIN_NAME}}/fullchain.pem data/assets/ssl/cert.pem
ln -sf /etc/letsencrypt/live/${{DOMAIN_NAME}}/privkey.pem data/assets/ssl/key.pem

echo "Creating cron job for cert renewal..."

cat >/etc/cron.daily/mailcow-cert-renew <<'EOF'
#!/bin/bash
set -e
echo "Running certbot renew (manual DNS challenge - you must update TXT records)..."
certbot renew --manual-public-ip-logging-ok --preferred-challenges dns --manual
if [ $? -eq 0 ]; then
  echo "Renewal successful, restarting Mailcow nginx..."
  cd /opt/mailcow-dockerized || exit 1
  docker-compose restart nginx-mailcow
fi
EOF

chmod +x /etc/cron.daily/mailcow-cert-renew

echo "Mailcow setup and wildcard cert complete!"

echo ""
echo "IMPORTANT DNS Records to configure for {DOMAIN_NAME} (replace YOUR_IPV4 and YOUR_IPV6 accordingly):"
echo "A Record: {DOMAIN_NAME} -> Your Server IP"
echo "CNAME: autodiscover -> {DOMAIN_NAME}"
echo "CNAME: autoconfig -> {DOMAIN_NAME}"
echo "MX Record for {base_domain}: {DOMAIN_NAME} with highest priority"
echo "SRV Record: _autodiscover._tcp -> 0 5 443 {DOMAIN_NAME}"
echo "TXT Record (SPF): \\"v=spf1 ip4:YOUR_IPV4 ip6:YOUR_IPV6 -all\\""
echo "TXT Record (_DMARC): \\"v=DMARC1; p=quarantine; adkim=s; aspf=s\\""
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
