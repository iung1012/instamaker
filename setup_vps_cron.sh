#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$PROJECT_DIR/run_automation.sh"
TIMES="09:00,12:00,15:00,18:00,21:00"
PUBLISH_KIND="both"
PUBLISH_COUNT="1"
PUBLISH_YOUTUBE_SHORTS="0"
SKIP_INSTAGRAM="0"
MARKER_START="# instagram_automation:start"
MARKER_END="# instagram_automation:end"

usage() {
    cat <<'EOF'
Uso: ./setup_vps_cron.sh [opcoes]

Opcoes:
  --times HH:MM,HH:MM,...       Horarios diarios. Padrao: 09:00,12:00,15:00,18:00,21:00
  --kind reel|story|both        Formato Instagram. Padrao: both
  --count N                     Midias por horario. Padrao: 1
  --youtube-shorts              Publica tambem como YouTube Shorts
  --skip-instagram              Publica apenas nos outros destinos habilitados

Padrao instalado:
  5 horarios por dia x 1 midia por horario x kind both = 5 reels + 5 stories por dia.
EOF
}

while (($#)); do
    case "$1" in
        --times)
            TIMES="$2"
            shift 2
            ;;
        --kind|--publish-kind)
            PUBLISH_KIND="$2"
            shift 2
            ;;
        --count|--publish-count)
            PUBLISH_COUNT="$2"
            shift 2
            ;;
        --youtube-shorts|--publish-youtube-shorts)
            PUBLISH_YOUTUBE_SHORTS="1"
            shift
            ;;
        --skip-instagram)
            SKIP_INSTAGRAM="1"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Opcao desconhecida: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! -f "$RUNNER" ]]; then
    echo "Runner nao encontrado: $RUNNER" >&2
    exit 1
fi

IFS=',' read -r -a TIME_LIST <<< "$TIMES"
if [[ "${#TIME_LIST[@]}" -eq 0 ]]; then
    echo "Informe pelo menos um horario em --times." >&2
    exit 1
fi

for time_value in "${TIME_LIST[@]}"; do
    if [[ ! "$time_value" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
        echo "Horario invalido: $time_value. Use HH:MM." >&2
        exit 1
    fi
done

existing_cron="$(crontab -l 2>/dev/null || true)"
filtered_cron="$(
    printf '%s\n' "$existing_cron" |
        awk -v start="$MARKER_START" -v end="$MARKER_END" '
            $0 == start {skip=1; next}
            $0 == end {skip=0; next}
            !skip {print}
        '
)"

cron_block="$MARKER_START"$'\n'
cron_block+="# Projeto: $PROJECT_DIR"$'\n'
cron_block+="SHELL=/bin/bash"$'\n'

extra_runner_flags=""
if [[ "$PUBLISH_YOUTUBE_SHORTS" == "1" ]]; then
    extra_runner_flags+=" --youtube-shorts"
fi
if [[ "$SKIP_INSTAGRAM" == "1" ]]; then
    extra_runner_flags+=" --skip-instagram"
fi

for time_value in "${TIME_LIST[@]}"; do
    hour="${time_value%%:*}"
    minute="${time_value##*:}"
    cron_block+="$minute $hour * * * cd \"$PROJECT_DIR\" && bash \"$RUNNER\" --kind \"$PUBLISH_KIND\" --count \"$PUBLISH_COUNT\"$extra_runner_flags >> \"$PROJECT_DIR/logs/cron.log\" 2>&1"$'\n'
done

cron_block+="$MARKER_END"

{
    if [[ -n "$filtered_cron" ]]; then
        printf '%s\n' "$filtered_cron"
    fi
    printf '%s\n' "$cron_block"
} | crontab -

echo "Cron instalado para: $TIMES"
if [[ "$SKIP_INSTAGRAM" == "1" ]]; then
    echo "Instagram desativado neste cron."
else
    echo "Resultado: $((${#TIME_LIST[@]} * PUBLISH_COUNT)) reels e $((${#TIME_LIST[@]} * PUBLISH_COUNT)) stories por dia quando --kind both."
fi
if [[ "$PUBLISH_YOUTUBE_SHORTS" == "1" ]]; then
    echo "YouTube Shorts tambem habilitado para cada midia publicada."
fi
