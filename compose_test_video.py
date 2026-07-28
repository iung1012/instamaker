import argparse
import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

CANVAS_W = 1080
CANVAS_H = 1920

# Altura do painel de video. O padrao 760 e mantido, mas o painel flexiona
# dentro da faixa abaixo para encaixar o video sem cortar nada.
# O minimo 600 existe para que 16:9 (608px em 1080 de largura) caiba exato:
# com um minimo maior sobrariam barras de poucos pixels, que parecem defeito.
TOP_H_DEFAULT = 760
TOP_H_MIN = 600
TOP_H_MAX = 880

# Fracao do painel do avatar onde o bloco central e ancorado. O rosto do
# personagem fica no topo desse painel, entao o texto desce para o corpo.
BODY_ANCHOR = 0.42

# Onde comeca o crop vertical do painel do avatar, em fracao da sobra.
# 0.0 ancora no topo: o que nao cabe sai pelos pes, nunca pela cabeca. Com o
# crop central de antes, personagem enquadrado com a cabeca alta perdia o topo
# do rosto -- defeito que nao gera erro no ffmpeg e so aparecia na conta.
AVATAR_CROP_ANCHOR = 0.0

# Faixa preta entre o video e o avatar, onde mora o hook. A altura acompanha
# o numero de linhas do hook, dentro destes limites.
BAND_H_MIN = 130
BAND_H_MAX = 250
BAND_PADDING = 34
BAND_COLOR = "black"

BODY_SIZE = 46
CTA_SIZE = 48
BODY_LINE_STEP = int(BODY_SIZE * 1.24)
CTA_LINE_STEP = int(CTA_SIZE * 1.24)

# Corpos de fonte tentados para o hook, do maior para o menor, com quantos
# caracteres cabem por linha em cada um. O hook encolhe antes de ser cortado:
# reticencias na faixa comem justamente o final da frase, que e onde mora a
# informacao que prende.
HOOK_SIZE_STEPS = [(56, 26), (50, 30), (44, 34)]
HOOK_SIZE = HOOK_SIZE_STEPS[0][0]
HOOK_MAX_CHARS = HOOK_SIZE_STEPS[0][1]
HOOK_MAX_LINES = 2

# Zonas seguras do Instagram Reels: o topo tem o header e o rodape tem
# legenda, audio e botoes. Texto fora dessas faixas fica escondido.
SAFE_TOP = 230
SAFE_BOTTOM = 1580

OUTPUT_FPS = 30
END_FADE_SECONDS = 0.4

CTA_PHRASES = [
    "Comente EU QUERO para parte 2",
    "Salve este video para aplicar depois",
    "Me siga para mais estrategias praticas",
]


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def resolve_executable(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found

    executable_name = f"{name}.exe" if sys.platform.startswith("win") and not name.endswith(".exe") else name
    path_values = [os.environ.get("PATH", "")]
    if sys.platform.startswith("win"):
        try:
            import winreg

            for hive, subkey in (
                (winreg.HKEY_CURRENT_USER, "Environment"),
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ),
            ):
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        value, _ = winreg.QueryValueEx(key, "Path")
                        path_values.append(value)
                except OSError:
                    pass
        except ImportError:
            pass

    for raw_path in path_values:
        for item in raw_path.split(os.pathsep):
            if not item:
                continue
            candidate = Path(item.strip().strip('"')) / executable_name
            if candidate.exists():
                return str(candidate)

    if sys.platform.startswith("win"):
        winget_packages = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
        if winget_packages.exists():
            matches = list(winget_packages.glob(f"**/{executable_name}"))
            if matches:
                return str(matches[0])

    raise RuntimeError(
        f"{name} nao encontrado no PATH. Instale o FFmpeg e confirme que '{name} -version' funciona no terminal."
    )


