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

# O tema "post" usa fonte normal em caixa mista: cabe muito mais texto que
# a condensada, e o titulo e uma frase, nao um rotulo.
MAX_POST = dict(MAX, title=62, body=260, item_t=18, item_d=90,
                card_h=20, card_p=130, check=44, cover_line=34, pill=60)


def _titulo_frase(texto: str) -> str:
    """Converte "/DIVERSIFICAR" em "Diversificar".

    O tema post nao usa barra nem caixa alta. Titulo que ja venha em frase
    passa intacto — so mexe no que veio no formato antigo.
    """
    t = (texto or "").strip().lstrip("/").strip()
    if not t:
        return t
    letras = [c for c in t if c.isalpha()]
    if letras and all(c.isupper() for c in letras):
        # Todo em caixa alta: vira frase, preservando siglas de 2-3 letras.
        palavras = []
        for p in t.split():
            palavras.append(p if (len(p) <= 3 and p.isalpha()) else p.capitalize())
        t = " ".join(palavras)
        t = t[0].upper() + t[1:] if t else t
    return t.replace("-", " ")


def _maybe_upper(texto: str, alta: bool) -> str:
    return texto.upper() if alta else texto


def _limites(template: str) -> dict:
    return MAX_POST if str(template).lower() == "post" else MAX


def _caixa_alta(template: str) -> bool:
    """Só o blueprint e derivados usam caixa alta nos rotulos."""
    return str(template).lower() != "post"


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


SCHEMA_POST = """
{
  "caption": "legenda do Instagram em PT-BR, 3 a 5 linhas curtas separadas por \\n\\n, com CTA no fim",
  "hashtags": ["#ia", "..."],
  "credito": "@perfil_de_origem",
  "slides": [
    {"type":"cover","lines":[{"text":"Primeira parte do gancho"},
                             {"text":"segunda parte","band":true},
                             {"text":"e o fecho"}],
     "pill":"frase curta de apoio"},
    {"type":"text","title":"O que mudou de fato","body":["...","..."]},
    {"type":"text","title":"Como o mecanismo funciona","body":["..."]},
    {"type":"text","title":"O numero que importa","body":["..."]},
    {"type":"list","title":"Onde isso pega de verdade","body":["..."],
     "items":[{"n":"01","t":"TITULO","d":"descricao"}]},
    {"type":"cards","title":"O custo que ninguem cita","body":["..."],
     "cards":[{"style":"dark","lbl":"HOJE","h":"TITULO","p":"..."},
              {"style":"out","lbl":"DEPOIS","h":"TITULO","p":"..."}]},
    {"type":"checks","title":"O que fazer com isso","body":["..."],
     "checksLabel":"NA PRATICA","checks":["...","...","...","..."]},
    {"type":"text","title":"A prova tecnica","body":["..."]},
    {"type":"text","title":"Resumindo","body":["...","..."]},
    {"type":"text","title":"Salva e segue","body":["..."]}
  ]
}
"""


