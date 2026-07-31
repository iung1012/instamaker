"""Monta o deck do carrossel (9 slides) a partir do conteudo de um post.

O texto sai em portugues. As imagens sao frames do proprio video do post, escolhidos
em `frames.py` -- nao geramos ilustracao: mascote gerado por IA ficou ruim e sai caro.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

MAX = {  # limites que o layout aguenta sem vazar
    "title": 13,
    "chip": 14,
    "body": 190,
    "item_t": 10,
    "item_d": 70,
    "card_h": 12,
    "card_p": 110,
    "check": 28,
    "pill": 45,
    "cover_line": 14,
}

SCHEMA_HINT = """
{
  "caption": "legenda do Instagram em PT-BR, 3 a 5 linhas curtas separadas por \\n\\n, com CTA no fim",
  "hashtags": ["#ia", "..."],
  "slides": [
    {"type":"cover","lines":[{"text":"LINHA UM"},{"text":"DESTAQUE","band":true},{"text":"LINHA TRES"}],
     "pill":"frase de gancho"},
    {"type":"text","chip":"O CONTEXTO","title":"/O-QUE-MUDOU","fig":"FIG.1 - descricao","body":["..."]},
    {"type":"list","chip":"POR DENTRO","title":"/OS-PASSOS","fig":"FIG.2 - ...","body":["..."],
     "items":[{"n":"01","t":"TITULO","d":"descricao"}]},
    {"type":"cards","chip":"A MUDANCA","title":"/ANTES-DEPOIS","fig":"FIG.3 - ...","body":["..."],
     "cards":[{"style":"dark","lbl":"ANTES","h":"TITULO","p":"..."},
              {"style":"out","lbl":"DEPOIS","h":"TITULO","p":"..."}]},
    {"type":"checks","chip":"LEITURA","title":"/POR-QUE","fig":"FIG.4 - ...","body":["..."],
     "checksLabel":"O QUE OBSERVAR","checks":["...","...","...","..."]}
  ]
}
"""


def _rules(status: str) -> str:
    base = f"""
Voce escreve carrosseis de Instagram em PORTUGUES DO BRASIL, tom direto e tecnico.
Proibido: "revolucionario", "game changer", "chocante", emoji no corpo dos slides.

Regras de tamanho (o layout QUEBRA se estourar):
- title: SEMPRE em CAIXA ALTA comecando com "/", no maximo {MAX['title']} caracteres.
- chip: no maximo {MAX['chip']} caracteres, caixa alta.
- body: 1 ou 2 paragrafos, cada um com no maximo {MAX['body']} caracteres.
  Use <em>texto</em> para destacar em laranja (pelo menos um por slide) e <strong> para negrito.
- fig: sempre "FIG.N - descricao em minusculas".
- items: exatamente 4, t <= {MAX['item_t']} chars, d <= {MAX['item_d']} chars.
- cards: h <= {MAX['card_h']} chars, p <= {MAX['card_p']} chars.
- checks: exatamente 4, cada um <= {MAX['check']} chars.
- capa: exatamente 3 linhas, <= {MAX['cover_line']} chars cada, exatamente 1 com "band": true.
- pill: <= {MAX['pill']} caracteres.

Sao 9 slides no total: 1 capa + 7 de conteudo + 1 final de "me segue".
Varie os tipos entre text, list, cards e checks. Devolva SOMENTE JSON valido.

REGRA DE CONTEUDO: cada slide precisa de um fato concreto -- numero, preco, prazo,
nome de botao, comparacao. Frases como "os criterios visam garantir integridade" ou
"os indices refletem eficiencia" sao LIXO: nao dizem nada e servem para qualquer
assunto. Se faltar informacao para 9 slides, aprofunde mais em vez de encher.
"""
    if status == "conceito":
        base += """
IMPORTANTE: o que o post anuncia NAO EXISTE -- e mockup/conceito de terceiro.
O carrossel NAO pode afirmar que foi lancado. A capa deve deixar claro que e hipotese
(ex: "E SE...?"). Um dos slides DEVE ser type "cards" contrastando
"O QUE E: conceito" x "O QUE NAO E: produto".
"""
    elif status == "confirmado":
        base += "\nO fato foi confirmado em fonte primaria. Pode escrever como fato.\n"
    else:
        # Padrao seguro: ninguem checou o post, entao nao afirmamos nada em nome
        # da empresa citada. Post viral com mockup convincente e comum, e publicar
        # "fulano lancou X" sem confirmacao vira desinformacao no perfil do usuario.
        base += """
