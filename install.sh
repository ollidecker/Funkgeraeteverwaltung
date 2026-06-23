#!/usr/bin/env bash
set -e

APP_DIR="/opt/funkgeraeteverwaltung"
REPO_URL="https://github.com/ollidecker/Funkgeraeteverwaltung.git"
SERVICE_NAME="funkgeraeteverwaltung"

echo "Installiere Funkgeraeteverwaltung..."

apt update
apt install -y git python3 python3-venv python3-pip

rm -rf "$APP_DIR"
git clone "$REPO_URL" "$APP_DIR"

cd "$APP_DIR"

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

mkdir -p data logs pdfs project_images

cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Funkgeraeteverwaltung
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/app.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl restart ${SERVICE_NAME}

IP=$(hostname -I | awk '{print $1}')

echo ""
echo "Fertig."
echo "Aufruf:"
echo "http://${IP}:14943"
