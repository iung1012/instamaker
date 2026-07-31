"""Cliente unico de LLM, compativel com a API da OpenAI.

Hoje aponta para a Standard Compute (`https://api.stdcmpt.com/v1`, modelo
`StandardCompute`). Como o contrato e o da OpenAI, trocar de provedor e so mexer
no .env -- nenhum arquivo do projeto precisa saber quem esta atendendo.

Usa urllib de proposito: o projeto ja depende dele e assim nao entra SDK novo.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.stdcmpt.com/v1"
DEFAULT_MODEL = "StandardCompute"


class LLMError(RuntimeError):
    pass


def is_available() -> bool:
    # So LLM_API_KEY conta. Aceitar GEMINI_API_KEY aqui fazia o bot oferecer o
    # botao de IA numa maquina sem LLM_API_KEY configurada, e a chamada quebrava
    # depois com "Falha na IA" -- exatamente o caso da vps apos a migracao.
    return bool((os.getenv("LLM_API_KEY") or "").strip())


def _config() -> tuple[str, str, str]:
    key = (os.getenv("LLM_API_KEY") or "").strip()
    if not key:
        raise LLMError(
            "LLM_API_KEY nao encontrada no .env. O projeto migrou do Gemini para uma "
            "API compativel com OpenAI: preencha LLM_BASE_URL, LLM_API_KEY e LLM_MODEL."
        )
    base = (os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    model = (os.getenv("LLM_MODEL") or DEFAULT_MODEL).strip()
    return key, base, model


def chat(prompt: str, system: str | None = None, temperature: float = 0.8,
         timeout: int = 180) -> str:
    """Manda um prompt e devolve o texto da resposta."""
    key, base, model = _config()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Sem User-Agent proprio o Cloudflare da 403 "error code: 1010" no
            # "Python-urllib/3.x" padrao.
            "User-Agent": "instamaker/1.0",
            "Accept": "application/json",
        },
    )
    # O proxy Cloudflare corta em 120s (erro 524) e este modelo raciocina antes de
    # responder, entao estourar o tempo e normal, nao excepcional. Reenviamos.
    body = None
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            last = LLMError(f"HTTP {exc.code}: {detail}")
            if exc.code not in (429, 500, 502, 503, 504, 524):
                raise last from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = LLMError(f"falha ao falar com o LLM: {exc}")
        if attempt < 2:
            time.sleep(4 * (attempt + 1))
    if body is None:
        raise last or LLMError("sem resposta do LLM")

    choices = body.get("choices") or []
    if not choices:
        raise LLMError(f"resposta sem choices: {str(body)[:200]}")
    # `reasoning` vem separado de `content` nesta API; so o content interessa.
    return (choices[0].get("message") or {}).get("content") or ""


def describe_images(paths: list, prompt: str, timeout: int = 240) -> str:
    """Descreve imagens usando o modelo de visao (LLM_VISION_*).

    Existe porque o modelo rapido de texto nao tem visao ("At most 0 image(s)"),
    e sem ler as telas o redator escreve enchimento generico: em post de demo o
    conteudo real (precos, prazos, numeros) esta no video, nao no texto do post.
    """
    import base64
    import mimetypes
    from pathlib import Path

    key = (os.getenv("LLM_VISION_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    base = (os.getenv("LLM_VISION_BASE_URL") or os.getenv("LLM_BASE_URL")
            or DEFAULT_BASE_URL).strip().rstrip("/")
    model = (os.getenv("LLM_VISION_MODEL") or DEFAULT_MODEL).strip()
    if not key:
        raise LLMError("LLM_VISION_API_KEY/LLM_API_KEY nao configurada")

    content: list[dict] = [{"type": "text", "text": prompt}]
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{data}"}})
    if len(content) == 1:
        return ""

    payload = json.dumps({"model": model,
                          "messages": [{"role": "user", "content": content}],
                          "temperature": 0.3}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "instamaker/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - visao e opcional, nao derruba o post
        raise LLMError(f"visao falhou: {exc}") from exc
    choices = body.get("choices") or []
    return (choices[0].get("message") or {}).get("content", "") if choices else ""


def chat_json(prompt: str, system: str | None = None, timeout: int = 180) -> dict:
    """Igual ao chat(), mas exige JSON de volta e tolera cerca de markdown."""
    raw = chat(
        prompt,
        system=(system or "") + "\nResponda SOMENTE com JSON valido, sem markdown.",
        temperature=0.7,
        timeout=timeout,
    ).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # alguns modelos falam antes do JSON; pega o maior objeto da resposta
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise LLMError(f"resposta nao era JSON: {raw[:200]}")
        return json.loads(match.group(0))