def _rules(status: str, template: str = "blueprint") -> str:
    # O tema "post" nao usa a condensada em caixa alta: titulo com "/" e
    # capa de tres linhas soltas ficam sem sentido num cartao de rede social.
    formato_post = """

FORMATO DESTE CARROSSEL: cartao de rede social, com 10 SLIDES e papel fixo
para cada um. Respeite a ordem — ela e a narrativa:

  1  GANCHO. Uma frase de efeito persuasiva que para o scroll. Tensao, numero
     que surpreende ou crenca contrariada. Sem explicar ainda. (leva imagem)
  2  O QUE E. Apresenta o fato principal em duas ou tres frases. (leva imagem)
  3  COMO FUNCIONA. O mecanismo, com dado concreto. (leva imagem)
  4  O NUMERO. O dado mais forte do material, com o que ele significa. (leva imagem)
  5  APROFUNDAMENTO. So texto, sem imagem. Uma camada que o leitor nao viu.
  6  A RESSALVA. So texto. O limite, o custo, o que pode dar errado.
  7  O QUE FAZER. So texto. Aplicacao pratica para quem le.
  8  A PROVA. O detalhe tecnico que sustenta tudo. (leva imagem)
  9  RESUMO. Amarra o carrossel inteiro em 3 ou 4 linhas curtas. Sem imagem.
 10  CTA. So a chamada: salvar e seguir. Curto, duas linhas no maximo.
     Sem imagem, sem dado novo.

Slides 5, 6, 7, 9 e 10 NAO devem ter imagem — escreva um pouco mais neles,
porque a pagina fica so com texto.
- title: uma FRASE curta em caixa normal, ate 60 caracteres, SEM barra "/"
  no inicio e SEM caixa alta. Deve continuar a leitura, nao rotular a secao.
  Bom:  "O modelo escreve o jogo inteiro sozinho"
  Ruim: "/OS-PASSOS"
- cover.lines: as 3 linhas sao lidas como UMA frase corrida, entao escreva
  uma afirmacao completa quebrada em 3 partes, com sujeito e verbo.
  Bom:  ["Um comando gerou", "um jogo inteiro", "em trinta horas"]
  Ruim: ["UM UNICO", "SIMULA MOTORES", "COM VALIDACAO"]
- Sem chip e sem "FIG.N": esse tema nao mostra esses elementos.
"""

    base = f"""
Voce escreve carrosseis de Instagram em PORTUGUES DO BRASIL, tom direto e tecnico.
Proibido: "revolucionario", "game changer", "chocante", emoji no corpo dos slides.

VOZ: voce escreve MATERIA, nao resenha de video.

O post que voce recebe e material de apuracao — a sua fonte —, nao o assunto.
O assunto e o FATO em si: a ferramenta, o lancamento, a tecnica, o numero.
Escreva como quem apura e reporta, nao como quem assistiu e conta o que viu.

NUNCA escreva, em nenhuma variacao: "o video mostra", "nesse post", "ele explica",
"o autor diz", "segundo o tweet", "na demonstracao", "ele mostrou como",
"segundo o relato", "conforme relatado", "de acordo com a publicacao", "o material",
"a fonte afirma", "o conteudo apresenta", "foi demonstrado".

Nao basta trocar "o video mostra" por "segundo o relato" — e o mesmo vicio com
outra roupa. O teste e simples: se a frase precisa apontar para ALGUEM QUE CONTOU,
ela esta errada. Afirme o fato direto, no presente, sem intermediario.

Errado:  "Segundo o relato tecnico, o mercado operava com margens altas."
Certo:   "O mercado operava com margens altas sobre modelos fechados."

Se a frase so faz sentido porque existe um post ou video atras, reescreva.

Errado:  "O video mostra como usar o framework para automatizar tarefas."
Certo:   "O framework automatiza a fila de tarefas sem intervencao humana."

Errado:  "Ele demonstra que da para rodar tudo com 9 etapas."
Certo:   "Sao 9 etapas entre a intencao e o codigo em producao."

O leitor nao sabe que existe um post de origem e nao precisa saber. Ele quer o
fato e o que fazer com ele. Cite a fonte apenas se o nome importar para a
credibilidade do dado (a empresa que lancou, o repositorio, o estudo) — e nesse
caso cite a ENTIDADE, nao o autor do post.

Terceira pessoa, presente. Sujeito da frase e a coisa, nao a pessoa que falou dela.

OBJETIVO: parar o scroll e fazer a pessoa arrastar ate o fim. Cada slide precisa
criar motivo para ver o proximo. Texto que so informa e texto que perde o leitor.

O GANCHO DA CAPA e o elemento mais importante do carrossel. As tres linhas devem,
juntas, formar uma afirmacao que gera tensao: um numero que surpreende, uma perda
que a pessoa nao sabia que estava tendo, uma crenca comum sendo contrariada, ou uma
promessa concreta. A linha do meio (band:true) carrega o impacto.
Bom:  "SEU AGENTE" / "VAZA TOKEN" / "E VOCE NAO VE"
Ruim: "NOVO FRAMEWORK" / "DE IA" / "LANCADO"

TITULOS dos slides internos tambem sao ganchos, nao rotulos. Prometem o que vem a
seguir em vez de nomear a secao.
Bom:  /O-ERRO-CARO   /NINGUEM-VE   /O-QUE-TRAVA
Ruim: /O-CONTEXTO    /OS-PASSOS    /INFORMACOES

PERSUASAO no corpo, sem inventar fato:
- Numero concreto vence adjetivo. "42 mil estrelas" e melhor que "muito popular".
- Fale da consequencia para quem le, nao so do que aconteceu. Ligue o fato ao
  custo, ao risco ou ao ganho de quem esta lendo.
- Uma ideia por slide. Duas ideias competindo enfraquecem as duas.
- Especifico vence generico: nome do repo, versao, valor, tempo economizado.
- Nada de hype vazio, superlativo sem numero, nem promessa que o conteudo original
  nao sustenta. Se o material nao tem o dado, nao invente: use o que tem.

A LEGENDA abre repetindo o gancho com outras palavras, entrega o valor em 2 ou 3
linhas e fecha com UMA chamada clara (salvar, comentar ou seguir) - nunca as tres.

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
NINGUEM CONFIRMOU o que o material afirma. Escreva de forma atribuida SEM apontar
para o post ou o video: use "a proposta e", "o projeto promete", "segundo a
documentacao", "o anuncio diz". NAO escreva que uma empresa lancou, anunciou ou
confirmou nada. A capa nao pode afirmar que existe. Mantenha a regra de voz: nada
de "o video mostra" nem "segundo o post" — atribua a ENTIDADE, nao a quem contou.
"""
    if str(template).lower() == "post":
        base += formato_post

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


