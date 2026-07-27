#!/usr/bin/env bash
# Autoriza um id do Telegram a usar o bot. Uso: ./autorizar.sh 123456789
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
id="${1:?Uso: ./autorizar.sh <seu_id_do_telegram>}"
[[ "$id" =~ ^-?[0-9]+$ ]] || { echo "Id invalido: $id" >&2; exit 1; }

atual="$(grep -E "^TELEGRAM_ALLOWED_USER_IDS=" .env | cut -d= -f2- || true)"
if [[ ",$atual," == *",$id,"* ]]; then
    echo "Id $id ja estava autorizado."
else
    novo="${atual:+$atual,}$id"
    sed -i "s|^TELEGRAM_ALLOWED_USER_IDS=.*|TELEGRAM_ALLOWED_USER_IDS=$novo|" .env
    echo "Autorizado: $novo"
fi
systemctl restart instagram-bot
sleep 4
systemctl is-active --quiet instagram-bot && journalctl -u instagram-bot -n 3 --no-pager -o cat
