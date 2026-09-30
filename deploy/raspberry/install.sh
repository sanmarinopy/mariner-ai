#!/usr/bin/env bash
# Instalación de Mariner en Raspberry Pi OS Lite 64-bit (Bookworm/Trixie).
# BORRADOR: todavía no probado en hardware real. Uso: bash deploy/raspberry/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
APP_USER="$(id -un)"
APP_UID="$(id -u)"

echo "==> Paquetes del sistema"
sudo apt-get update
if apt-cache show chromium-browser >/dev/null 2>&1; then CHROMIUM=chromium-browser; else CHROMIUM=chromium; fi
sudo apt-get install -y python3-venv python3-dev libportaudio2 cage "$CHROMIUM" fonts-dejavu-core git

echo "==> Entorno Python"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
[ -f "$APP_DIR/.env" ] || cp "$APP_DIR/.env.example" "$APP_DIR/.env"

echo "==> Servicios systemd"
CHROMIUM_BIN="$(command -v "$CHROMIUM")"
for svc in mariner mariner-kiosk; do
  sed -e "s#__USER__#$APP_USER#g" -e "s#__UID__#$APP_UID#g" -e "s#__DIR__#$APP_DIR#g" \
      -e "s#__CHROMIUM__#$CHROMIUM_BIN#g" \
      "$APP_DIR/deploy/raspberry/$svc.service" | sudo tee "/etc/systemd/system/$svc.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl disable getty@tty1.service || true
sudo systemctl enable mariner.service mariner-kiosk.service

cat <<EOF

Listo. Pasos siguientes:
  1) Edita $APP_DIR/.env  (OPENAI_API_KEY, GAME_SOURCE=bridge, HOST=0.0.0.0)
  2) sudo reboot
  3) En la PC gamer:  python -m mariner.bridge --target ws://$(hostname -I | awk '{print $1}'):8765/bridge
Logs:  journalctl -u mariner -f
EOF