def resolve_font_file() -> Path:
    """Prefere sempre uma variante bold: texto fino some no feed."""
    candidates = [
        Path(os.environ["AUTOMATION_FONT_PATH"])
        if os.environ.get("AUTOMATION_FONT_PATH")
        else None,
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\ARIALBD.TTF"),
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path(r"C:\Windows\Fonts\calibrib.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
        # Fallbacks regulares, so se nenhum bold existir.
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise RuntimeError(
        "Nenhuma fonte utilizavel encontrada. No Ubuntu, instale fonts-dejavu-core."
    )


def escape_path_for_filter(path: Path | str) -> str:
    """Caminho seguro dentro de um filtergraph do ffmpeg."""
    text = str(path).replace("\\", "/")
    return text.replace(":", r"\:")


def probe_media(path: Path) -> dict:
    """Le dimensoes, fps, duracao e presenca de audio em uma unica chamada."""
    ffprobe_bin = resolve_executable("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe falhou para {path}: {result.stderr.strip()}")

    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    width = int(video_stream.get("width") or 0) if video_stream else 0
    height = int(video_stream.get("height") or 0) if video_stream else 0

    # Corrige pixels nao quadrados para obter o aspecto real de exibicao.
    if video_stream:
        sar = str(video_stream.get("sample_aspect_ratio") or "1:1")
        if ":" in sar:
            try:
                sar_num, sar_den = (int(part) for part in sar.split(":", 1))
                if sar_num > 0 and sar_den > 0:
                    width = int(round(width * sar_num / sar_den))
            except ValueError:
                pass

    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    return {
        "width": max(0, width),
        "height": max(0, height),
        "duration": duration,
        "has_audio": has_audio,
    }


def make_even(value: int) -> int:
    value = int(value)
    return value if value % 2 == 0 else value - 1


def compute_top_height(src_w: int, src_h: int, min_h: int, max_h: int, default_h: int) -> int:
    """Altura do painel que exibe o video inteiro, presa dentro da faixa."""
    if src_w <= 0 or src_h <= 0:
        return make_even(default_h)
    ideal = round(CANVAS_W * src_h / src_w)
    return make_even(max(min_h, min(max_h, ideal)))


def fit_inside(src_w: int, src_h: int, box_w: int, box_h: int) -> tuple[int, int]:
    """Maior tamanho que cabe na caixa mantendo o aspecto (sem cortar)."""
    if src_w <= 0 or src_h <= 0:
        return box_w, box_h
    ratio = min(box_w / src_w, box_h / src_h)
    return make_even(round(src_w * ratio)), make_even(round(src_h * ratio))


def build_video_panel(
    input_label: str,
    output_label: str,
    src_w: int,
    src_h: int,
    panel_w: int,
    panel_h: int,
    pad_mode: str,
    animate: bool,
) -> list[str]:
    """Encaixa o video no painel sem cortar. Sobra vira fundo desfocado."""
    fitted_w, fitted_h = fit_inside(src_w, src_h, panel_w, panel_h)
    needs_pad = fitted_w < panel_w or fitted_h < panel_h

    # Deriva do enquadramento. Custa 7% de overscale recortado, entao fica
    # desligada por padrao: em gravacao de tela ou video com texto, esse corte
    # come justamente as bordas onde a informacao esta.
    if animate:
        drift_x = max(4, int(fitted_w * 0.016))
        drift_y = max(3, int(fitted_h * 0.012))
        motion = (
            f",scale=iw*1.07:ih*1.07,"
            f"crop={fitted_w}:{fitted_h}:"
            f"'(iw-{fitted_w})/2 + sin(t*1.1)*{drift_x}':"
            f"'(ih-{fitted_h})/2 + cos(t*0.9)*{drift_y}'"
        )
    else:
        motion = ""

    fg_chain = (
        f"fps={OUTPUT_FPS},scale={fitted_w}:{fitted_h}:flags=lanczos"
        f"{motion},setsar=1"
    )

    if not needs_pad:
        return [f"[{input_label}]{fg_chain}[{output_label}]"]

    if pad_mode == "color":
        return [
            f"[{input_label}]{fg_chain},"
            f"pad={panel_w}:{panel_h}:(ow-iw)/2:(oh-ih)/2:color=0x0E0E12[{output_label}]"
        ]

    # pad_mode == "blur": preenche a sobra com o proprio video ampliado e borrado.
    split_a = f"{output_label}_src"
    split_b = f"{output_label}_bgsrc"
    return [
        f"[{input_label}]split=2[{split_a}][{split_b}]",
        (
            f"[{split_b}]fps={OUTPUT_FPS},"
            f"scale={panel_w}:{panel_h}:force_original_aspect_ratio=increase,"
            f"crop={panel_w}:{panel_h},gblur=sigma=32,eq=brightness=-0.10:saturation=0.85,"
            f"setsar=1[{output_label}_bg]"
        ),
        f"[{split_a}]{fg_chain}[{output_label}_fg]",
        (
            f"[{output_label}_bg][{output_label}_fg]"
            f"overlay=(W-w)/2:(H-h)/2:format=auto[{output_label}]"
        ),
    ]


def build_avatar_panel(
    input_label: str,
    output_label: str,
    panel_w: int,
    panel_h: int,
) -> list[str]:
    """O avatar e asset proprio: pode preencher cortando as bordas.

    Horizontal continua centralizado; o vertical ancora em AVATAR_CROP_ANCHOR
    para preservar o rosto.
    """
    return [
        (
            f"[{input_label}]fps={OUTPUT_FPS},"
            f"scale={panel_w}:{panel_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={panel_w}:{panel_h}:(iw-{panel_w})/2:(ih-{panel_h})*{AVATAR_CROP_ANCHOR}"
            f",setsar=1[{output_label}]"
        )
    ]


def read_description(info_file: Path) -> str:
    if not info_file.exists():
        return ""
    content = info_file.read_text(encoding="utf-8", errors="ignore")
    for marker in ("Descrição:", "Descricao:"):
        if marker in content:
            return content.split(marker, 1)[1].strip()
    return content.strip()


def phrase_from_description(description: str) -> str:
    """Ultimo recurso para o hook quando a abertura do texto nao serve.

    Retorna string vazia em vez de sortear uma frase pronta: quem chama
    decide o fallback, entao nenhuma frase generica entra na faixa sem que
    alguem tenha pedido.
    """
    text = " ".join(description.replace("\n", " ").split())
    low = text.lower()
    if "google maps" in low:
        return "Google Maps pode virar sua maquina de clientes."
    if "claude" in low and "code" in low:
        return "Claude Code acelera entregas de verdade."
    if "automate" in low or "automation" in low or "automat" in low:
        return "Automacao certa multiplica resultado."
    if "video" in low and "tutorial" in low:
        return "Tutorial bom encurta meses de aprendizado."
    if len(text) < 12:
        return ""
    first_sentence = text.split(".")[0].strip()
    if len(first_sentence) > 72:
        first_sentence = first_sentence[:72].rstrip(" ,;:-") + "..."
    return first_sentence


def shorten_hook(text: str, max_chars: int = HOOK_MAX_CHARS * HOOK_MAX_LINES) -> str:
    """Reduz o hook ao nucleo da frase: a faixa so funciona se for curta."""
    cleaned = " ".join(str(text).replace("\n", " ").split())
    if not cleaned:
        return ""

    # Fora URLs, mencoes, hashtags e emoji: nada disso ajuda a fisgar.
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"[@#]\w+", "", cleaned)
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and ord(ch) < 0x2190)
    cleaned = " ".join(cleaned.split()).strip(" -–—,;:|")
    if not cleaned:
        return ""

    # Menor trecho que ainda e uma oracao inteira: corta na pontuacao mais
    # proxima em vez de pegar a frase toda e depois truncar no meio.
    heads = [
        cleaned.split(separator, 1)[0].strip()
        for separator in (". ", "! ", "? ", " | ", " — ", " – ", " - ", ": ")
        if separator in cleaned
    ]
    usable = [head for head in heads if len(head) >= 12]
    if usable:
        cleaned = min(usable, key=len)
    cleaned = cleaned.rstrip(" .,;:-")

    if len(cleaned) <= max_chars:
        return cleaned

    # Corta na fronteira de palavra, nunca no meio de uma.
    clipped = cleaned[: max_chars + 1]
    if " " in clipped:
        clipped = clipped[: clipped.rfind(" ")]
    return clipped.rstrip(" .,;:-")


