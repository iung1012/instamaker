"""Revisao visual do Reel montado, antes de publicar.

Dois defeitos passavam batido no render e so apareciam na conta:

1. Cabeca do personagem cortada. O painel do avatar usa
   `force_original_aspect_ratio=increase` + crop central
   (compose_test_video.build_avatar_panel), entao um personagem enquadrado
   com a cabeca no alto perde o topo do rosto.
2. Texto cortado. O hook encolhe de corpo antes de virar reticencias, mas
   legenda e CTA no painel do avatar podem passar do SAFE_BOTTOM do Reels ou
   esbarrar no personagem.

Nenhum dos dois da erro no ffmpeg: o video sai "com sucesso", errado. Aqui
tiramos alguns frames e perguntamos ao Gemini o que ele ve.

Uso:
    python video_qa.py --video outputs_ig/x_final.mp4
    python video_qa.py --video x.mp4 --hook "o gancho" --caption "a legenda"
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_MODEL = "gemini-flash-latest"
FRAME_COUNT = 4
# O Reels esconde o que passa dessas linhas; o modelo precisa saber onde elas
# estao para julgar "cortado" do jeito certo. Espelha compose_test_video.
SAFE_TOP = 230
SAFE_BOTTOM = 1580


def current_model() -> str:
    return os.getenv("GEMINI_MODEL") or DEFAULT_MODEL


def ffmpeg_bin(name: str = "ffmpeg") -> str:
    import shutil

    found = shutil.which(name)
    if found:
        return found
    candidate = Path(sys.executable).parent / name
    return str(candidate)


def video_duration(video: Path) -> float:
    result = subprocess.run(
        [ffmpeg_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def extract_frames(video: Path, dest_dir: Path, count: int = FRAME_COUNT) -> list[Path]:
    """Frames espalhados pelo video, evitando o primeiro e o ultimo instante.

    O gancho aparece no inicio e o CTA no fim; pegar so o meio esconderia
    justamente os dois textos que queremos conferir.
    """
    duration = video_duration(video)
    if duration <= 0:
        raise RuntimeError(f"Nao consegui ler a duracao de {video}")

    frames: list[Path] = []
    for index in range(count):
        # 10%, 36%, 63%, 90% para count=4
        position = duration * (0.10 + 0.80 * index / max(1, count - 1))
        dest = dest_dir / f"frame_{index}.jpg"
        result = subprocess.run(
            [ffmpeg_bin(), "-y", "-loglevel", "error",
             "-ss", f"{position:.3f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(dest)],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and dest.is_file():
            frames.append(dest)

    if not frames:
        raise RuntimeError("Nenhum frame foi extraido do video.")
    return frames


def build_prompt(hook: str, caption: str) -> str:
    linhas = [
        "Voce revisa Reels verticais (1080x1920) antes da publicacao.",
        "",
        "O layout esperado, de cima para baixo:",
        "1. painel com o video de origem;",
        "2. faixa preta com o gancho (texto curto, 1 ou 2 linhas);",
        "3. painel do personagem, com texto de apoio e uma chamada final.",
        "",
        f"Zona segura do Instagram: y={SAFE_TOP} ate y={SAFE_BOTTOM} de 1920.",
        "Fora disso o app cobre com header, legenda, audio e botoes.",
        "",
        "Analise os frames e responda:",
        "- cabeca_cortada: o topo da cabeca ou o queixo do personagem esta",
        "  cortado pela borda do painel? Enquadramento apertado nao e defeito;",
        "  so marque se parte do rosto realmente sai do quadro.",
        "- texto_cortado: algum texto aparece cortado, com reticencias no fim,",
        "  saindo pela lateral, sobreposto a outro elemento ou fora da zona",
        "  segura? Frase que termina sem sentido tambem conta.",
        "- problemas: uma frase por defeito, dizendo em qual frame e onde.",
        "- aprovado: true apenas se nao houver nenhum dos dois defeitos.",
    ]
    if hook:
        linhas += ["", f"O gancho deveria aparecer inteiro assim: {hook!r}"]
    if caption:
        linhas += [f"A legenda do post (nao aparece no video): {caption[:400]!r}"]
    return "\n".join(linhas)


RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "aprovado": {"type": "BOOLEAN"},
        "cabeca_cortada": {"type": "BOOLEAN"},
        "texto_cortado": {"type": "BOOLEAN"},
        "problemas": {"type": "ARRAY", "items": {"type": "STRING"}},
        "observacoes": {"type": "STRING"},
    },
    "required": ["aprovado", "cabeca_cortada", "texto_cortado", "problemas"],
}


def review_frames(frames: list[Path], hook: str = "", caption: str = "") -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY nao encontrada no .env")

    # Import tardio: sem a lib, o resto do projeto continua publicando.
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    parts: list = [build_prompt(hook, caption)]
    for frame in frames:
        parts.append(
            types.Part.from_bytes(data=frame.read_bytes(), mime_type="image/jpeg")
        )

    try:
        response = client.models.generate_content(
            model=current_model(),
            contents=parts,
            config=types.GenerateContentConfig(
                temperature=0.0,  # revisao precisa ser reproduzivel
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
            ),
        )
    except Exception as exc:
        # O SDK levanta ClientError/ServerError proprios. Quem chama so trata
        # RuntimeError, e uma revisao indisponivel nao pode barrar o post.
        raise RuntimeError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc

    text = (response.text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Resposta da IA nao era JSON: {text[:200]}") from exc


def review_video(video: Path, hook: str = "", caption: str = "",
                 frames: int = FRAME_COUNT) -> dict:
    with tempfile.TemporaryDirectory(prefix="video_qa_") as tmp:
        extracted = extract_frames(video, Path(tmp), frames)
        return review_frames(extracted, hook=hook, caption=caption)


def format_report(result: dict) -> str:
    if result.get("aprovado"):
        linhas = ["✅ Revisao da IA: nenhum defeito de enquadramento ou texto."]
    else:
        linhas = ["⚠️ Revisao da IA encontrou problemas:"]
        if result.get("cabeca_cortada"):
            linhas.append("• Cabeca do personagem cortada")
        if result.get("texto_cortado"):
            linhas.append("• Texto cortado ou fora da zona segura")
        for problema in result.get("problemas") or []:
            linhas.append(f"  - {problema}")
    observacoes = (result.get("observacoes") or "").strip()
    if observacoes:
        linhas.append(f"\n{observacoes}")
    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Revisa o Reel montado com a IA antes de publicar."
    )
    parser.add_argument("--video", required=True, help="Video final a revisar.")
    parser.add_argument("--hook", default="", help="Gancho que deveria aparecer inteiro.")
    parser.add_argument("--caption", default="", help="Legenda do post.")
    parser.add_argument("--frames", type=int, default=FRAME_COUNT,
                        help="Quantos frames enviar para a IA.")
    parser.add_argument("--strict", action="store_true",
                        help="Sai com codigo 2 se a IA reprovar o video.")
    args = parser.parse_args()

    video = Path(args.video)
    if not video.is_file():
        print(f"Video nao encontrado: {video}", file=sys.stderr)
        return 1

    try:
        result = review_video(video, hook=args.hook, caption=args.caption,
                              frames=args.frames)
    except RuntimeError as exc:
        # A revisao e opcional: falha dela nao pode derrubar a publicacao.
        print(f"Aviso: revisao da IA indisponivel ({exc}).", file=sys.stderr)
        return 0

    print(format_report(result))
    if args.strict and not result.get("aprovado"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