NINGUEM CONFIRMOU o que o post afirma. Escreva de forma atribuida, nunca como fato
proprio: use "segundo o post", "o video mostra", "a proposta e". NAO escreva que uma
empresa lancou, anunciou ou confirmou nada. A capa nao pode afirmar que existe.
"""
    return base


def _capitalize(text: str) -> str:
    """Sobe a primeira letra do paragrafo, pulando tags HTML.

    O modelo entrega "o post acumula 45 mil..." e "segundo o post, ...". Nao da
    para usar .capitalize(): isso rebaixaria o resto ("VisionPsy" viraria
    "Visionpsy") e falharia quando o paragrafo comeca com <em> ou <strong>.
    """
    index = 0
    while index < len(text):
        char = text[index]
        if char == "<":
            fim = text.find(">", index)
            if fim == -1:
                break
            index = fim + 1  # salta a tag inteira; so `continue` capitalizava o "<s"
            continue
        if char.isalpha():
            return text[:index] + char.upper() + text[index + 1:]
        if not char.isspace():
            break
        index += 1
    return text


def _clip(value: str, limit: int, word_safe: bool = False) -> str:
    """Corta no limite. Em titulo corta na palavra: '/MAX-TOKENS-CAD' e pior que '/MAX-TOKENS'."""
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    if word_safe:
        head = value[:limit]
        cut = max(head.rfind("-"), head.rfind(" "), head.rfind("/"))
        if cut > 2:
            return head[:cut].rstrip("-/ ")
        return head.rstrip("-/ ")
    return value[: limit - 1].rstrip() + "…"


def _sanitize(deck: dict) -> dict:
    """Corta o que estourou. O prompt e a primeira barreira; isto e a garantia."""
    for slide in deck.get("slides", []):
        if slide.get("type") == "cover":
            slide["lines"] = [
                {"text": _clip(l.get("text", ""), MAX["cover_line"], word_safe=True).upper(),
                 **({"band": True} if l.get("band") else {})}
                for l in (slide.get("lines") or [])[:3]
            ]
            slide["pill"] = _clip(slide.get("pill", ""), MAX["pill"])
            continue
        slide["title"] = _clip(slide.get("title", ""), MAX["title"], word_safe=True).upper()
        slide["chip"] = _clip(slide.get("chip", ""), MAX["chip"]).upper()
        slide["body"] = [_capitalize(_clip(b, MAX["body"]))
                         for b in (slide.get("body") or [])[:2]]
        for item in slide.get("items", [])[:4]:
            item["t"] = _clip(item.get("t", ""), MAX["item_t"], word_safe=True).upper()
            item["d"] = _clip(item.get("d", ""), MAX["item_d"])
        for card in slide.get("cards", [])[:2]:
            card["h"] = _clip(card.get("h", ""), MAX["card_h"], word_safe=True).upper()
            card["p"] = _clip(card.get("p", ""), MAX["card_p"])
        if slide.get("checks"):
            slide["checks"] = [_clip(c, MAX["check"]) for c in slide["checks"][:4]]
    return deck


def _finish(deck: dict, source: dict) -> dict:
    slides = deck.get("slides", [])
    total = len(slides)
    for index, slide in enumerate(slides, start=1):
        if slide.get("type") != "cover":
            slide["sheet"] = f"SHEET {index:02d} / {total:02d}"

    if slides and slides[0].get("type") == "cover":
        cover = slides[0]
        cover.setdefault("specTop", ["GRID SYSTEM 2.0", "UNIT: PX · SNAP: ON"])
        cover.setdefault("specTopRight", [
            "VERSION 5.0",
            f"DATE: {datetime.now():%d.%m.%Y}",
            f"BY: {(os.getenv('CAROUSEL_HANDLE') or 'INSTAMAKER').lstrip('@').upper()}",
        ])
        author = (source.get("author") or "").lstrip("@")
        cover.setdefault("specBot", [x for x in [
            f"FONTE: @{author.upper()}" if author else None,
            f"VIEWS: {source['views']}" if source.get("views") else None,
        ] if x])
        cover.setdefault("specBotRight", ["LAYOUT NOTES", "1. BIG MESSAGE",
                                          "2. STRONG HIERARCHY", "3. MAX IMPACT"])
    return deck


def attach_images(deck: dict, images: list[str], captions: list[str] | None = None) -> dict:
    """Distribui os frames do video pelos slides que sobram espaco.

    So slides `text` recebem imagem. Em list/cards/checks a imagem soma com o bloco,
    estoura os 1350px e o final e cortado -- checklist de 4 itens aparecendo com 1,
    lista de 4 passos aparecendo com 2. Perder conteudo e pior que ter menos imagem.
    """
    captions = captions or []
    targets = [s for s in deck.get("slides", []) if s.get("type") == "text"]
    for slide, image in zip(targets, images):
        slide["image"] = str(image)
        index = images.index(image)
        if index < len(captions) and captions[index]:
            slide["imageCaption"] = captions[index]
    return deck


VISION_PROMPT = (
    "Estas sao telas de um video de demonstracao, em ordem. Descreva de forma "
    "objetiva o que aparece em cada uma: textos visiveis, numeros, precos, prazos, "
    "nomes de botoes e o que a interface esta fazendo. Nao interprete nem opine, "
    "so relate o que da para ler na tela. Uma linha por tela."
)


def describe_frames(images: list) -> str:
    """Le o que esta escrito nas telas do video.

    Sem isso o redator so recebe o texto do post -- que em post de demo costuma ser
    uma unica frase de efeito -- e preenche 9 slides inventando generalidades. Os
    fatos que interessam (precos, prazos, numeros) estao na interface, nao no texto.
    """
    if not images:
        return ""
    import llm_client

    try:
        return llm_client.describe_images(images, VISION_PROMPT).strip()
    except Exception:  # noqa: BLE001 - sem visao o carrossel ainda sai, so que pior
        return ""


def build_deck(source: dict, status: str = "conceito", screens: str = "") -> dict:
    """source: {text, author, url, views, likes}. status: confirmado|conceito|duvidoso."""
    import llm_client

    prompt = (
        f"Post de origem:\n"
        f"autor: @{(source.get('author') or '').lstrip('@')}\n"
        f"texto: {source.get('text', '')}\n"
        f"metricas: {source.get('views', '?')} views, {source.get('likes', '?')} likes\n\n"
    )
    if screens:
        prompt += (
            "O QUE APARECE NAS TELAS DO VIDEO — use estes fatos concretos (numeros, "
            f"precos, prazos, nomes de botao) em vez de generalidades:\n{screens}\n\n"
        )
    prompt += f"Formato exato de saida:\n{SCHEMA_HINT}"

    deck = llm_client.chat_json(prompt, system=_rules(status))
    return _finish(_sanitize(deck), source)