def derive_hook_from_source(
    input_video: Path,
    fallback: str,
    max_chars: int,
    translate: bool = False,
) -> str:
    """Le o _info.txt gravado pelo scraper e tira o hook do conteudo real.

    A abertura do proprio texto vem primeiro. As frases enlatadas de
    phrase_from_description() so entram se a descricao nao render nada:
    elas casam por palavra-chave, entao qualquer post que cite "AI" acabaria
    com o mesmo hook, que e o oposto de um hook do conteudo.

    Antes, so o modo em lote lia o _info.txt. A pipeline chama um video por
    vez, entao na pratica o hook era sempre a mesma frase fixa.
    """
    info_file = input_video.with_name(f"{input_video.stem}_info.txt")
    description = read_description(info_file)
    if not description:
        return fallback

    hook = shorten_hook(description, max_chars)
    if len(hook) < 12:
        hook = shorten_hook(phrase_from_description(description), max_chars)
    if not hook:
        return fallback

    if translate:
        try:
            hook = shorten_hook(translate_to_ptbr(hook), max_chars)
        except RuntimeError as exc:
            print(f"Aviso: hook mantido no idioma original ({exc}).")
    return hook or fallback


def fit_hook(text: str, max_lines: int = HOOK_MAX_LINES) -> tuple[str, int, list[str]]:
    """Encaixa o hook inteiro na faixa reduzindo o corpo da fonte.

    Devolve (texto usado, corpo da fonte, linhas). So corta palavras se nem
    o menor corpo resolver, e mesmo assim sem reticencias.
    """
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return "", HOOK_SIZE_STEPS[0][0], []

    def wrap(value: str, width: int) -> list[str]:
        return textwrap.wrap(
            value, width=width, break_long_words=False, break_on_hyphens=False
        )

    for size, max_chars in HOOK_SIZE_STEPS:
        wrapped = wrap(cleaned, max_chars)
        if len(wrapped) <= max_lines:
            return cleaned, size, wrapped

    size, max_chars = HOOK_SIZE_STEPS[-1]
    words = cleaned.split()
    while len(words) > 1:
        words.pop()
        candidate = " ".join(words).rstrip(" .,;:-")
        wrapped = wrap(candidate, max_chars)
        if wrapped and len(wrapped) <= max_lines:
            return candidate, size, wrapped

    clipped = cleaned[:max_chars]
    return clipped, size, [clipped]


def plan_band_height(hook_lines: int, hook_step: int) -> int:
    if hook_lines <= 0:
        return 0
    raw = hook_lines * hook_step + BAND_PADDING * 2
    return make_even(max(BAND_H_MIN, min(BAND_H_MAX, raw)))


