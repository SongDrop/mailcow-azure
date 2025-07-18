def generate_setup(DOMAIN_NAME, ADMIN_EMAIL, MAILCOW_ADMIN_PASSWORD, PORT=80):
    import re

    def get_base_domain(domain):
        domain = domain.strip('.')
        parts = domain.split('.')
        if len(parts) < 2:
            raise ValueError(f"'{domain}' is not a valid FQDN to derive base domain")
        return '.'.join(parts[-2:])

    fqdn_pattern = re.compile(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    if not fqdn_pattern.match(DOMAIN_NAME):
        raise ValueError(f"{DOMAIN_NAME} is not a valid FQDN (e.g., smtp.example.com)")

    base_domain = get_base_domain(DOMAIN_NAME)

    gpt_repo = "https://github.com/mailcow/mailcow-dockerized.git"
    letsencrypt_options_url = "https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf"
    ssl_dhparams_url = "https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem"
    INSTALL_DIR = "/opt/mailcow-dockerized"
    docker_compose_url = "https://github.com/docker/compose/releases/download/v2.24.5/docker-compose-linux-x86_64"
  
    script_template = f"""#!/bin/bash
set -euo pipefail

MAILCOW_DIR="{INSTALL_DIR}"

cleanup() {{
    echo "[CLEANUP] Removing Mailcow containers and volumes..."
    cd "$MAILCOW_DIR" || exit 1
    docker-compose down -v --remove-orphans || true
    docker system prune -f --volumes -f || true
}}

trap 'echo "[ERROR] An error occurred. Running cleanup..."; cleanup' ERR

# ========== [1/10] ENV VARIABLES ==========
DOMAIN_NAME="{DOMAIN_NAME}"
ADMIN_EMAIL="{ADMIN_EMAIL}"
MAILCOW_ADMIN_PASSWORD="{MAILCOW_ADMIN_PASSWORD}"
MAILCOW_GIT="{gpt_repo}"
PORT="{PORT}"

echo "Starting Mailcow setup for DOMAIN_NAME=$DOMAIN_NAME (base domain: {base_domain})"

# ========== [2/10] CHECK AND FREE PORTS ==========
echo "[2/10] Checking ports 80 and 443 availability..."

for port in 80 443; do
    if ss -tln | grep -q ":$port\$"; then
        echo "Port $port is in use. Attempting to stop conflicting services..."
        systemctl stop nginx || true
        systemctl stop apache2 || true
        fuser -k $port/tcp || true
        sleep 3
        if ss -tln | grep -q ":$port\$"; then
            echo "ERROR: Port $port still in use after cleanup attempts. Please free it manually."
            exit 1
        else
            echo "Port $port freed."
        fi
    else
        echo "Port $port is free."
    fi
done

# ========== [3/10] INSTALL DEPENDENCIES ==========
echo "[3/10] Installing dependencies..."

export DEBIAN_FRONTEND=noninteractive

# Remove docker.io package if installed to avoid conflicts with Docker CE
if dpkg -l | grep -q docker.io; then
    apt-get remove -y docker.io
fi

apt-get update -y
apt-get install -y \\
    curl \\
    git \\
    netcat-openbsd \\
    ufw \\
    software-properties-common \\
    apt-transport-https \\
    ca-certificates \\
    gnupg \\
    lsb-release \\
    certbot

# ========== [4/10] INSTALL DOCKER CE ==========
echo "[4/10] Installing Docker CE..."

if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io
else
    echo "Docker is already installed."
fi

# ========== [5/10] INSTALL DOCKER-COMPOSE ==========
echo "[5/10] Installing docker-compose..."

curl -fsSL {docker_compose_url} -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose
ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose

# ========== [6/10] ENABLE AND START DOCKER ==========
echo "[6/10] Enabling and starting Docker..."

systemctl enable docker
systemctl start docker

# ========== [7/10] CLONE MAILCOW ==========
echo "[7/10] Cloning Mailcow repository to $MAILCOW_DIR ..."

mkdir -p "$MAILCOW_DIR"
cd "$MAILCOW_DIR"

if [ -d ".git" ]; then
    echo "Repository exists, pulling latest changes..."
    git pull
else
    git clone "$MAILCOW_GIT" .
fi

# ========== [8/10] GENERATE CONFIG ==========
echo "[8/10] Generating Mailcow configuration..."
printf '%s\nEtc/UTC\n1\n' "$DOMAIN_NAME" | ./generate_config.sh

# ========== [9/10] CONFIGURE FIREWALL ==========
echo "[9/10] Configuring firewall rules..."

ufw allow 22/tcp    # SSH
ufw allow 25/tcp    # SMTP
ufw allow 465/tcp   # SMTPS
ufw allow 587/tcp   # Submission
ufw allow 110/tcp   # POP3
ufw allow 995/tcp   # POP3S
ufw allow 143/tcp   # IMAP
ufw allow 993/tcp   # IMAPS
ufw allow 4190/tcp  # ManageSieve
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS

# Allow Docker bridge network traffic (adjust if different)
ufw allow in on docker0
ufw allow out on docker0

ufw allow out 25/tcp
ufw allow out 53
ufw allow out 443/tcp
ufw allow out 587/tcp

if ufw status | grep -qw inactive; then
    echo "Enabling UFW firewall..."
    ufw --force enable
fi

ufw reload || true

# ========== [10/10] START MAILCOW ==========
echo "[10/10] Pulling and starting Mailcow containers..."

docker-compose pull
docker-compose up -d

sleep 15


echo ""
echo "Mailcow setup and SSL certificate are complete!"
echo ""
echo "IMPORTANT DNS Records to configure for {DOMAIN_NAME} (replace YOUR_IPV4 and YOUR_IPV6 accordingly):"
echo "A Record: {DOMAIN_NAME} -> Your Server IP"
echo "CNAME: autodiscover -> {DOMAIN_NAME}"
echo "CNAME: autoconfig -> {DOMAIN_NAME}"
echo "MX Record for {base_domain}: {DOMAIN_NAME} with highest priority"
echo "SRV Record: _autodiscover._tcp -> 0 5 443 {DOMAIN_NAME}"
echo 'TXT Record (SPF): "v=spf1 ip4:YOUR_IPV4 ip6:YOUR_IPV6 -all"'
echo 'TXT Record (_DMARC): "v=DMARC1; p=quarantine; adkim=s; aspf=s"'
echo ""
echo "After Mailcow startup, add these DNS records from Mailcow UI:"
echo "- TLSA for _25._tcp.{DOMAIN_NAME}"
echo "- TXT for dkim._domainkey.{base_domain}"
echo ""
echo "Mailcow is now accessible at: https://{DOMAIN_NAME}/"
echo "Admin email: {ADMIN_EMAIL}"
echo "Admin password: {MAILCOW_ADMIN_PASSWORD}"
"""

    return script_template
