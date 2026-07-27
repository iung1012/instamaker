#!/usr/bin/env bash
set -euo pipefail

project_dir="${1:?Informe o diretorio do projeto.}"
publish_count="${2:-1}"
manual_dry_run="${3:-false}"
event_name="${4:-schedule}"
publish_youtube_shorts="${5:-false}"
skip_instagram="${6:-false}"
runtime_dir="$project_dir/.github-runtime"

cleanup() {
    rm -rf "$runtime_dir"
}
trap cleanup EXIT

cd "$project_dir"
test -s avatar_video.mp4
test -s nastelbom-minimal-technology-345194.mp3
test -s "$runtime_dir/IG_USER_ID"
test -s "$runtime_dir/IG_ACCESS_TOKEN"
test -s "$runtime_dir/cookies.json"

export IG_USER_ID
IG_USER_ID="$(<"$runtime_dir/IG_USER_ID")"
export IG_ACCESS_TOKEN
IG_ACCESS_TOKEN="$(<"$runtime_dir/IG_ACCESS_TOKEN")"
export TWITTER_COOKIES_FILE="$runtime_dir/cookies.json"

command -v python3 >/dev/null ||
    { echo "python3 nao esta instalado na VPS." >&2; exit 1; }

if [[ ! -x venv/bin/python ]] ||
    ! venv/bin/python -m pip --version >/dev/null 2>&1
then
    rm -rf venv
    if ! python3 -m venv venv; then
        echo "python3-venv indisponivel; usando bootstrap do virtualenv."
        rm -rf venv
        virtualenv_zipapp="$(mktemp --suffix=.pyz)"
        VIRTUALENV_ZIPAPP="$virtualenv_zipapp" python3 - <<'PY'
import os
from urllib.request import urlopen

url = "https://bootstrap.pypa.io/virtualenv.pyz"
with urlopen(url, timeout=120) as response:
    content = response.read()
if not content:
    raise SystemExit("Download vazio de virtualenv.pyz")
with open(os.environ["VIRTUALENV_ZIPAPP"], "wb") as output:
    output.write(content)
PY
        python3 "$virtualenv_zipapp" venv
        rm -f "$virtualenv_zipapp"
    fi
fi

run_as_root_if_available() {
    if [[ "$EUID" -eq 0 ]]; then
        "$@"
        return 0
    fi

    if command -v sudo >/dev/null && sudo -n true 2>/dev/null; then
        sudo -n "$@"
        return 0
    fi
    return 1
}

local_system_root="$project_dir/.local-system"

activate_local_system() {
    if [[ ! -d "$local_system_root/root" ]]; then
        return
    fi

    local library_path
    library_path="$(
        find "$local_system_root/root" -type f \
            \( -name '*.so' -o -name '*.so.*' \) \
            -printf '%h\n' |
            sort -u |
            paste -sd: -
    )"
    if [[ -n "$library_path" ]]; then
        export LD_LIBRARY_PATH="$library_path${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi

    local font_path
    font_path="$local_system_root/root/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if [[ -f "$font_path" ]]; then
        export AUTOMATION_FONT_PATH="$font_path"
    fi

    mkdir -p "$project_dir/.local-system/fontconfig-cache"
    cat > "$project_dir/.local-system/fonts.conf" <<EOF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>$local_system_root/root/usr/share/fonts</dir>
  <dir>/usr/share/fonts</dir>
  <cachedir>$project_dir/.local-system/fontconfig-cache</cachedir>
  <cachedir>/tmp/fontconfig-cache</cachedir>
</fontconfig>
EOF
    export FONTCONFIG_FILE="$project_dir/.local-system/fonts.conf"
    export FONTCONFIG_PATH="$project_dir/.local-system"
}

install_local_browser_dependencies() {
    command -v apt-get >/dev/null ||
        { echo "apt-get nao esta disponivel para baixar bibliotecas locais." >&2; return 1; }
    command -v dpkg-deb >/dev/null ||
        { echo "dpkg-deb nao esta disponivel para extrair bibliotecas locais." >&2; return 1; }

    local packages=(
        fonts-dejavu-core
        libasound2t64
        libatk-bridge2.0-0t64
        libatk1.0-0t64
        libatspi2.0-0t64
        libcairo2
        libcups2t64
        libdbus-1-3
        libdrm2
        libgbm1
        libglib2.0-0t64
        libnspr4
        libnss3
        libpango-1.0-0
        libx11-6
        libxau6
        libxcb1
        libxcomposite1
        libxdamage1
        libxdmcp6
        libxext6
        libxfixes3
        libxi6
        libxkbcommon0
        libxrandr2
        libxrender1
    )
    local package_signature
    package_signature="$(printf '%s\n' "${packages[@]}" | sha256sum | awk '{print $1}')"

    if [[ -f "$local_system_root/.package-signature" ]] &&
        [[ "$(<"$local_system_root/.package-signature")" == "$package_signature" ]]
    then
        activate_local_system
        return
    fi

    rm -rf "$local_system_root/debs" "$local_system_root/root"
    mkdir -p "$local_system_root/debs" "$local_system_root/root"
    (
        cd "$local_system_root/debs"
        apt-get download "${packages[@]}"
    )

    local package_file
    for package_file in "$local_system_root"/debs/*.deb; do
        dpkg-deb -x "$package_file" "$local_system_root/root"
    done
    printf '%s\n' "$package_signature" > "$local_system_root/.package-signature"
    activate_local_system
}

requirements_hash="$(sha256sum requirements.txt | awk '{print $1}')"
installed_hash="$(cat venv/.requirements-hash 2>/dev/null || true)"
if [[ "$requirements_hash" != "$installed_hash" ]]; then
    venv/bin/python -m pip install --upgrade pip
    venv/bin/python -m pip install -r requirements.txt
    venv/bin/python -m playwright install chromium
    printf '%s\n' "$requirements_hash" > venv/.requirements-hash
fi

venv/bin/static_ffmpeg -version >/dev/null
venv/bin/static_ffprobe -version >/dev/null
ln -sfn static_ffmpeg venv/bin/ffmpeg
ln -sfn static_ffprobe venv/bin/ffprobe
export PATH="$project_dir/venv/bin:$PATH"
activate_local_system

validate_chromium() {
    venv/bin/python - <<'PY'
import asyncio
from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        await browser.close()


asyncio.run(main())
PY
}

if ! validate_chromium; then
    echo "Chromium precisa de bibliotecas adicionais do sistema."
    if install_local_browser_dependencies && validate_chromium; then
        echo "Chromium validado com bibliotecas locais."
    else
        if ! run_as_root_if_available \
            "$project_dir/venv/bin/python" -m playwright install-deps chromium
        then
            echo "Execute uma vez na VPS como root:" >&2
            echo "$project_dir/venv/bin/python -m playwright install-deps chromium" >&2
            exit 1
        fi
        validate_chromium
    fi
fi

is_truthy() {
    case "${1,,}" in
        1|true|yes|y|on|sim) return 0 ;;
        *) return 1 ;;
    esac
}

args=(--kind both --count "$publish_count")
if is_truthy "$publish_youtube_shorts"; then
    args+=(--youtube-shorts)
fi
if is_truthy "$skip_instagram"; then
    args+=(--skip-instagram)
fi
args+=(-- --strict-scrape)
if [[ "$event_name" == "workflow_dispatch" && "$manual_dry_run" == "true" ]]; then
    args=(--dry-run "${args[@]}")
fi

exec bash "$project_dir/run_automation.sh" "${args[@]}"
