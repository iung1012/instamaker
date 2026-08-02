#!/usr/bin/env python3
"""watcher.py — vigia perfis do X e monta carrossel do que for relevante.

O yt-dlp le um post individual, mas NAO lista a timeline de um perfil (nao
existe extractor twitter:user). A descoberta sai do RSS do nitter.net, que
devolve os posts recentes com data, texto e link.

Uso:
    ./venv/bin/python watcher.py --once      # roda um ciclo agora
    ./venv/bin/python watcher.py --list      # mostra a config
    ./venv/bin/python watcher.py --add @x    # cadastra perfil
    ./venv/bin/python watcher.py --rm @x     # remove perfil
    ./venv/bin/python watcher.py --on|--off  # liga/desliga a busca
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent

# O llm_client le as chaves de os.environ. Quem roda o bot chama load_dotenv no
# start; aqui e um processo proprio, entao precisa carregar o .env por conta.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
except ImportError:
    for _linha in (BASE / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in _linha and not _linha.lstrip().startswith("#"):
            _k, _, _v = _linha.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

CONFIG = BASE / "watch.json"
STATE = BASE / ".watch_state.json"
NITTER = os.getenv("NITTER_BASE", "https://nitter.net")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"

PADRAO = {
    "enabled": False,          # comeca desligado de proposito
    "profiles": [],
    "max_age_hours": 24,
    "skip_retweets": True,
    "skip_replies": True,
    "theme": "blueprint",          # compatibilidade: tema unico antigo
    "themes": ["blueprint"],       # sorteia entre estes a cada post
    "topic": "IA, ferramentas e novidades para quem constroi com IA",
    "publish": False,          # publicar sozinho e opt-in explicito
    "max_per_run": 1,
}


def carregar() -> dict:
    cfg = dict(PADRAO)
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    return cfg


def salvar(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def vistos() -> set:
    if STATE.exists():
        return set(json.loads(STATE.read_text(encoding="utf-8")).get("seen", []))
    return set()


def marcar(ids: set) -> None:
    atual = vistos() | ids
    # Mantem os 500 mais recentes: o suficiente para nao repetir, sem crescer sem fim.
    STATE.write_text(json.dumps({"seen": sorted(atual)[-500:]}, ensure_ascii=False),
                     encoding="utf-8")


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- descoberta
def buscar_perfil(handle: str, cfg: dict) -> list[dict]:
    """Posts recentes de um perfil, via RSS do nitter."""
    user = handle.lstrip("@")
    url = f"{NITTER}/{user}/rss"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            xml = r.read().decode("utf-8", "replace")
    except Exception as exc:
        log(f"  {handle}: RSS falhou ({exc})")
        return []

    limite = datetime.now(timezone.utc) - timedelta(hours=cfg["max_age_hours"])
    achados = []
    for bloco in re.findall(r"<item>(.*?)</item>", xml, re.S):
        titulo = html.unescape(_tag(bloco, "title"))
        link = _tag(bloco, "link")
        data = _tag(bloco, "pubDate")

        if cfg["skip_retweets"] and titulo.startswith("RT by"):
            continue
        if cfg["skip_replies"] and titulo.startswith("R to"):
            continue
        try:
            quando = parsedate_to_datetime(data)
            if quando.tzinfo is None:
                quando = quando.replace(tzinfo=timezone.utc)
            if quando < limite:
                continue
        except Exception:
            continue

        m = re.search(r"/status/(\d+)", link)
        if not m:
            continue
        achados.append({
            "id": m.group(1),
            "url": f"https://x.com/{user}/status/{m.group(1)}",
            "autor": f"@{user}",
            "texto": titulo,
            "quando": quando.isoformat(),
        })
    return achados


def _tag(bloco: str, nome: str) -> str:
    m = re.search(rf"<{nome}>(.*?)</{nome}>", bloco, re.S)
    return (m.group(1) if m else "").strip()


# ---------------------------------------------------------------- selecao
def escolher(candidatos: list[dict], quantos: int, cfg: dict | None = None) -> list[dict]:
    """Pede ao modelo para ranquear por relevancia em IA. Se o LLM falhar,
    cai para ordem cronologica — melhor publicar o mais novo do que travar."""
    if len(candidatos) <= quantos:
        return candidatos
    sys.path.insert(0, str(BASE))
    try:
        import llm_client
        lista = "\n".join(f"{i}. {c['texto'][:220]}" for i, c in enumerate(candidatos))
        assunto = (cfg or {}).get("topic") or PADRAO["topic"]
        prompt = (
            f"Voce seleciona pauta sobre: {assunto}\n\n"
            f"Candidatos:\n{lista}\n\n"
            f"Escolha os {quantos} mais relevantes para esse assunto. "
            "Priorize: fato concreto, ferramenta utilizavel, numero ou "
            "benchmark, mudanca que afeta o trabalho de quem le. "
            "Descarte: opiniao solta, thread motivacional, promocao de curso. "
            "Se NENHUM servir ao assunto, devolva lista vazia — melhor nao "
            "publicar do que publicar fora de pauta.\n\n"
            'Responda so JSON: {"escolhidos": [indices]}'
        )
        r = llm_client.chat_json(prompt)
        idx = [int(i) for i in r.get("escolhidos", [])][:quantos]
        sel = [candidatos[i] for i in idx if 0 <= i < len(candidatos)]
        if sel:
            return sel
    except Exception as exc:
        log(f"  ranqueamento falhou ({exc}); usando ordem cronologica")
    return sorted(candidatos, key=lambda c: c["quando"], reverse=True)[:quantos]


# ---------------------------------------------------------------- producao
def sortear_tema(cfg: dict) -> str:
    """Um dos temas marcados. Com varios, o feed nao fica monotono."""
    import random
    lista = [t for t in (cfg.get("themes") or []) if t]
    return random.choice(lista) if lista else cfg.get("theme", "blueprint")


def produzir(item: dict, cfg: dict) -> bool:
    """Monta o carrossel e, se configurado, publica."""
    sys.path.insert(0, str(BASE))
    from carousel import (attach_images, build_deck, describe_frames,
                          extract_frames, render_deck)
    from telegram_bot import fetch_post_source, probe_video  # reaproveita o que ja existe

    work = BASE / "outputs" / f"watch_{item['id']}"
    work.mkdir(parents=True, exist_ok=True)
    py = str(BASE / "venv" / "bin" / "python")

    log(f"  montando: {item['texto'][:70]}")
    source = fetch_post_source(item["url"], BASE, py)
    if not (source.get("text") or "").strip():
        log("  sem texto legivel — pulando")
        return False

    imagens = []
    try:
        video = Path(source["video"]) if source.get("video") else None
        if video and video.exists():
            imagens = [str(p) for p in extract_frames(video, work / "img", count=4)]
    except Exception as exc:
        log(f"  sem frames ({exc})")

    telas = describe_frames(imagens) if imagens else ""
    # Sorteia UMA vez: o prompt e o render precisam do mesmo tema.
    tema = sortear_tema(cfg)
    deck = build_deck(source, status=os.getenv("CAROUSEL_STATUS", "nao_verificado"),
                      screens=telas, template=tema)
    deck["template"] = tema
    if imagens:
        attach_images(deck, imagens)

    saida = work / "out"
    slides = render_deck(deck, saida)
    legenda = "\n\n".join([deck.get("caption", "").strip(),
                           " ".join(deck.get("hashtags", []))]).strip()
    (saida / "legenda.txt").write_text(legenda, encoding="utf-8")
    log(f"  {len(slides)} slides em {saida}")

    if not cfg.get("publish"):
        log("  publish desligado — carrossel ficou salvo, nada foi ao ar")
        return True

    cmd = [py, "instagrapi_publisher.py", "--publish-album",
           *[str(p) for p in slides], "--caption", legenda]
    r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, timeout=600)
    if r.returncode == 0:
        log(f"  PUBLICADO: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'ok'}")
        return True
    log(f"  publicacao falhou: {(r.stderr or r.stdout)[:300]}")
    return False


# ---------------------------------------------------------------- ciclo
def ciclo() -> int:
    cfg = carregar()
    if not cfg["enabled"]:
        log("busca desligada (--on para ligar)")
        return 0
    if not cfg["profiles"]:
        log("nenhum perfil cadastrado (--add @perfil)")
        return 0

    ja = vistos()
    candidatos = []
    for p in cfg["profiles"]:
        achados = buscar_perfil(p, cfg)
        novos = [a for a in achados if a["id"] not in ja]
        log(f"{p}: {len(achados)} recentes, {len(novos)} novos")
        candidatos.extend(novos)

    if not candidatos:
        log("nada novo")
        return 0

    escolhidos = escolher(candidatos, cfg["max_per_run"], cfg)
    log(f"{len(candidatos)} candidatos -> {len(escolhidos)} escolhido(s)")

    # Marca TODOS os vistos, nao so os escolhidos: os descartados nao devem
    # voltar a competir no proximo ciclo.
    marcar({c["id"] for c in candidatos})

    for item in escolhidos:
        try:
            produzir(item, cfg)
        except Exception as exc:
            log(f"  falhou: {exc}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Vigia perfis do X e monta carrosseis.")
    ap.add_argument("--once", action="store_true", help="roda um ciclo agora")
    ap.add_argument("--list", action="store_true", help="mostra a config")
    ap.add_argument("--add", metavar="PERFIL", help="cadastra um perfil")
    ap.add_argument("--rm", metavar="PERFIL", help="remove um perfil")
    ap.add_argument("--on", action="store_true", help="liga a busca")
    ap.add_argument("--off", action="store_true", help="desliga a busca")
    ap.add_argument("--publish-on", action="store_true", help="liga a publicacao automatica")
    ap.add_argument("--publish-off", action="store_true", help="desliga a publicacao automatica")
    ap.add_argument("--theme", metavar="TEMA",
                help="tema: blueprint|dark|minimal|editorial|post")
    a = ap.parse_args()

    cfg = carregar()
    mudou = False
    if a.add:
        h = "@" + a.add.lstrip("@")
        if h not in cfg["profiles"]:
            cfg["profiles"].append(h); mudou = True
    if a.rm:
        h = "@" + a.rm.lstrip("@")
        if h in cfg["profiles"]:
            cfg["profiles"].remove(h); mudou = True
    if a.on:
        cfg["enabled"] = True; mudou = True
    if a.off:
        cfg["enabled"] = False; mudou = True
    if a.publish_on:
        cfg["publish"] = True; mudou = True
    if a.publish_off:
        cfg["publish"] = False; mudou = True
    if a.theme:
        cfg["theme"] = a.theme; mudou = True
    if mudou:
        salvar(cfg)

    if a.list or mudou:
        print(f"busca:      {'LIGADA' if cfg['enabled'] else 'desligada'}")
        print(f"publicacao: {'AUTOMATICA' if cfg['publish'] else 'desligada (so gera)'}")
        print(f"tema:       {cfg['theme']}")
        print(f"janela:     ultimas {cfg['max_age_hours']}h, ate {cfg['max_per_run']} por ciclo")
        print(f"perfis:     {', '.join(cfg['profiles']) or '(nenhum)'}")
        return 0

    if a.once:
        return ciclo()

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
