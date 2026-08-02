"""carousel/profile.py — identidade do perfil para o tema de cartao de post.

Nome, @ e foto vem do Instagram, mas so uma vez por dia: a foto e a mesma
por semanas e cada chamada e uma requisicao autenticada a mais na conta —
quanto menos, menor o risco de a sessao ser marcada.

A foto vai embutida em base64 porque o template e carregado com file:// e
a URL do CDN do Instagram expira; um src remoto quebraria no render.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CACHE = BASE / ".profile_cache.json"
VALIDADE = 24 * 3600  # segundos

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122 Safari/537.36")


def _avatar_data_uri(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        bruto = r.read()
    tipo = "image/jpeg"
    if bruto[:8] == b"\x89PNG\r\n\x1a\n":
        tipo = "image/png"
    elif bruto[:4] == b"RIFF" and bruto[8:12] == b"WEBP":
        tipo = "image/webp"
    return f"data:{tipo};base64,{base64.b64encode(bruto).decode()}"


def carregar_perfil(force: bool = False) -> dict:
    """{name, handle, verified, avatar}. Devolve o cache se ainda valido.

    Falhar aqui nao pode derrubar o carrossel: sem perfil o tema cai para
    o handle do .env e um avatar vazio.
    """
    if not force and CACHE.exists():
        try:
            dados = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - dados.get("_ts", 0) < VALIDADE and dados.get("avatar"):
                return dados
        except Exception:
            pass

    perfil = {"name": "", "handle": "", "verified": False, "avatar": "", "_ts": time.time()}
    try:
        from instagrapi import Client
        sessao = BASE / ".ig_session.json"
        c = Client()
        c.load_settings(sessao)
        info = c.account_info()
        d = info.model_dump() if hasattr(info, "model_dump") else dict(info)

        perfil["handle"] = str(d.get("username") or "")
        perfil["name"] = str(d.get("full_name") or perfil["handle"])
        perfil["verified"] = bool(d.get("is_verified"))
        url = d.get("profile_pic_url_hd") or d.get("profile_pic_url")
        if url:
            perfil["avatar"] = _avatar_data_uri(str(url))
    except Exception as exc:  # noqa: BLE001
        print(f"[perfil] nao consegui ler o perfil ({exc}); usando fallback")

    if not perfil["handle"]:
        import os
        perfil["handle"] = (os.getenv("CAROUSEL_HANDLE", "") or "").lstrip("@")
        perfil["name"] = perfil["name"] or perfil["handle"]

    try:
        CACHE.write_text(json.dumps(perfil, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return perfil