def _sanitize(deck: dict, template: str = "blueprint") -> dict:
    M = _limites(template)
    alta = _caixa_alta(template)
    """Corta o que estourou. O prompt e a primeira barreira; isto e a garantia."""
    for slide in deck.get("slides", []):
        if slide.get("type") == "cover":
            slide["lines"] = [
                {"text": _maybe_upper(_clip(l.get("text", ""), M["cover_line"], word_safe=True), alta),
                 **({"band": True} if l.get("band") else {})}
                for l in (slide.get("lines") or [])[:3]
            ]
            slide["pill"] = _clip(slide.get("pill", ""), M["pill"])
            continue
        titulo = _clip(slide.get("title", ""), M["title"], word_safe=True)
        slide["title"] = (_maybe_upper(titulo, alta) if alta
                          else _titulo_frase(titulo))
        slide["chip"] = _maybe_upper(_clip(slide.get("chip", ""), M["chip"]), alta)
        slide["body"] = [_capitalize(_clip(b, M["body"]))
                         for b in (slide.get("body") or [])[:2]]
        for item in slide.get("items", [])[:4]:
            item["t"] = _maybe_upper(_clip(item.get("t", ""), M["item_t"], word_safe=True), alta)
            item["d"] = _clip(item.get("d", ""), M["item_d"])
        for card in slide.get("cards", [])[:2]:
            card["h"] = _maybe_upper(_clip(card.get("h", ""), M["card_h"], word_safe=True), alta)
            card["p"] = _clip(card.get("p", ""), M["card_p"])
        if slide.get("checks"):
            slide["checks"] = [_clip(c, M["check"]) for c in slide["checks"][:4]]
    return deck


def _finish(deck: dict, source: dict) -> dict:
    slides = deck.get("slides", [])

    # Credito de quem publicou o material original. Sai do source, nao do
    # modelo: assim nunca vem inventado.
    autor = (source.get("author") or "").lstrip("@")
    if autor:
        deck["credito"] = f"@{autor}"
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
    slides = deck.get("slides", [])

    if str(deck.get("template", "")).lower() == "post":
        # Estrutura fixa: capa e slides 2, 3, 4 e 8 levam imagem; os demais
        # sao de texto puro de proposito, para o carrossel ter respiro.
        posicoes = [0, 1, 2, 3, 7]
        alvos = [slides[i] for i in posicoes if i < len(slides)]
        for indice, (slide, image) in enumerate(zip(alvos, images)):
            chave = "artImage" if slide.get("type") == "cover" else "image"
            slide[chave] = str(image)
            if indice < len(captions) and captions[indice]:
                slide["imageCaption"] = captions[indice]
        return deck

    targets = [s for s in slides if s.get("type") == "text"]
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


def build_deck(source: dict, status: str = "conceito", screens: str = "",
               template: str = "blueprint") -> dict:
    """source: {text, author, url, views, likes}. status: confirmado|conceito|duvidoso."""
    import llm_client

    prompt = (
        f"MATERIAL DE APURACAO (sua fonte, nao o assunto da materia):\n"
        f"relato de @{(source.get('author') or '').lstrip('@')}: "
        f"{source.get('text', '')}\n"
        f"(alcance da fonte: {source.get('views', '?')} views, "
        f"{source.get('likes', '?')} likes — sinal de relevancia, nao materia)\n\n"
    )
    if screens:
        prompt += (
            "EVIDENCIAS COLETADAS (fatos apurados: numeros, precos, prazos, nomes). "
            "Use como dado da materia — nunca descreva a tela nem diga onde apareceu:\n"
            f"{screens}\n\n"
        )
    esquema = SCHEMA_POST if str(template).lower() == "post" else SCHEMA_HINT
    prompt += f"Formato exato de saida:\n{esquema}"

    deck = llm_client.chat_json(prompt, system=_rules(status, template))
    return _finish(_sanitize(deck, template), source)