def wrap_lines(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Quebra em linhas. O conteudo em si nao precisa de escape: vai por textfile."""
    normalized = " ".join(text.replace("\n", " ").split())
    if not normalized:
        return []
    wrapped = textwrap.wrap(
        normalized,
        width=max(8, max_chars),
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not wrapped:
        return []
    if len(wrapped) > max_lines:
        wrapped = wrapped[:max_lines]
        if not wrapped[-1].endswith("..."):
            wrapped[-1] = wrapped[-1].rstrip(" .,:;!-") + "..."
    return wrapped


def build_alpha_expression(start: float, end: float, fade: float) -> str:
    """Fade in/out suave em vez de aparecer e sumir de uma vez."""
    fade = max(0.05, min(fade, max(0.05, (end - start) / 2)))
    fade_in_end = start + fade
    fade_out_start = end - fade
    return (
        f"if(lt(t,{start:.3f}),0,"
        f"if(lt(t,{fade_in_end:.3f}),(t-{start:.3f})/{fade:.3f},"
        f"if(lt(t,{fade_out_start:.3f}),1,"
        f"if(lt(t,{end:.3f}),({end:.3f}-t)/{fade:.3f},0))))"
    )


def append_text_block(
    filters: list[str],
    input_label: str,
    output_prefix: str,
    lines: list[str],
    text_dir: Path,
    font_file: str,
    font_size: int,
    start_y: int,
    line_step: int,
    start_time: float,
    end_time: float,
    fade: float,
) -> str:
    """Uma drawtext por linha (necessario para centralizar cada linha).

    O texto vai por `textfile` com `expansion=none`: nenhum caractere do tweet
    precisa de escape, entao `%`, `[`, `]`, `:` e aspas nao quebram mais o render.
    """
    current_label = input_label
    if not lines:
        return current_label

    alpha = build_alpha_expression(start_time, end_time, fade)
    enable = f"between(t,{start_time:.3f},{end_time:.3f})"

    for index, line in enumerate(lines):
        text_file = text_dir / f"{output_prefix}{index}.txt"
        text_file.write_text(line, encoding="utf-8")
        output_label = f"{output_prefix}{index}"
        filters.append(
            f"[{current_label}]drawtext="
            f"fontfile='{font_file}':"
            f"textfile='{escape_path_for_filter(text_file)}':"
            f"expansion=none:"
            f"fontcolor=white:fontsize={font_size}:"
            f"borderw=6:bordercolor=black@0.92:"
            f"shadowcolor=black@0.55:shadowx=2:shadowy=3:"
            f"x=(w-text_w)/2:y={start_y + index * line_step}:"
            f"alpha='{alpha}':"
            f"enable='{enable}'[{output_label}]"
        )
        current_label = output_label
    return current_label


def generate_tts_audio(
    text: str,
    out_file: Path,
    provider: str,
    voice_name: str,
    voice_rate: str,
    voice_pitch: str,
    voice_volume: str,
    gtts_lang: str,
) -> None:
    if provider in {"auto", "edge"}:
        try:
            import edge_tts  # type: ignore

            async def _run() -> None:
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=voice_name,
                    rate=voice_rate,
                    pitch=voice_pitch,
                    volume=voice_volume,
                )
                await communicate.save(str(out_file))

            asyncio.run(_run())
            return
        except ImportError:
            if provider == "edge":
                raise RuntimeError("edge-tts nao instalado. Rode: pip install edge-tts")
        except Exception as exc:
            if provider == "edge":
                raise RuntimeError(f"Falha ao gerar voz com edge-tts: {exc}") from exc

    if provider in {"auto", "gtts"}:
        try:
            from gtts import gTTS  # type: ignore

            gTTS(text=text, lang=gtts_lang).save(str(out_file))
            return
        except ImportError as exc:
            raise RuntimeError("gTTS nao instalado. Rode: pip install gTTS") from exc
        except Exception as exc:
            raise RuntimeError(f"Falha ao gerar voz com gTTS: {exc}") from exc

    raise RuntimeError("Provider de voz invalido. Use: auto, edge ou gtts")


def transcribe_video_audio(video_path: Path, model_name: str, language: str | None) -> str:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise RuntimeError("faster-whisper nao instalado. Rode: pip install faster-whisper") from exc

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(video_path), language=language, vad_filter=True)
    parts = [seg.text.strip() for seg in segments if seg.text and seg.text.strip()]
    transcript = " ".join(parts).strip()
    if not transcript:
        raise RuntimeError("Nao foi possivel transcrever o audio do video.")
    return transcript


def translate_to_ptbr(text: str) -> str:
    try:
        from deep_translator import GoogleTranslator  # type: ignore
    except ImportError as exc:
        raise RuntimeError("deep-translator nao instalado. Rode: pip install deep-translator") from exc

    translated = GoogleTranslator(source="auto", target="pt").translate(text)
    if not translated or not translated.strip():
        raise RuntimeError("Falha ao traduzir texto para PT-BR.")
    return translated.strip()


def polish_text_for_narration(text: str) -> str:
    cleaned = text
    replacements = {
        "I/O": "I O",
        "AI": "inteligencia artificial",
        "HTML": "agá tê eme éle",
        "/": " e ",
        " - ": ", ",
    }
    for src, dst in replacements.items():
        cleaned = cleaned.replace(src, dst)
    cleaned = " ".join(cleaned.replace("\n", " ").split())
    cleaned = cleaned.strip(" ,.-")

    # Prosodia: cria pausas naturais para TTS em listas e trocas de ideia.
    cleaned = cleaned.replace(":", ". ")
    cleaned = cleaned.replace(";", ". ")
    cleaned = cleaned.replace(" - ", ". ")
    cleaned = cleaned.replace("(", ", ")
    cleaned = cleaned.replace(")", ", ")
    cleaned = cleaned.replace(" ,", ",")
    cleaned = cleaned.replace(" .", ".")

    pause_markers = [
        " alem disso ",
        " por fim ",
        " bonus ",
        " e tambem ",
        " agora ",
    ]
    lower = f" {cleaned.lower()} "
    for marker in pause_markers:
        if marker in lower:
            token = marker.strip()
            cleaned = cleaned.replace(token, f"... {token}")
            lower = f" {cleaned.lower()} "

    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."
    return " ".join(cleaned.split())


def build_filter(
    hook_lines: list[str],
    hook_size: int,
    body_text: str,
    cta_text: str,
    font_file: str,
    text_dir: Path,
    total_duration: float,
    intro_seconds: float,
    outro_seconds: float,
    animate_top: bool,
    text_box_opacity: float,
    src_w: int,
    src_h: int,
    top_h: int,
    band_h: int,
    pad_mode: str,
    text_fade: float,
    body_y_override: int,
    band_color: str,
) -> str:
    avatar_h = CANVAS_H - top_h - band_h
    safe_font = escape_path_for_filter(font_file)

    hook_step = int(hook_size * 1.20)
    body_step, cta_step = BODY_LINE_STEP, CTA_LINE_STEP
    body_size, cta_size = BODY_SIZE, CTA_SIZE

    safe_hook = hook_lines
    safe_body = wrap_lines(body_text, max_chars=28, max_lines=3)
    safe_cta = wrap_lines(cta_text, max_chars=24, max_lines=2)

    intro_end = min(max(0.3, intro_seconds), max(0.3, total_duration * 0.4))
    outro_start = max(intro_end + 0.5, total_duration - max(0.8, outro_seconds))

    # Hook centralizado na faixa preta, entre os dois paineis.
    hook_y = top_h + max(0, (band_h - len(safe_hook) * hook_step) // 2)
    # Corpo no painel do avatar, abaixo do rosto: cair sobre a mascara do
    # personagem foi o pior efeito colateral do layout antigo.
    if body_y_override > 0:
        body_y = body_y_override
    else:
        body_y = top_h + band_h + int(avatar_h * BODY_ANCHOR)
    body_y = min(body_y, SAFE_BOTTOM - len(safe_body) * body_step)
    # CTA ancorado acima da UI inferior do Reels.
    cta_y = SAFE_BOTTOM - len(safe_cta) * cta_step if safe_cta else SAFE_BOTTOM

    filters: list[str] = []
    filters.extend(
        build_video_panel(
            input_label="0:v",
            output_label="top",
            src_w=src_w,
            src_h=src_h,
            panel_w=CANVAS_W,
            panel_h=top_h,
            pad_mode=pad_mode,
            animate=animate_top,
        )
    )
    filters.extend(
        build_avatar_panel(
            input_label="1:v",
            output_label="bottom",
            panel_w=CANVAS_W,
            panel_h=avatar_h,
        )
    )

    if band_h > 0:
        # Fonte de cor gerada dentro do filtergraph: nao entra como input do
        # ffmpeg, entao os indices de audio ([2:a], [3:a]) seguem intactos.
        filters.append(
            f"color=c={band_color}:s={CANVAS_W}x{band_h}:r={OUTPUT_FPS}:"
            f"d={total_duration:.3f}[band]"
        )
        filters.append("[top][band][bottom]vstack=inputs=3[base]")
    else:
        filters.append("[top][bottom]vstack=inputs=2[base]")

    current_label = "base"
    opacity = max(0.0, min(text_box_opacity, 1.0))

    def add_box(label_in: str, label_out: str, y: int, height: int, enable: str) -> str:
        if opacity <= 0 or height <= 0:
            return label_in
        filters.append(
            f"[{label_in}]drawbox=x=70:y={max(0, y - 28)}:w={CANVAS_W - 140}:h={height + 56}:"
            f"color=black@{opacity}:t=fill:enable='{enable}'[{label_out}]"
        )
        return label_out

    body_enable = f"between(t,{intro_end:.3f},{outro_start:.3f})"
    cta_enable = f"between(t,{outro_start:.3f},{total_duration:.3f})"

    # O hook fica o video inteiro: a faixa e um elemento fixo do layout e
    # ficaria preta e vazia se o texto sumisse depois da introducao.
    current_label = append_text_block(
        filters=filters,
        input_label=current_label,
        output_prefix="hookline",
        lines=safe_hook,
        text_dir=text_dir,
        font_file=safe_font,
        font_size=hook_size,
        start_y=hook_y,
        line_step=hook_step,
        start_time=0.0,
        end_time=total_duration,
        fade=text_fade,
    )

    current_label = add_box(current_label, "bodybox", body_y, len(safe_body) * body_step, body_enable)
    current_label = append_text_block(
        filters=filters,
        input_label=current_label,
        output_prefix="bodyline",
        lines=safe_body,
        text_dir=text_dir,
        font_file=safe_font,
        font_size=body_size,
        start_y=body_y,
        line_step=body_step,
        start_time=intro_end,
        end_time=outro_start,
        fade=text_fade,
    )

    current_label = add_box(current_label, "ctabox", cta_y, len(safe_cta) * cta_step, cta_enable)
    current_label = append_text_block(
        filters=filters,
        input_label=current_label,
        output_prefix="ctaline",
        lines=safe_cta,
        text_dir=text_dir,
        font_file=safe_font,
        font_size=cta_size,
        start_y=cta_y,
        line_step=cta_step,
        start_time=outro_start,
        end_time=total_duration,
        fade=text_fade,
    )

    fade_start = max(0.0, total_duration - END_FADE_SECONDS)
    filters.append(
        f"[{current_label}]fade=t=out:st={fade_start:.3f}:d={END_FADE_SECONDS},"
        f"format=yuv420p,setsar=1[v]"
    )
    return ";".join(filters)


def build_audio_filter(
    has_original_audio: bool,
    bg_volume: float,
    music_only: bool,
    enable_ducking: bool,
    has_bg_music: bool,
    has_voice: bool,
    total_duration: float,
    normalize: bool,
) -> str | None:
    if not has_bg_music and not has_voice:
        # Sem trilha e sem narracao: o audio original ainda passa pelo
        # loudnorm e pelo fade, senao cada post sai num volume diferente.
        if not has_original_audio:
            return None
        fade_start = max(0.0, total_duration - END_FADE_SECONDS)
        chain = "[0:a]"
        if normalize:
            chain += "loudnorm=I=-14:TP=-1.5:LRA=11,"
        chain += f"afade=t=out:st={fade_start:.3f}:d={END_FADE_SECONDS},aresample=44100[aout]"
        return chain

    voice_input = "3:a" if has_bg_music and has_voice else "2:a"
    voice_proc = (
        f"[{voice_input}]highpass=f=90,lowpass=f=7800,"
        "acompressor=threshold=-20dB:ratio=2.5:attack=15:release=120,volume=1.6[vnarr]"
    )

    if has_voice:
        if has_bg_music:
            if enable_ducking:
                base = (
                    f"[2:a]volume={bg_volume}[bgm];"
                    f"{voice_proc};"
                    "[bgm][vnarr]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=300:makeup=1[bgduck]"
                )
            else:
                base = f"[2:a]volume={bg_volume}[bgduck];{voice_proc}"

            if music_only or not has_original_audio:
                mixed = f"{base};[vnarr][bgduck]amix=inputs=2:duration=first:dropout_transition=2:weights='1 0.35'[amixed]"
            else:
                mixed = f"{base};[0:a][vnarr][bgduck]amix=inputs=3:duration=first:dropout_transition=2:weights='0.50 1 0.28'[amixed]"
        elif music_only or not has_original_audio:
            mixed = f"{voice_proc};[vnarr]anull[amixed]"
        else:
            mixed = f"{voice_proc};[0:a][vnarr]amix=inputs=2:duration=first:dropout_transition=2:weights='0.50 1'[amixed]"
    elif music_only or not has_original_audio:
        mixed = f"[2:a]volume={bg_volume}[amixed]"
    elif enable_ducking:
        mixed = (
            f"[2:a]volume={bg_volume}[bgm];"
            "[bgm][0:a]sidechaincompress=threshold=0.03:ratio=10:attack=20:release=300:makeup=1[ducked];"
            "[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=2:weights='1 1'[amixed]"
        )
    else:
        mixed = f"[2:a]volume={bg_volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[amixed]"

    # loudnorm alinha o volume ao alvo das redes (~-14 LUFS): sem isso, cada
    # post sai com um volume diferente. O afade evita o corte seco no fim.
    tail = "[amixed]"
    if normalize:
        tail += "loudnorm=I=-14:TP=-1.5:LRA=11,"
    fade_start = max(0.0, total_duration - END_FADE_SECONDS)
    tail += f"afade=t=out:st={fade_start:.3f}:d={END_FADE_SECONDS},aresample=44100[aout]"
    return f"{mixed};{tail}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Monta video vertical com video em cima, avatar embaixo e texto no meio.")
    parser.add_argument("--video", help="Arquivo de video principal (parte de cima)")
    parser.add_argument("--avatar", required=True, help="Arquivo de avatar (imagem ou video, parte de baixo)")
    parser.add_argument(
        "--text",
        help="Texto do bloco central, sobre o avatar. Opcional: sem ele o "
        "avatar fica limpo e a mensagem fica so na faixa e no CTA.",
    )
    parser.add_argument("--hook-text", help="Texto da faixa. Sem isso, sai do _info.txt do conteudo.")
    parser.add_argument("--cta-text", help="Texto de encerramento (CTA)")
    parser.add_argument(
        "--auto-text",
        action="store_true",
        help="Aceito por compatibilidade. Nao injeta mais frase pronta no meio do video.",
    )
    parser.add_argument(
        "--random-text",
        action="store_true",
        help="Aceito por compatibilidade. Sem efeito.",
    )
    parser.add_argument(
        "--phrase-index",
        type=int,
        default=0,
        help="Aceito por compatibilidade. Sem efeito.",
    )
    parser.add_argument("--max-chars-per-line", type=int, default=28, help="Maximo de caracteres por linha do bloco central")
    parser.add_argument("--max-body-lines", type=int, default=3, help="Maximo de linhas do bloco central")
    parser.add_argument("--single-line", action="store_true", help="Forca o bloco central em uma unica linha")
    parser.add_argument("--output", default="saida_teste.mp4", help="Arquivo de saida")
    parser.add_argument("--batch-dir", help="Processa todos os .mp4 do diretorio")
    parser.add_argument("--output-dir", default="outputs_ig", help="Diretorio de saida no modo em lote")
    parser.add_argument("--max-duration", type=float, default=40.0, help="Duracao maxima do video final em segundos")
    parser.add_argument("--intro-seconds", type=float, default=1.4, help="Duracao do gancho inicial")
    parser.add_argument("--outro-seconds", type=float, default=1.8, help="Duracao do CTA final")
    parser.add_argument(
        "--top-motion",
        action="store_true",
        help="Liga a deriva no video superior. Custa 7%% de corte nas bordas.",
    )
    parser.add_argument(
        "--no-top-motion",
        action="store_true",
        help="Mantido por compatibilidade: a deriva ja vem desligada por padrao.",
    )
    parser.add_argument(
        "--body-y",
        type=int,
        default=0,
        help="Posicao vertical do bloco central em pixels. 0 = automatico.",
    )
    parser.add_argument(
        "--band-height",
        type=int,
        default=0,
        help="Altura da faixa do hook em pixels. 0 = calculada pelas linhas do hook.",
    )
    parser.add_argument(
        "--band-color",
        default=BAND_COLOR,
        help=f"Cor da faixa do hook. Padrao: {BAND_COLOR}",
    )
    parser.add_argument(
        "--no-band",
        action="store_true",
        help="Remove a faixa e volta ao layout de dois paineis colados.",
    )
    parser.add_argument(
        "--hook-max-chars",
        type=int,
        default=64,
        help="Tamanho maximo do hook. Acima disso o texto e cortado na palavra.",
    )
    parser.add_argument(
        "--default-hook",
        default="Pare de rolar agora",
        help="Hook usado quando o conteudo nao tem _info.txt aproveitavel.",
    )
    parser.add_argument(
        "--translate-hook",
        action="store_true",
        help="Traduz o hook para PT-BR. Depende de rede e de deep-translator.",
    )
    parser.add_argument("--text-fade", type=float, default=0.28, help="Duracao do fade de entrada/saida dos textos")
    parser.add_argument(
        "--top-height",
        type=int,
        default=TOP_H_DEFAULT,
        help=f"Altura base do painel de video. Padrao: {TOP_H_DEFAULT}",
    )
    parser.add_argument(
        "--top-height-min",
        type=int,
        default=TOP_H_MIN,
        help=f"Altura minima do painel adaptativo. Padrao: {TOP_H_MIN}",
    )
    parser.add_argument(
        "--top-height-max",
        type=int,
        default=TOP_H_MAX,
        help=f"Altura maxima do painel adaptativo. Padrao: {TOP_H_MAX}",
    )
    parser.add_argument(
        "--fixed-layout",
        action="store_true",
        help="Desliga o painel adaptativo e usa sempre --top-height.",
    )
    parser.add_argument(
        "--pad-mode",
        choices=["blur", "color"],
        default="blur",
        help="Preenchimento quando o video nao preenche o painel exatamente.",
    )
    parser.add_argument(
        "--text-box-opacity",
        type=float,
        default=0.0,
        help="Opacidade das caixas atras dos textos (0 remove os quadros; exemplo antigo: 0.6).",
    )
    parser.add_argument("--crf", type=int, default=20, help="Qualidade do x264. Menor = melhor. Padrao: 20")
    parser.add_argument(
        "--preset",
        default="medium",
        help="Preset do x264 (ultrafast..veryslow). Padrao: medium, que fecha "
        "um video de 40s em tempo aceitavel numa VPS modesta.",
    )
    parser.add_argument("--no-loudnorm", action="store_true", help="Desativa normalizacao de volume")
    parser.add_argument("--bg-music", help="Arquivo de musica de fundo (mp3/wav/m4a)")
    parser.add_argument("--bg-volume", type=float, default=0.12, help="Volume da musica de fundo (0.0 a 1.0)")
    parser.add_argument("--music-only", action="store_true", help="Remove audio original e mantem somente musica de fundo")
    parser.add_argument("--disable-ducking", action="store_true", help="Desativa ducking automatico da musica")
    parser.add_argument("--voice-file", help="Arquivo de narracao (mp3/wav/m4a)")
    parser.add_argument("--voice-text", help="Texto para gerar narracao por IA (TTS)")
    parser.add_argument("--voice-from-video", action="store_true", help="Transcreve o audio do video e gera narracao com esse texto")
    parser.add_argument("--transcribe-model", default="small", help="Modelo do faster-whisper (ex: tiny, base, small, medium)")
    parser.add_argument("--transcribe-lang", help="Idioma da transcricao (ex: pt, en). Vazio = auto")
    parser.add_argument("--voice-provider", default="auto", choices=["auto", "edge", "gtts"], help="Provider TTS")
    parser.add_argument("--voice-name", default="pt-BR-AntonioNeural", help="Voz do edge-tts")
    parser.add_argument("--voice-rate", default="-8%", help="Velocidade da voz edge-tts (ex: +10%%, -10%%)")
    parser.add_argument("--voice-pitch", default="-2Hz", help="Tom da voz edge-tts (ex: +2Hz, -2Hz)")
    parser.add_argument("--voice-volume", default="+0%", help="Volume da voz edge-tts (ex: +0%%, +10%%)")
    parser.add_argument("--voice-lang", default="pt-br", help="Idioma para gTTS (ex: pt-br, en)")
    parser.add_argument("--translate-voice-to-ptbr", action="store_true", help="Traduz o texto da narracao para portugues (Brasil) antes do TTS")
    parser.add_argument("--print-command", action="store_true", help="Mostra o comando ffmpeg completo antes de rodar")
    args = parser.parse_args()

    video = Path(args.video) if args.video else None
    avatar = Path(args.avatar)
    output = Path(args.output)
    try:
        font_path = resolve_font_file()
    except RuntimeError as exc:
        print(f"Erro: {exc}")
        return 1

    if not avatar.exists():
        print(f"Erro: avatar nao encontrado: {avatar}")
        return 1

    bg_music = Path(args.bg_music) if args.bg_music else None
    if bg_music and not bg_music.exists():
        print(f"Erro: musica de fundo nao encontrada: {bg_music}")
        return 1

    voice_file = Path(args.voice_file) if args.voice_file else None
    if voice_file and not voice_file.exists():
        print(f"Erro: arquivo de narracao nao encontrado: {voice_file}")
        return 1

    # O bloco central so aparece com --text explicito. A faixa carrega a
    # mensagem do conteudo e o CTA fecha; uma terceira frase generica no meio
    # so competia com as duas.
    selected_text = args.text or ""

    def render_one(input_video: Path, out_file: Path, text_value: str, hook_value: str | None, cta_value: str | None) -> int:
        probe = probe_media(input_video)
        source_duration = probe["duration"] or args.max_duration
        duration = min(source_duration, args.max_duration)
        if duration <= 0:
            print(f"Erro: duracao invalida para {input_video.name}")
            return 1

        if args.fixed_layout:
            top_h = make_even(args.top_height)
        else:
            top_h = compute_top_height(
                src_w=probe["width"],
                src_h=probe["height"],
                min_h=args.top_height_min,
                max_h=args.top_height_max,
                default_h=args.top_height,
            )

        hook_final = hook_value or derive_hook_from_source(
            input_video,
            fallback=args.default_hook,
            max_chars=args.hook_max_chars,
            translate=args.translate_hook,
        )
        hook_final = shorten_hook(hook_final, args.hook_max_chars)
        hook_final, hook_size, hook_lines = fit_hook(hook_final)
        hook_step = int(hook_size * 1.20)
        band_h = 0 if args.no_band else (
            args.band_height or plan_band_height(len(hook_lines), hook_step)
        )
        band_h = make_even(band_h)

        fitted_w, fitted_h = fit_inside(probe["width"], probe["height"], CANVAS_W, top_h)
        fill_note = "preenche o painel" if fitted_w >= CANVAS_W and fitted_h >= top_h else f"encaixe {fitted_w}x{fitted_h}"
        print(
            f"Layout: fonte {probe['width']}x{probe['height']} -> "
            f"video {CANVAS_W}x{top_h} ({fill_note}) / faixa {CANVAS_W}x{band_h} / "
            f"avatar {CANVAS_W}x{CANVAS_H - top_h - band_h}"
        )
        print(f"Hook na faixa: {hook_final!r}")

        # O bloco central sempre passou por wrap_lines; deixar o texto cru aqui
        # evita a quebra dupla que antes anulava --max-chars-per-line.
        body_max_lines = 1 if args.single_line else max(1, args.max_body_lines)
        body_max_chars = 999 if args.single_line else args.max_chars_per_line

        cta_final = cta_value or random.choice(CTA_PHRASES)

        has_original_audio = probe["has_audio"]

        temp_voice_path: Path | None = None
        render_voice_file = voice_file

        narration_text = args.voice_text
        if not narration_text and args.voice_from_video:
            try:
                narration_text = transcribe_video_audio(
                    video_path=input_video,
                    model_name=args.transcribe_model,
                    language=args.transcribe_lang,
                )
                print("Transcricao automatica concluida para narracao.")
            except Exception as exc:
                print(f"Erro na transcricao automatica: {exc}")
                return 1

        if narration_text:
            if args.translate_voice_to_ptbr:
                try:
                    narration_text = translate_to_ptbr(narration_text)
                    print("Narracao traduzida para PT-BR.")
                except Exception as exc:
                    print(f"Erro ao traduzir narracao: {exc}")
                    return 1
            narration_text = polish_text_for_narration(narration_text)
            temp_voice_path = Path(tempfile.gettempdir()) / f"ig_tts_{input_video.stem}.mp3"
            try:
                generate_tts_audio(
                    text=narration_text,
                    out_file=temp_voice_path,
                    provider=args.voice_provider,
                    voice_name=args.voice_name,
                    voice_rate=args.voice_rate,
                    voice_pitch=args.voice_pitch,
                    voice_volume=args.voice_volume,
                    gtts_lang=args.voice_lang,
                )
                render_voice_file = temp_voice_path
                print(f"Narracao IA gerada: {temp_voice_path}")
            except Exception as exc:
                print(f"Erro ao gerar narracao IA: {exc}")
                return 1

        has_bg_music = bg_music is not None
        has_voice = render_voice_file is not None

        text_dir = Path(tempfile.mkdtemp(prefix="ig_text_"))
        try:
            body_lines = wrap_lines(text_value, body_max_chars, body_max_lines)
            filter_complex = build_filter(
                hook_lines=hook_lines,
                hook_size=hook_size,
                body_text=" ".join(body_lines) if body_lines else text_value,
                cta_text=cta_final,
                font_file=str(font_path),
                text_dir=text_dir,
                total_duration=duration,
                intro_seconds=args.intro_seconds,
                outro_seconds=args.outro_seconds,
                animate_top=args.top_motion and not args.no_top_motion,
                text_box_opacity=args.text_box_opacity,
                src_w=probe["width"],
                src_h=probe["height"],
                top_h=top_h,
                band_h=band_h,
                pad_mode=args.pad_mode,
                text_fade=args.text_fade,
                body_y_override=args.body_y,
                band_color=args.band_color,
            )

            audio_filter = build_audio_filter(
                has_original_audio=has_original_audio,
                bg_volume=args.bg_volume,
                music_only=args.music_only,
                enable_ducking=not args.disable_ducking,
                has_bg_music=has_bg_music,
                has_voice=has_voice,
                total_duration=duration,
                normalize=not args.no_loudnorm,
            )
            if audio_filter:
                filter_complex = f"{filter_complex};{audio_filter}"

            ffmpeg_bin = resolve_executable("ffmpeg")
            cmd = [ffmpeg_bin, "-y", "-i", str(input_video)]
            if is_video_file(avatar):
                cmd += ["-stream_loop", "-1", "-i", str(avatar)]
            else:
                cmd += ["-loop", "1", "-i", str(avatar)]
            if has_bg_music:
                cmd += ["-stream_loop", "-1", "-i", str(bg_music)]
            if has_voice and render_voice_file is not None:
                cmd += ["-stream_loop", "-1", "-i", str(render_voice_file)]

            cmd += ["-filter_complex", filter_complex, "-map", "[v]"]
            if audio_filter:
                cmd += ["-map", "[aout]"]
            else:
                cmd += ["-map", "0:a?"]

            cmd += [
                "-c:v", "libx264",
                "-preset", args.preset,
                "-crf", str(args.crf),
                "-profile:v", "high",
                "-level", "4.1",
                "-pix_fmt", "yuv420p",
                "-r", str(OUTPUT_FPS),
                "-g", str(OUTPUT_FPS * 2),
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "44100",
                "-ac", "2",
                "-t", f"{duration:.3f}",
                "-movflags", "+faststart",
                str(out_file),
            ]

            if args.print_command:
                print(" ".join(f'"{c}"' if " " in c else c for c in cmd))

            print(f"Executando ffmpeg: {input_video.name} ({duration:.1f}s)")
            result = subprocess.run(cmd)
            returncode = result.returncode
        finally:
            shutil.rmtree(text_dir, ignore_errors=True)
            if temp_voice_path and temp_voice_path.exists():
                try:
                    temp_voice_path.unlink()
                except OSError:
                    pass

        return returncode

    if args.batch_dir:
        batch_dir = Path(args.batch_dir)
        if not batch_dir.exists():
            print(f"Erro: diretorio nao encontrado: {batch_dir}")
            return 1

        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        videos = sorted([p for p in batch_dir.glob("*.mp4") if not p.name.endswith("_final.mp4")])
        if not videos:
            print("Erro: nenhum .mp4 encontrado no diretorio em lote.")
            return 1

        ok = 0
        for v in videos:
            # Sem hook explicito, render_one extrai do _info.txt: assim o lote
            # e a pipeline chegam ao mesmo hook para o mesmo video.
            text_for_video = selected_text
            hook_for_video = args.hook_text
            cta_for_video = args.cta_text or random.choice(CTA_PHRASES)
            out_file = out_dir / f"{v.stem}_final.mp4"
            rc = render_one(v, out_file, text_for_video, hook_for_video, cta_for_video)
            if rc == 0:
                ok += 1
                print(f"Concluido: {out_file}")
            else:
                print(f"Erro ao gerar: {v.name}")

        print(f"Finalizado em lote: {ok}/{len(videos)} videos gerados.")
        return 0 if ok == len(videos) else 1

    if not video or not video.exists():
        print(f"Erro: video nao encontrado: {video}")
        return 1

    rc = render_one(video, output, selected_text, args.hook_text, args.cta_text)
    if rc != 0:
        print("Erro: ffmpeg falhou.")
        return rc

    print(f"Concluido: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
