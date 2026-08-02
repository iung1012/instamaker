#!/bin/bash
# publish.sh — publica um carrossel gerado pelo carousel-gen no Instagram.
#
# A publicacao usa o instagrapi_publisher.py do /opt/instagram-bot, que ja
# tem a sessao de @devhackeria salva. Aquele diretorio e root-only, entao a
# chamada passa por sudo.
#
# Por padrao NAO publica: valida e mostra o que seria postado. Para publicar
# de verdade e preciso passar --publish explicitamente.
#
#   ./publish.sh                      # valida o carrossel de hoje
#   ./publish.sh 2026-07-30           # valida uma data especifica
#   ./publish.sh 2026-07-30 --publish # publica

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_DIR=/opt/instagram-bot
PUBLISHER="$BOT_DIR/instagrapi_publisher.py"
PYTHON="$BOT_DIR/venv/bin/python"

DO_PUBLISH=0
DATE=""
for arg in "$@"; do
  case "$arg" in
    --publish) DO_PUBLISH=1 ;;
    --*) echo "Opcao desconhecida: $arg" >&2; exit 2 ;;
    *) DATE="$arg" ;;
  esac
done

if [ -z "$DATE" ]; then
  DATE=$(TZ="${TIMEZONE:-America/Sao_Paulo}" date +%F)
fi

DIR="$ROOT/out/$DATE"
[ -d "$DIR" ] || { echo "Pasta nao existe: $DIR"; exit 1; }

# --- Validacao antes de qualquer chamada de rede -------------------------
mapfile -t IMAGES < <(find "$DIR" -maxdepth 1 -name '0[1-9].png' | sort)
COUNT=${#IMAGES[@]}

if [ "$COUNT" -lt 2 ] || [ "$COUNT" -gt 10 ]; then
  echo "Album aceita de 2 a 10 imagens, encontrei $COUNT em $DIR"
  exit 1
fi

BAD=0
for img in "${IMAGES[@]}"; do
  DIMS=$(identify -format '%wx%h' "$img" 2>/dev/null || python3 -c "
import struct,sys
with open(sys.argv[1],'rb') as f:
    f.read(16); w,h = struct.unpack('>II', f.read(8))
print(f'{w}x{h}')" "$img")
  if [ "$DIMS" != "1080x1350" ]; then
    echo "  $(basename "$img"): $DIMS  <-- esperado 1080x1350"
    BAD=1
  fi
done
[ "$BAD" -eq 0 ] || { echo "Abortado: ha imagem fora do formato 4:5."; exit 1; }

CAPTION_FILE="$DIR/legenda.txt"
[ -f "$CAPTION_FILE" ] || { echo "legenda.txt nao encontrada em $DIR"; exit 1; }
CAPTION=$(cat "$CAPTION_FILE")
[ -n "$CAPTION" ] || { echo "legenda.txt esta vazia"; exit 1; }

echo "Pasta:    $DIR"
echo "Imagens:  $COUNT (todas 1080x1350)"
echo "Conta:    $(sudo -n bash -c "cd $BOT_DIR && $PYTHON instagrapi_publisher.py --whoami" 2>/dev/null | tail -1)"
echo "---------- legenda ----------"
echo "$CAPTION"
echo "-----------------------------"

if [ "$DO_PUBLISH" -ne 1 ]; then
  echo
  echo "Modo validacao. Nada foi publicado."
  echo "Para publicar de verdade:  ./publish.sh $DATE --publish"
  exit 0
fi

echo
echo "Publicando em @devhackeria..."

# A legenda vai por variavel de ambiente para nao passar por interpolacao
# de aspas dentro do sudo. As imagens entram como argumentos posicionais.
sudo -n CAPTION="$CAPTION" BOT_DIR="$BOT_DIR" PY="$PYTHON" bash -c '
  cd "$BOT_DIR" || exit 1
  exec "$PY" instagrapi_publisher.py --publish-album "$@" --caption "$CAPTION"
' _ "${IMAGES[@]}"
