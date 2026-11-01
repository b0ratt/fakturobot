#!/usr/bin/env bash
# Instalacja albo aktualizacja fakturobota (Ubuntu 24.04). Uruchom jako root w katalogu z rozpakowanym kodem:
#   sudo bash deploy/install.sh
# Nie nadpisuje istniejących /etc/fakturobot/.env i config.yaml ani bazy w /var/lib/fakturobot.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/fakturobot
ETC=/etc/fakturobot

[[ $EUID -eq 0 ]] || { echo "Uruchom przez sudo" >&2; exit 1; }

if ! python3 -c 'import ensurepip' 2>/dev/null; then
    echo "Brak python3-venv. Zainstaluj: apt install python3.12-venv" >&2
    exit 1
fi

if [[ ! -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf ]]; then
    echo "Brak czcionki DejaVu do PDF-ów. Zainstaluj: apt install fonts-dejavu-core" >&2
    exit 1
fi

id fakturobot &>/dev/null || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin fakturobot

install -d -o root -g root -m 755 "$APP"
install -d -o root -g fakturobot -m 750 "$ETC"

# Kod: nowa wersja obok, potem podmiana (poprzednia zostaje w app.prev)
rm -rf "$APP/app.new"
install -d -m 755 "$APP/app.new"
cp -r "$SRC/fakturobot" "$SRC/requirements.txt" "$APP/app.new/"

[[ -d "$APP/venv" ]] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --disable-pip-version-check --require-hashes --only-binary=:all: \
    -r "$APP/app.new/requirements.txt"
"$APP/venv/bin/python" -m compileall -q "$APP/app.new/fakturobot"

rm -rf "$APP/app.prev"
[[ -d "$APP/app" ]] && mv "$APP/app" "$APP/app.prev"
mv "$APP/app.new" "$APP/app"
chown -R root:root "$APP"
chmod -R a+rX,go-w "$APP"

# Konfiguracja: tylko jeśli jej jeszcze nie ma
if [[ ! -f "$ETC/.env" ]]; then
    install -o root -g root -m 600 "$SRC/.env.example" "$ETC/.env"
    echo "Utworzono $ETC/.env z szablonu. Uzupełnij: sudoedit $ETC/.env"
fi
if [[ ! -f "$ETC/config.yaml" ]]; then
    install -o root -g fakturobot -m 640 "$SRC/config.example.yaml" "$ETC/config.yaml"
    echo "Utworzono $ETC/config.yaml z PRZYKŁADU. Uzupełnij: sudoedit $ETC/config.yaml"
fi
chown root:root "$ETC/.env" && chmod 600 "$ETC/.env"
chown root:fakturobot "$ETC/config.yaml" && chmod 640 "$ETC/config.yaml"

install -o root -g root -m 644 "$SRC/deploy/fakturobot.service" /etc/systemd/system/fakturobot.service
systemctl daemon-reload
systemctl enable fakturobot.service >/dev/null

if grep -qE '^TELEGRAM_BOT_TOKEN=.+' "$ETC/.env"; then
    systemctl restart fakturobot.service
    echo "Zrestartowano fakturobot. Logi: journalctl -u fakturobot -f"
else
    echo "Usługa włączona, ale nie uruchomiona: najpierw uzupełnij $ETC/.env, potem: sudo systemctl restart fakturobot"
fi
