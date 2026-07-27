import argparse
import json
import random
import re
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "Pillow nao encontrado. Instale com: python -m pip install pillow"
    ) from exc

DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1350

STOPWORDS_PT_EN = {
    "a",
    "about",
    "agora",
    "ai",
    "ainda",
    "all",
    "an",
    "and",
    "ao",
    "aos",
    "as",
    "at",
    "ate",
    "automacao",
    "automation",
    "br",
    "com",
    "como",
    "da",
    "das",
    "de",
    "del",
    "dentro",
    "desse",
    "dessa",
    "do",
    "dos",
    "e",
    "em",
    "entre",
    "essa",
    "esse",
    "esta",
    "este",
    "eu",
    "for",
    "from",
    "gpt",
    "ia",
    "instagram",
    "is",
    "it",
    "mais",
    "mas",
    "me",
    "my",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "of",
    "on",
    "or",
    "os",
    "para",
    "por",
    "pra",
    "que",
    "se",
    "sem",
    "ser",
    "sobre",
    "the",
    "this",
    "to",
    "um",
    "uma",
    "v2",
    "vc",
    "voce",
    "with",
    "you",
}

TOOLS_ROTATION = [
    "ChatGPT + VS Code",
    "Python + FastAPI",
    "Cursor + GitHub",
    "OpenAI API + n8n",
    "Docker + CI/CD",
]


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def abs_path(base: Path, raw: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return (base / candidate).resolve()


def read_info_texts(source_dir: Path, max_files: int) -> list[str]:
    if not source_dir.exists() or not source_dir.is_dir():
        return []

    files = sorted(
        [p for p in source_dir.iterdir() if p.is_file() and p.name.endswith("_info.txt")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    selected = files[: max(1, max_files)]

    texts: list[str] = []
    for file in selected:
        content = file.read_text(encoding="utf-8", errors="ignore")
        if "Descricao:" in content:
            content = content.split("Descricao:", 1)[1]
        if "Descrição:" in content:
            content = content.split("Descrição:", 1)[1]
        compact = " ".join(content.split())
        if compact:
            texts.append(compact)
    return texts


def normalize_word(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9áéíóúãõâêôçÁÉÍÓÚÃÕÂÊÔÇ_-]", "", raw).strip().lower()


def extract_keywords(texts: list[str], limit: int) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        for token in re.split(r"\s+", text):
            word = normalize_word(token)
            if len(word) < 4:
                continue
            if word in STOPWORDS_PT_EN:
                continue
            counter[word] += 1
    return [w for w, _ in counter.most_common(limit)]


def pick_theme(profile_keywords: list[str], extracted_keywords: list[str], fallback_theme: str) -> str:
    candidates = profile_keywords + extracted_keywords
    for candidate in candidates:
        if len(candidate) >= 4:
            return candidate.capitalize()
    return fallback_theme


def clean_sentence(raw: str) -> str:
    text = re.sub(r"https?://\S+", "", raw)
    text = re.sub(r"[@#][A-Za-z0-9_]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -:;,.\n\t")
    return text


def extract_practical_snippets(texts: list[str], limit: int = 12) -> list[str]:
    snippets: list[str] = []
    seen: set[str] = set()
    banned_terms = {
        "follow",
        "rt",
        "retweet",
        "comment",
        "dm",
        "giveaway",
        "priority access",
        "48h",
    }

    for text in texts:
        parts = re.split(r"[\.!?\n]", text)
        for part in parts:
            cleaned = clean_sentence(part)
            if len(cleaned) < 35 or len(cleaned) > 180:
                continue
            lowered = cleaned.casefold()
            if any(term in lowered for term in banned_terms):
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            snippets.append(cleaned)
            if len(snippets) >= limit:
                return snippets

    return snippets


def build_slide_copy(
    theme: str,
    profile_focus: str,
    slide_count: int,
    profile_keywords: list[str],
    snippets: list[str],
) -> list[dict]:
    body_slots = max(1, slide_count - 2)

    frameworks = [
        {
            "title": "Problema tecnico e meta",
            "summary": "Defina o bug, gargalo ou tarefa de engenharia com objetivo claro.",
            "concept": "Sem escopo tecnico objetivo, o conteudo vira teoria e perde valor pratico.",
            "checklist": [
                "Mostre sintoma real do problema.",
                "Defina metrica de sucesso.",
                "Explique impacto no projeto.",
            ],
        },
        {
            "title": "Arquitetura minima",
            "summary": "Apresente stack e fluxo de dados em formato simples e reproduzivel.",
            "concept": "Um diagrama mental claro acelera implementacao e reduz retrabalho.",
            "checklist": [
                "Entrada -> processamento -> saida.",
                "Tecnologias escolhidas.",
                "Tradeoff principal da decisao.",
            ],
        },
        {
            "title": "Implementacao guiada",
            "summary": "Mostre sequencia de codigo que resolve o caso com o menor caminho.",
            "concept": "Quebre o build em etapas curtas para facilitar copia e adaptacao.",
            "checklist": [
                "Passo 1: setup rapido.",
                "Passo 2: logica principal.",
                "Passo 3: saida validada.",
            ],
        },
        {
            "title": "Teste e validacao",
            "summary": "Ensine como validar comportamento antes de publicar ou deployar.",
            "concept": "Demonstrar teste aumenta confianca e autoridade tecnica.",
            "checklist": [
                "Teste de caso feliz.",
                "Teste de erro/edge case.",
                "Metrica final apos ajuste.",
            ],
        },
        {
            "title": "Escala e automacao",
            "summary": "Mostre proximo nivel para transformar script em sistema reutilizavel.",
            "concept": "Conteudo que conecta codigo com impacto de produto gera mais valor percebido.",
            "checklist": [
                "Logs e observabilidade.",
                "Agendamento ou worker.",
                "Versionamento e rollback.",
            ],
        },
    ]

    focus_short = clean_sentence(profile_focus) or "programacao, tecnologia e IA aplicada"
    kwords = [k for k in profile_keywords if k]

    slides: list[dict] = [
        {
            "kind": "cover",
            "title": f"{theme}: carrossel com aplicacao real",
            "subtitle": f"{slide_count - 1} blocos praticos para {focus_short}",
            "learning_points": [
                "Como estruturar conteudo tecnico com clareza.",
                "Como transformar teoria em implementacao real.",
                "Como validar resultado com teste e metrica.",
            ],
        }
    ]

    for idx in range(body_slots):
        framework = frameworks[idx % len(frameworks)]
        keyword = kwords[idx % len(kwords)] if kwords else theme.lower()
        snippet = snippets[idx % len(snippets)] if snippets else ""
        base_context = f"Contexto: perfil de {keyword} com baixa retencao e pouco salvamento."
        topic_tokens = {
            keyword.casefold(),
            theme.casefold(),
            "codigo",
            "code",
            "python",
            "api",
            "backend",
            "frontend",
            "dev",
            "software",
            "llm",
            "ia",
            "automacao",
            "agente",
            "workflow",
            "deploy",
            "teste",
        }
        snippet_lower = snippet.casefold()
        is_relevant_snippet = bool(snippet) and any(token in snippet_lower for token in topic_tokens)

        if is_relevant_snippet:
            short_snippet = snippet[:130].rstrip(" .,:;!?") + "."
            example_context = f"{base_context} Insight observado: {short_snippet}"
        else:
            example_context = base_context
        example_action = (
            f"Acao: implementar {keyword} em 3 partes (entrada, logica e saida), "
            "com snippet curto e stack objetiva."
        )
        example_result = (
            "Resultado esperado: mais salvamentos de devs, comentarios tecnicos qualificados "
            "e pedidos de parte 2."
        )

        slides.append(
            {
                "kind": "value",
                "title": f"Passo {idx + 1}: {framework['title']}",
                "subtitle": framework["summary"],
                "concept": framework["concept"],
                "example_context": example_context,
                "example_action": example_action,
                "example_result": example_result,
                "checklist": framework["checklist"],
                "tool": TOOLS_ROTATION[idx % len(TOOLS_ROTATION)],
            }
        )

    slides.append(
        {
            "kind": "cta",
            "title": "Agora execute no seu perfil tech",
            "subtitle": "Comente CODIGO para receber um roteiro tecnico variado.",
            "actions": [
                "Escolha 1 problema tecnico comum da sua audiencia.",
                "Monte 3 slides com arquitetura, codigo e validacao.",
                "Publique e compare salvamentos/comentarios tecnicos em 24h.",
            ],
        }
    )

    return slides[:slide_count]


def image_query_for_slide(profile_keywords: list[str], theme: str, slide: dict, idx: int) -> str:
    pieces = profile_keywords[:3]
    pieces.append(theme)
    pieces.append(slide.get("title", "slide"))
    pieces.append(slide.get("kind", "value"))
    query = " ".join(pieces)
    query = re.sub(r"\s+", " ", query).strip()
    return f"{query} {idx}"


def download_background_image(query: str, width: int, height: int, timeout_seconds: int) -> Image.Image | None:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://loremflickr.com/{width}/{height}/{encoded}"
    req = urllib.request.Request(
        url=url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/*",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            content = resp.read()

        img = Image.open(BytesIO(content)).convert("RGB")
        if img.size != (width, height):
            resample_lanczos = getattr(Image, "Resampling", Image).LANCZOS
            img = img.resize((width, height), resample_lanczos)
        return img
    except Exception:
        return None


def make_gradient_background(width: int, height: int, seed_value: str) -> Image.Image:
    rnd = random.Random(seed_value)
    c1 = (rnd.randint(20, 60), rnd.randint(55, 130), rnd.randint(90, 180))
    c2 = (rnd.randint(8, 40), rnd.randint(20, 90), rnd.randint(35, 120))

    img = Image.new("RGB", (width, height), c1)
    draw = ImageDraw.Draw(img)

    for y in range(height):
        t = y / max(1, height - 1)
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        draw.line((0, y, width, y), fill=(r, g, b))

    for _ in range(26):
        radius = rnd.randint(30, 220)
        cx = rnd.randint(-100, width + 100)
        cy = rnd.randint(-100, height + 100)
        alpha = rnd.randint(10, 32)
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        o_draw = ImageDraw.Draw(overlay)
        o_draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(255, 255, 255, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    return img


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates_bold = [
        "arialbd.ttf",
        "segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
    ]
    candidates_regular = [
        "arial.ttf",
        "segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ]
    candidates = candidates_bold if bold else candidates_regular

    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, spacing: int) -> tuple[int, int]:
    left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing)
    return right - left, bottom - top


def wrap_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    words = text.split()
    if not words:
        return ""

    lines: list[str] = []
    current: list[str] = []

    for word in words:
        trial = " ".join(current + [word])
        width, _ = text_bbox(draw, trial, font, spacing=6)
        if width <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]

    if current:
        lines.append(" ".join(current))

    return "\n".join(lines)


def wrap_to_width_limited(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
) -> str:
    wrapped = wrap_to_width(draw, text, font, max_width)
    lines = [line for line in wrapped.splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)

    lines = lines[:max_lines]
    if lines:
        lines[-1] = lines[-1].rstrip(" .,:;!?") + "..."
    return "\n".join(lines)


def apply_dark_overlay(base: Image.Image, alpha: int = 88) -> Image.Image:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, alpha))
    return Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")


def draw_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: tuple[int, int, int, int]) -> None:
    draw.rounded_rectangle(
        box,
        radius=28,
        fill=fill,
        outline=(255, 255, 255, 55),
        width=2,
    )


def draw_common_chrome(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    slide_index: int,
    slide_count: int,
    profile_name: str,
) -> None:
    margin_x = int(width * 0.07)

    badge_text = f"{slide_index + 1}/{slide_count}"
    badge_font = load_font(size=30, bold=True)
    badge_w, badge_h = text_bbox(draw, badge_text, badge_font, spacing=4)
    badge_pad_x = 20
    badge_pad_y = 10
    badge_x2 = width - margin_x
    badge_x1 = badge_x2 - badge_w - (badge_pad_x * 2)
    badge_y1 = int(height * 0.045)
    badge_y2 = badge_y1 + badge_h + (badge_pad_y * 2)

    draw.rounded_rectangle(
        (badge_x1, badge_y1, badge_x2, badge_y2),
        radius=22,
        fill=(0, 0, 0, 140),
        outline=(255, 255, 255, 50),
        width=2,
    )
    draw.text((badge_x1 + badge_pad_x, badge_y1 + badge_pad_y), badge_text, font=badge_font, fill=(255, 255, 255))

    footer = profile_name.strip() if profile_name.strip() else "@seu_perfil"
    footer_font = load_font(size=27, bold=True)
    footer_w, footer_h = text_bbox(draw, footer, footer_font, spacing=4)
    footer_x = margin_x
    footer_y = height - int(height * 0.07) - footer_h

    draw.rounded_rectangle(
        (footer_x - 14, footer_y - 10, footer_x + footer_w + 16, footer_y + footer_h + 10),
        radius=18,
        fill=(0, 0, 0, 130),
    )
    draw.text((footer_x, footer_y), footer, font=footer_font, fill=(255, 255, 255))


def render_cover_slide(draw: ImageDraw.ImageDraw, width: int, height: int, slide: dict) -> None:
    margin_x = int(width * 0.07)
    top = int(height * 0.11)

    title_font = load_font(size=66, bold=True)
    subtitle_font = load_font(size=36, bold=False)

    title = wrap_to_width_limited(draw, slide["title"], title_font, int(width * 0.84), 3)
    subtitle = wrap_to_width_limited(draw, slide["subtitle"], subtitle_font, int(width * 0.84), 3)

    t_w, t_h = text_bbox(draw, title, title_font, spacing=10)
    s_w, s_h = text_bbox(draw, subtitle, subtitle_font, spacing=8)

    hero_box = (
        margin_x,
        top,
        width - margin_x,
        top + t_h + s_h + 120,
    )
    draw_card(draw, hero_box, fill=(8, 16, 34, 155))

    tx = hero_box[0] + 32
    ty = hero_box[1] + 30
    draw.multiline_text((tx, ty), title, font=title_font, fill=(245, 248, 255), spacing=10)
    draw.multiline_text((tx, ty + t_h + 24), subtitle, font=subtitle_font, fill=(218, 232, 245), spacing=8)

    bullet_font = load_font(size=33, bold=False)
    bullet_title_font = load_font(size=36, bold=True)

    list_box = (
        margin_x,
        hero_box[3] + 28,
        width - margin_x,
        int(height * 0.82),
    )
    draw_card(draw, list_box, fill=(6, 12, 28, 145))
    draw.text((list_box[0] + 28, list_box[1] + 22), "Voce vai aprender:", font=bullet_title_font, fill=(255, 255, 255))

    y = list_box[1] + 80
    max_text_width = list_box[2] - list_box[0] - 90
    for point in slide.get("learning_points", []):
        bullet_text = wrap_to_width_limited(draw, point, bullet_font, max_text_width, 2)
        draw.text((list_box[0] + 30, y), "-", font=bullet_title_font, fill=(192, 226, 255))
        draw.multiline_text((list_box[0] + 64, y), bullet_text, font=bullet_font, fill=(232, 241, 252), spacing=6)
        _, h = text_bbox(draw, bullet_text, bullet_font, spacing=6)
        y += h + 22


def render_value_slide(draw: ImageDraw.ImageDraw, width: int, height: int, slide: dict) -> None:
    margin_x = int(width * 0.07)
    gap = 18

    title_font = load_font(size=54, bold=True)
    subtitle_font = load_font(size=31, bold=False)
    section_font = load_font(size=30, bold=True)
    body_font = load_font(size=25, bold=False)
    checklist_font = load_font(size=24, bold=False)

    header_title = wrap_to_width_limited(draw, slide["title"], title_font, int(width * 0.84), 2)
    header_subtitle = wrap_to_width_limited(draw, slide["subtitle"], subtitle_font, int(width * 0.84), 3)

    ht_w, ht_h = text_bbox(draw, header_title, title_font, spacing=8)
    hs_w, hs_h = text_bbox(draw, header_subtitle, subtitle_font, spacing=6)

    header_box = (
        margin_x,
        int(height * 0.09),
        width - margin_x,
        int(height * 0.09) + ht_h + hs_h + 92,
    )
    draw_card(draw, header_box, fill=(8, 16, 35, 155))
    draw.multiline_text((header_box[0] + 26, header_box[1] + 26), header_title, font=title_font, fill=(245, 248, 255), spacing=8)
    draw.multiline_text(
        (header_box[0] + 26, header_box[1] + 26 + ht_h + 16),
        header_subtitle,
        font=subtitle_font,
        fill=(220, 233, 247),
        spacing=6,
    )

    cards_top = header_box[3] + 16
    cards_bottom = int(height * 0.74)
    left_w = int((width - (margin_x * 2) - gap) * 0.42)
    left_box = (margin_x, cards_top, margin_x + left_w, cards_bottom)
    right_box = (left_box[2] + gap, cards_top, width - margin_x, cards_bottom)

    draw_card(draw, left_box, fill=(8, 15, 30, 145))
    draw_card(draw, right_box, fill=(8, 15, 30, 145))

    draw.text((left_box[0] + 20, left_box[1] + 18), "Conceito", font=section_font, fill=(181, 222, 255))
    concept_text = wrap_to_width_limited(
        draw,
        slide.get("concept", ""),
        body_font,
        left_w - 40,
        7,
    )
    draw.multiline_text((left_box[0] + 20, left_box[1] + 66), concept_text, font=body_font, fill=(232, 240, 250), spacing=6)

    tool_text = wrap_to_width_limited(
        draw,
        f"Ferramenta: {slide.get('tool', 'Canva + IA')}",
        body_font,
        left_w - 40,
        2,
    )
    draw.multiline_text((left_box[0] + 20, left_box[3] - 84), tool_text, font=body_font, fill=(192, 226, 255), spacing=6)

    draw.text((right_box[0] + 20, right_box[1] + 18), "Exemplo pratico", font=section_font, fill=(181, 222, 255))

    context = wrap_to_width_limited(draw, slide.get("example_context", ""), body_font, right_box[2] - right_box[0] - 40, 4)
    action = wrap_to_width_limited(draw, slide.get("example_action", ""), body_font, right_box[2] - right_box[0] - 40, 4)
    result = wrap_to_width_limited(draw, slide.get("example_result", ""), body_font, right_box[2] - right_box[0] - 40, 3)

    y = right_box[1] + 66
    draw.multiline_text((right_box[0] + 20, y), context, font=body_font, fill=(232, 240, 250), spacing=6)
    _, context_h = text_bbox(draw, context, body_font, spacing=6)
    y += context_h + 14

    draw.multiline_text((right_box[0] + 20, y), action, font=body_font, fill=(232, 240, 250), spacing=6)
    _, action_h = text_bbox(draw, action, body_font, spacing=6)
    y += action_h + 14

    draw.multiline_text((right_box[0] + 20, y), result, font=body_font, fill=(200, 236, 216), spacing=6)

    checklist_box = (margin_x, cards_bottom + 16, width - margin_x, int(height * 0.9))
    draw_card(draw, checklist_box, fill=(6, 12, 28, 148))
    draw.text((checklist_box[0] + 20, checklist_box[1] + 14), "Checklist de execucao", font=section_font, fill=(181, 222, 255))

    cy = checklist_box[1] + 56
    max_width = checklist_box[2] - checklist_box[0] - 80
    for item in slide.get("checklist", []):
        bullet = wrap_to_width_limited(draw, item, checklist_font, max_width, 2)
        draw.text((checklist_box[0] + 20, cy), "-", font=section_font, fill=(181, 222, 255))
        draw.multiline_text((checklist_box[0] + 52, cy), bullet, font=checklist_font, fill=(235, 241, 250), spacing=5)
        _, bh = text_bbox(draw, bullet, checklist_font, spacing=5)
        cy += bh + 12


def render_cta_slide(draw: ImageDraw.ImageDraw, width: int, height: int, slide: dict) -> None:
    margin_x = int(width * 0.07)

    title_font = load_font(size=60, bold=True)
    subtitle_font = load_font(size=33, bold=False)
    action_font = load_font(size=29, bold=False)
    action_title_font = load_font(size=32, bold=True)

    title = wrap_to_width_limited(draw, slide["title"], title_font, int(width * 0.84), 2)
    subtitle = wrap_to_width_limited(draw, slide["subtitle"], subtitle_font, int(width * 0.84), 3)

    t_w, t_h = text_bbox(draw, title, title_font, spacing=8)
    s_w, s_h = text_bbox(draw, subtitle, subtitle_font, spacing=6)

    top_box = (margin_x, int(height * 0.11), width - margin_x, int(height * 0.11) + t_h + s_h + 104)
    draw_card(draw, top_box, fill=(8, 16, 35, 160))
    draw.multiline_text((top_box[0] + 24, top_box[1] + 24), title, font=title_font, fill=(245, 248, 255), spacing=8)
    draw.multiline_text((top_box[0] + 24, top_box[1] + 24 + t_h + 14), subtitle, font=subtitle_font, fill=(220, 233, 247), spacing=6)

    action_box = (margin_x, top_box[3] + 24, width - margin_x, int(height * 0.84))
    draw_card(draw, action_box, fill=(6, 12, 28, 148))
    draw.text((action_box[0] + 24, action_box[1] + 20), "Plano de acao em 3 passos", font=action_title_font, fill=(181, 222, 255))

    y = action_box[1] + 76
    max_width = action_box[2] - action_box[0] - 80
    for idx, action in enumerate(slide.get("actions", []), start=1):
        bullet = wrap_to_width_limited(draw, f"{idx}. {action}", action_font, max_width, 3)
        draw.multiline_text((action_box[0] + 24, y), bullet, font=action_font, fill=(235, 241, 250), spacing=5)
        _, bh = text_bbox(draw, bullet, action_font, spacing=5)
        y += bh + 14


def render_slide(
    output_file: Path,
    background: Image.Image,
    slide: dict,
    slide_index: int,
    slide_count: int,
    profile_name: str,
) -> None:
    width, height = background.size
    img = apply_dark_overlay(background, alpha=92)
    draw = ImageDraw.Draw(img)

    kind = slide.get("kind", "value")
    if kind == "cover":
        render_cover_slide(draw, width, height, slide)
    elif kind == "cta":
        render_cta_slide(draw, width, height, slide)
    else:
        render_value_slide(draw, width, height, slide)

    draw_common_chrome(draw, width, height, slide_index, slide_count, profile_name)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_file, format="JPEG", quality=93, optimize=True)


def build_caption(theme: str, profile_keywords: list[str], slide_count: int) -> str:
    profile_line = " | ".join(k.capitalize() for k in profile_keywords[:3])
    if not profile_line:
        profile_line = "Conteudo de valor"

    hashtags = [
        "carrossel",
        "instagram",
        "programacao",
        "tecnologia",
        "inteligenciaartificial",
        "desenvolvimento",
        "softwareengineering",
        "python",
        "dev",
        "machinelearning",
    ]
    hashtags.extend([re.sub(r"[^a-z0-9]", "", k.lower()) for k in profile_keywords[:8]])

    unique: list[str] = []
    seen: set[str] = set()
    for tag in hashtags:
        if not tag:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        unique.append(tag)

    hash_line = " ".join(f"#{tag}" for tag in unique[:24])

    return (
        f"{theme}: guia pratico em {slide_count} slides com aplicacao real.\n"
        f"{profile_line}\n"
        "\n"
        "Conteudo tecnico estruturado com conceito, exemplo pratico e checklist.\n"
        "Se te ajudou, salva e compartilha com outro dev.\n"
        f"\n{hash_line}"
    )


def generate_single_carousel(
    output_base_dir: Path,
    source_info_dir: Path,
    profile_keywords: list[str],
    profile_focus: str,
    fallback_theme: str,
    slide_count: int,
    width: int,
    height: int,
    profile_name: str,
    max_source_files: int,
    image_timeout: int,
) -> Path:
    source_texts = read_info_texts(source_info_dir, max_source_files)
    extracted = extract_keywords(source_texts, limit=20)
    snippets = extract_practical_snippets(source_texts, limit=20)
    theme = pick_theme(profile_keywords, extracted, fallback_theme=fallback_theme)

    slides = build_slide_copy(
        theme=theme,
        profile_focus=profile_focus,
        slide_count=slide_count,
        profile_keywords=profile_keywords,
        snippets=snippets,
    )

    package_dir = output_base_dir / f"carousel_{now_stamp()}"
    package_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "theme": theme,
        "profile_keywords": profile_keywords,
        "profile_focus": profile_focus,
        "source_info_dir": str(source_info_dir),
        "snippets_count": len(snippets),
        "slides": [],
    }

    for idx, slide in enumerate(slides):
        query = image_query_for_slide(profile_keywords, theme, slide, idx + 1)
        background = download_background_image(query, width, height, timeout_seconds=image_timeout)
        if background is None:
            background = make_gradient_background(width, height, seed_value=f"{theme}-{idx}")
            image_source = "gradient_fallback"
        else:
            image_source = "loremflickr"

        output_file = package_dir / f"slide_{idx + 1:02d}.jpg"
        render_slide(
            output_file=output_file,
            background=background,
            slide=slide,
            slide_index=idx,
            slide_count=len(slides),
            profile_name=profile_name,
        )

        slide_meta = {
            "index": idx + 1,
            "file_name": output_file.name,
            "query": query,
            "image_source": image_source,
        }
        slide_meta.update(slide)
        metadata["slides"].append(slide_meta)

    caption = build_caption(theme=theme, profile_keywords=profile_keywords, slide_count=len(slides))
    (package_dir / "caption.txt").write_text(caption, encoding="utf-8")
    (package_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    return package_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gerador automatico de carrossel para Instagram.")
    parser.add_argument("--project-dir", default=".", help="Diretorio raiz do projeto")
    parser.add_argument("--output-dir", default="outputs_ig/carousels", help="Pasta para salvar pacotes de carrossel")
    parser.add_argument("--source-info-dir", default="timeline_downloads", help="Pasta com arquivos *_info.txt")
    parser.add_argument("--profile-name", default="@seu_perfil", help="Texto de assinatura nos slides")
    parser.add_argument(
        "--profile-keywords",
        default="programacao,tecnologia,ia,python,automacao",
        help="Palavras-chave do perfil separadas por virgula",
    )
    parser.add_argument(
        "--profile-focus",
        default="ensinar programacao, tecnologia e IA com exemplos praticos",
        help="Objetivo central do perfil",
    )
    parser.add_argument("--fallback-theme", default="Engenharia", help="Tema usado se nao houver palavras-chave")
    parser.add_argument("--slides", type=int, default=7, help="Quantidade de slides (2 a 10)")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="Largura da imagem")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help="Altura da imagem")
    parser.add_argument("--max-source-files", type=int, default=20, help="Maximo de *_info.txt usados para contexto")
    parser.add_argument("--image-timeout", type=int, default=20, help="Timeout em segundos para baixar imagem de fundo")
    parser.add_argument("--count", type=int, default=1, help="Quantidade de carrosseis para gerar")
    return parser


def parse_profile_keywords(raw: str) -> list[str]:
    parts = [part.strip() for part in raw.split(",")]
    return [part for part in parts if part]


def main() -> int:
    args = build_parser().parse_args()

    project_dir = Path(args.project_dir).resolve()
    output_dir = abs_path(project_dir, args.output_dir)
    source_info_dir = abs_path(project_dir, args.source_info_dir)

    slides = max(2, min(10, args.slides))
    width = max(720, args.width)
    height = max(900, args.height)
    count = max(1, args.count)

    profile_keywords = parse_profile_keywords(args.profile_keywords)

    generated: list[Path] = []
    for _ in range(count):
        package = generate_single_carousel(
            output_base_dir=output_dir,
            source_info_dir=source_info_dir,
            profile_keywords=profile_keywords,
            profile_focus=args.profile_focus,
            fallback_theme=args.fallback_theme,
            slide_count=slides,
            width=width,
            height=height,
            profile_name=args.profile_name,
            max_source_files=max(1, args.max_source_files),
            image_timeout=max(5, args.image_timeout),
        )
        generated.append(package)

    print(f"Carrossel gerado: {len(generated)} pacote(s)")
    for package in generated:
        print(f" - {package}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
