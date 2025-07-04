def generate_setup(
    DOMAIN_NAME,
    ADMIN_EMAIL,
    MAILCOW_ADMIN_PASSWORD
):
    base_domain = DOMAIN_NAME.split('.', 1)[-1] if DOMAIN_NAME.count('.') > 1 else DOMAIN_NAME
    mail_domain = DOMAIN_NAME

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
DEBIAN_FRONTEND=noninteractive apt-get install -y curl docker.io git netcat-openbsd ufw

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

echo "Docker starting"
docker-compose up -d

echo "Mailcow setup completed successfully!"

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
