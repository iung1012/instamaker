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
    return bool(os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY"))


def _config() -> tuple[str, str, str]:
    key = (os.getenv("LLM_API_KEY") or "").strip()
    if not key:
        raise LLMError("LLM_API_KEY nao encontrada no .env")
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
