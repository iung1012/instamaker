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

exec bash "$PROJECT_DIR/run_automation.sh" \
    --kind both \
    --count 1 \
    -- \
    --strict-scrape
