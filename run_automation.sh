#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

export PATH="$PROJECT_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"

LOCAL_SYSTEM_ROOT="$PROJECT_DIR/.local-system/root"
if [[ -d "$LOCAL_SYSTEM_ROOT" ]]; then
    library_path="$(
        find "$LOCAL_SYSTEM_ROOT" -type f \
            \( -name '*.so' -o -name '*.so.*' \) \
            -printf '%h\n' |
            sort -u |
            paste -sd: -
    )"
    if [[ -n "$library_path" ]]; then
        export LD_LIBRARY_PATH="$library_path${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi

    font_path="$LOCAL_SYSTEM_ROOT/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if [[ -f "$font_path" ]]; then
        export AUTOMATION_FONT_PATH="$font_path"
    fi

    mkdir -p "$PROJECT_DIR/.local-system/fontconfig-cache"
    cat > "$PROJECT_DIR/.local-system/fonts.conf" <<EOF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>$LOCAL_SYSTEM_ROOT/usr/share/fonts</dir>
  <dir>/usr/share/fonts</dir>
  <cachedir>$PROJECT_DIR/.local-system/fontconfig-cache</cachedir>
  <cachedir>/tmp/fontconfig-cache</cachedir>
</fontconfig>
EOF
    export FONTCONFIG_FILE="$PROJECT_DIR/.local-system/fonts.conf"
    export FONTCONFIG_PATH="$PROJECT_DIR/.local-system"
fi

MODE="${MODE:-reels}"
PUBLISH_KIND="${PUBLISH_KIND:-both}"
PUBLISH_COUNT="${PUBLISH_COUNT:-1}"
PUBLISH_YOUTUBE_SHORTS="${PUBLISH_YOUTUBE_SHORTS:-0}"
SKIP_INSTAGRAM="${SKIP_INSTAGRAM:-0}"
DRY_RUN=0
EXTRA_ARGS=()

usage() {
    cat <<'EOF'
Uso: ./run_automation.sh [opcoes] [-- argumentos extras da pipeline]

Opcoes:
  --mode reels|carousel          Modo da pipeline. Padrao: reels
  --kind reel|story|both         Formato Instagram. Padrao: both
  --count N                     Quantidade de midias por execucao. Padrao: 1
  --youtube-shorts              Publica tambem como YouTube Shorts
  --skip-instagram              Publica apenas nos outros destinos habilitados
  --dry-run                     Mostra comandos sem publicar

Exemplo para 5 reels + 5 stories em uma execucao:
  ./run_automation.sh --count 5 --kind both

Exemplo para publicar Instagram + YouTube Shorts:
  ./run_automation.sh --youtube-shorts
EOF
}

while (($#)); do
    case "$1" in
        --mode)
            MODE="$2"
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
            PUBLISH_YOUTUBE_SHORTS=1
            shift
            ;;
        --skip-instagram)
            SKIP_INSTAGRAM=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            EXTRA_ARGS+=("$@")
            break
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

mkdir -p "$PROJECT_DIR/logs"

LOCK_FILE="$PROJECT_DIR/.automation_vps.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Pipeline ja esta em execucao. Ignorando esta execucao."
    exit 0
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
    PYTHON="$PYTHON_BIN"
elif [[ -x "$PROJECT_DIR/venv/bin/python" ]]; then
    PYTHON="$PROJECT_DIR/venv/bin/python"
else
    PYTHON="python3"
fi

timestamp="$(date +"%Y-%m-%d_%H-%M-%S")"
LOG_FILE="$PROJECT_DIR/logs/automation_$timestamp.log"

RUNNER_ARGS=(
    "automation_pipeline.py"
    "--mode" "$MODE"
    "--publish-kind" "$PUBLISH_KIND"
    "--publish-count" "$PUBLISH_COUNT"
    "--max-compose" "$PUBLISH_COUNT"
)

if [[ "$DRY_RUN" == "1" ]]; then
    RUNNER_ARGS+=("--dry-run")
fi
if [[ "$PUBLISH_YOUTUBE_SHORTS" == "1" || "$PUBLISH_YOUTUBE_SHORTS" == "true" ]]; then
    RUNNER_ARGS+=("--publish-youtube-shorts")
fi
if [[ "$SKIP_INSTAGRAM" == "1" || "$SKIP_INSTAGRAM" == "true" ]]; then
    RUNNER_ARGS+=("--skip-instagram")
fi

set +e
"$PYTHON" "${RUNNER_ARGS[@]}" "${EXTRA_ARGS[@]}" 2>&1 | tee "$LOG_FILE"
status=${PIPESTATUS[0]}
set -e

exit "$status"
