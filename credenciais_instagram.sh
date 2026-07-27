#!/usr/bin/env bash
# Grava IG_USER_ID e IG_ACCESS_TOKEN no .env sem deixar rastro no historico.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
read -rp "IG_USER_ID: " ig_user
read -rsp "IG_ACCESS_TOKEN (nao aparece na tela): " ig_token; echo
[[ -n "$ig_user" && -n "$ig_token" ]] || { echo "Ambos sao obrigatorios." >&2; exit 1; }

for par in "IG_USER_ID=$ig_user" "IG_ACCESS_TOKEN=$ig_token"; do
    chave="${par%%=*}"
    if grep -qE "^$chave=" .env; then
        sed -i "s|^$chave=.*|$par|" .env
    else
        printf "%s\n" "$par" >> .env
    fi
done
chmod 600 .env
echo "Gravado. Validando na Graph API..."
./venv/bin/python - <<'PY'
import os
from instagram_graph_publisher import load_dotenv, graph_api_get
load_dotenv()
try:
    r = graph_api_get(os.environ["IG_USER_ID"], {"fields": "username,followers_count",
                                                 "access_token": os.environ["IG_ACCESS_TOKEN"]})
    print(f"  OK: @{r.get('username')} ({r.get('followers_count')} seguidores)")
except Exception as e:
    print(f"  FALHOU: {e}")
PY
systemctl restart instagram-bot
