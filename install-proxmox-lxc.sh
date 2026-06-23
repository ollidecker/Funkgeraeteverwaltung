#!/usr/bin/env bash
set -e

APP_INSTALL_URL="https://raw.githubusercontent.com/ollidecker/Funkgeraeteverwaltung/main/install-app.sh"

echo "======================================="
echo " Funkgeraeteverwaltung LXC Installer"
echo "======================================="
echo ""

read -p "LXC ID [14943]: " CTID
CTID=${CTID:-14943}

read -p "Hostname [funkgeraeteverwaltung]: " HOSTNAME
HOSTNAME=${HOSTNAME:-funkgeraeteverwaltung}

read -p "CPU Kerne [2]: " CORES
CORES=${CORES:-2}

read -p "RAM in MB [2048]: " MEMORY
MEMORY=${MEMORY:-2048}

read -p "Speicher in GB [10]: " DISK
DISK=${DISK:-10}

read -p "Storage [local-lvm]: " STORAGE
STORAGE=${STORAGE:-local-lvm}

read -p "Bridge [vmbr0]: " BRIDGE
BRIDGE=${BRIDGE:-vmbr0}

echo ""
echo "Netzwerk:"
echo "1) DHCP"
echo "2) Statische IP"
read -p "Auswahl [1]: " NET_CHOICE
NET_CHOICE=${NET_CHOICE:-1}

if [ "$NET_CHOICE" = "2" ]; then
  read -p "IP Adresse mit CIDR, z.B. 192.168.1.80/24: " STATIC_IP
  read -p "Gateway, z.B. 192.168.1.1: " GATEWAY
  read -p "DNS [1.1.1.1]: " DNS
  DNS=${DNS:-1.1.1.1}
  NET_CONF="name=eth0,bridge=${BRIDGE},ip=${STATIC_IP},gw=${GATEWAY}"
else
  DNS="1.1.1.1"
  NET_CONF="name=eth0,bridge=${BRIDGE},ip=dhcp"
fi

read -p "App Port [14943]: " APP_PORT
APP_PORT=${APP_PORT:-14943}

echo ""
echo "Zusammenfassung:"
echo "CT ID: $CTID"
echo "Hostname: $HOSTNAME"
echo "CPU: $CORES"
echo "RAM: ${MEMORY} MB"
echo "Disk: ${DISK} GB"
echo "Storage: $STORAGE"
echo "Netzwerk: $NET_CONF"
echo "DNS: $DNS"
echo "Port: $APP_PORT"
echo ""

read -p "Container so erstellen? [j/N]: " CONFIRM
if [[ ! "$CONFIRM" =~ ^[JjYy]$ ]]; then
  echo "Abgebrochen."
  exit 1
fi

if pct status "$CTID" >/dev/null 2>&1; then
  echo "FEHLER: CT ID $CTID existiert bereits."
  exit 1
fi

echo "Suche Debian 12 Template..."
pveam update

TEMPLATE=$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -n 1)

if [ -z "$TEMPLATE" ]; then
  echo "FEHLER: Kein Debian 12 Template gefunden."
  exit 1
fi

if [ ! -f "/var/lib/vz/template/cache/${TEMPLATE}" ]; then
  echo "Lade Template: $TEMPLATE"
  pveam download local "$TEMPLATE"
fi

echo "Erstelle LXC Container..."

pct create "$CTID" "local:vztmpl/${TEMPLATE}" \
  --hostname "$HOSTNAME" \
  --cores "$CORES" \
  --memory "$MEMORY" \
  --rootfs "${STORAGE}:${DISK}" \
  --net0 "$NET_CONF" \
  --nameserver "$DNS" \
  --ostype debian \
  --unprivileged 1 \
  --features nesting=1 \
  --start 1

echo "Warte auf Netzwerk..."
sleep 15

echo "Installiere Voraussetzungen im Container..."
pct exec "$CTID" -- apt update
pct exec "$CTID" -- apt install -y curl

echo "Installiere App im Container..."
pct exec "$CTID" -- bash -c "curl -s ${APP_INSTALL_URL} | bash -s -- ${APP_PORT}"

echo "Pruefe Dienst..."
sleep 5
pct exec "$CTID" -- systemctl is-active funkgeraeteverwaltung

IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')

echo ""
echo "======================================="
echo " Fertig"
echo "======================================="
echo ""
echo "Container ID: $CTID"
echo "Hostname: $HOSTNAME"
echo "Aufruf:"
echo "http://${IP}:${APP_PORT}"
