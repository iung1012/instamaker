#!/usr/bin/env bash
set -euo pipefail

project_dir="${1:?Informe o diretorio do projeto.}"
cd "$project_dir"

echo "=== VPS ==="
printf 'data='
date --iso-8601=seconds
printf 'hostname='
hostname
printf 'usuario='
whoami
printf 'uptime='
uptime -p
printf 'espaco_disponivel='
df -hP "$project_dir" | awk 'NR == 2 {print $4}'
printf 'memoria='
free -h | awk '/^Mem:/ {print $3 "/" $2}'

echo "=== Projeto ==="
for path in \
    .automation_state.json \
    .automation_vps.lock \
    avatar_video.mp4 \
    nastelbom-minimal-technology-345194.mp3 \
    venv/bin/python
do
    if [[ -e "$path" ]]; then
        printf '%s=presente tamanho=%s modificado=%s\n' \
            "$path" \
            "$(stat -c '%s' "$path")" \
            "$(stat -c '%y' "$path")"
    else
        printf '%s=ausente\n' "$path"
    fi
done

printf 'pipeline_em_execucao='
if pgrep -af '[a]utomation_pipeline.py' >/dev/null; then
    echo "sim"
else
    echo "nao"
fi

echo "=== Estado ==="
python_bin="python3"
if [[ -x venv/bin/python ]]; then
    python_bin="venv/bin/python"
fi
"$python_bin" - <<'PY'
import json
from pathlib import Path

path = Path(".automation_state.json")
if not path.is_file():
    print("arquivo=ausente")
    raise SystemExit

try:
    state = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"arquivo=invalido tipo_erro={type(exc).__name__}")
    raise SystemExit

print(f"last_run_utc={state.get('last_run_utc')}")
print(f"last_success_utc={state.get('last_success_utc')}")
print(f"processed_sources={len(state.get('processed_sources', []))}")
targets = state.get("published_outputs_by_target", {})
for name in sorted(targets):
    values = targets.get(name)
    print(f"publicados_{name}={len(values) if isinstance(values, list) else 0}")
PY

echo "=== Logs recentes ==="
if [[ ! -d logs ]]; then
    echo "diretorio=ausente"
    exit 0
fi

find logs -maxdepth 1 -type f -printf '%T@|%TY-%Tm-%TdT%TH:%TM:%TS%Tz|%s|%f\n' |
    sort -t '|' -k1,1nr |
    awk 'NR <= 10' |
    cut -d '|' -f2-

echo "=== Resumo dos logs ==="
while IFS= read -r log_file; do
    printf '%s: ' "$(basename "$log_file")"
    summary="$(
        grep -E \
            'Pipeline finalizada|Erro na pipeline|Dry-run finalizado|Pipeline ja esta em execucao|Publicado|Falha|Erro:' \
            "$log_file" |
            tail -n 3 |
            tr '\n' ';' || true
    )"
    if [[ -n "$summary" ]]; then
        printf '%s\n' "$summary"
    else
        echo "sem marcador de conclusao"
    fi
done < <(
    find logs -maxdepth 1 -type f -printf '%T@|%p\n' |
        sort -t '|' -k1,1nr |
        awk 'NR <= 5' |
        cut -d '|' -f2-
)
