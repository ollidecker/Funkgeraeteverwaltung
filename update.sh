#!/usr/bin/env bash
set -e

APP_DIR="/opt/funkgeraeteverwaltung"
SERVICE_NAME="funkgeraeteverwaltung"

echo "Update Funkgeraeteverwaltung..."

cd "$APP_DIR"

echo "Stoppe Dienst..."
systemctl stop "$SERVICE_NAME"

echo "Hole aktuelle Version von GitHub..."
git pull

echo "Aktualisiere Python-Abhängigkeiten..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Erstelle Laufzeitordner..."
mkdir -p data logs pdfs project_images

echo "Starte Dienst..."
systemctl daemon-reload
systemctl start "$SERVICE_NAME"

echo ""
echo "Update fertig."
echo "Aktuelle Version:"
cat version.txt || true
