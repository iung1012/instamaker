#!/usr/bin/env python3
"""Validate and exchange Instagram Graph API access tokens.

This project uses the Instagram Graph API with Facebook Login. An expired
Facebook user token cannot be refreshed by the API; a new short-lived user
token must be exchanged for a long-lived token first.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = PROJECT_DIR / ".env"
DEFAULT_GRAPH_VERSION = "v22.0"
TOKEN_ENV_KEY = "IG_ACCESS_TOKEN"


def load_env_file(env_path: Path) -> None:
    """Load simple KEY=value entries without replacing real environment vars."""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def graph_get(graph_base_url: str, endpoint: str, query: dict[str, str]) -> dict:
    """Call Graph API without putting credentials in error messages."""
    url = f"{graph_base_url}/{endpoint}?{parse.urlencode(query)}"
    req = request.Request(url=url, method="GET")
    try:
        with request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Meta Graph API HTTP {exc.code}: {raw}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Erro de rede ao acessar a Meta: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("A Meta retornou uma resposta que nao e JSON valido.") from exc


def require_value(name: str, *, prompt: bool = False, secret: bool = False) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value

    if prompt:
        reader = getpass.getpass if secret else input
        value = reader(f"{name}: ").strip()
        if value:
            return value

    raise SystemExit(f"Defina {name} no .env ou no ambiente.")


def epoch_to_text(value: object) -> str:
    try:
        timestamp = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "desconhecida"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def token_debug(graph_base_url: str, token: str, app_id: str, app_secret: str) -> dict:
    app_access_token = f"{app_id}|{app_secret}"
    response = graph_get(
        graph_base_url,
        "debug_token",
        {"input_token": token, "access_token": app_access_token},
    )
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"Resposta inesperada ao depurar token: {response}")
    return data


def print_token_debug(data: dict) -> bool:
    is_valid = bool(data.get("is_valid"))
    print(f"Token valido: {'sim' if is_valid else 'nao'}")
    print(f"Aplicacao: {data.get('application') or data.get('app_id') or 'desconhecida'}")
    print(f"Usuario Meta: {data.get('user_id') or 'desconhecido'}")
    print(f"Expira em: {epoch_to_text(data.get('expires_at'))}")
    print(
        "Expiracao de acesso aos dados: "
        f"{epoch_to_text(data.get('data_access_expiration_time'))}"
    )
    scopes = data.get("scopes") or []
    if isinstance(scopes, list):
        print(f"Permissoes: {', '.join(str(scope) for scope in scopes) or 'desconhecidas'}")
    return is_valid


def check_instagram_user(
    graph_base_url: str,
    token: str,
    ig_user_id: str,
) -> dict:
    return graph_get(
        graph_base_url,
        ig_user_id,
        {
            "fields": "id,username",
            "access_token": token,
        },
    )


def exchange_for_long_lived_token(
    graph_base_url: str,
    short_lived_token: str,
    app_id: str,
    app_secret: str,
) -> dict:
    response = graph_get(
        graph_base_url,
        "oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_lived_token,
        },
    )
    if not response.get("access_token"):
        raise RuntimeError(f"Resposta inesperada ao trocar token: {response}")
    return response


def update_env_value(env_path: Path, key: str, value: str) -> None:
    """Replace one env entry atomically while preserving the other entries."""
    old_content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    newline = "\r\n" if "\r\n" in old_content else "\n"
    lines = old_content.splitlines(keepends=True)
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replacement = f"{key}={value}{newline}"
    replaced = False

    for index, line in enumerate(lines):
        if key_pattern.match(line):
            lines[index] = replacement
            replaced = True
            break

    if not replaced:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.append(replacement)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=env_path.parent,
        prefix=f".{env_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write("".join(lines))

    try:
        os.replace(temp_path, env_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida ou troca o token do Instagram Graph API por um token longo."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Valida o token atual.")
    action.add_argument(
        "--exchange",
        action="store_true",
        help="Troca um token curto novo por um token de longa duracao.",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Arquivo .env usado para ler e, com --save, salvar o token.",
    )
    parser.add_argument(
        "--prompt-token",
        action="store_true",
        help="Pede o token curto sem exibi-lo na tela nem na linha de comando.",
    )
    parser.add_argument(
        "--prompt-app-credentials",
        action="store_true",
        help="Pede META_APP_ID e META_APP_SECRET se nao estiverem configurados.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Salva o novo token em IG_ACCESS_TOKEN no .env.",
    )
    parser.add_argument(
        "--graph-version",
        default="",
        help="Versao da Graph API. Padrao: v22.0.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    env_path = Path(args.env_file).expanduser().resolve()
    load_env_file(env_path)

    graph_version = args.graph_version.strip() or os.getenv(
        "META_GRAPH_VERSION", DEFAULT_GRAPH_VERSION
    )
    graph_base_url = f"https://graph.facebook.com/{graph_version.strip('/')}/"
    graph_base_url = graph_base_url.rstrip("/")
    ig_user_id = require_value("IG_USER_ID")

    if args.check:
        token = require_value(TOKEN_ENV_KEY)
        app_id = os.getenv("META_APP_ID", "").strip()
        app_secret = os.getenv("META_APP_SECRET", "").strip()

        if app_id and app_secret:
            print("Depurando token na Meta...")
            data = token_debug(graph_base_url, token, app_id, app_secret)
            valid = print_token_debug(data)
        else:
            print("META_APP_ID/META_APP_SECRET ausentes; validando acesso ao Instagram...")
            valid = True

        account = check_instagram_user(graph_base_url, token, ig_user_id)
        print(f"Instagram: @{account.get('username', 'desconhecido')}")
        return 0 if valid else 1

    app_id = require_value(
        "META_APP_ID",
        prompt=args.prompt_app_credentials,
    )
    app_secret = require_value(
        "META_APP_SECRET",
        prompt=args.prompt_app_credentials,
        secret=True,
    )
    if args.prompt_token:
        short_lived_token = getpass.getpass(
            "Cole o novo token curto da Meta (entrada oculta): "
        ).strip()
    else:
        short_lived_token = require_value(TOKEN_ENV_KEY)

    if not short_lived_token:
        raise SystemExit("O token curto nao pode estar vazio.")

    print("Trocando token curto por token de longa duracao...")
    response = exchange_for_long_lived_token(
        graph_base_url,
        short_lived_token,
        app_id,
        app_secret,
    )
    new_token = str(response["access_token"])

    data = token_debug(graph_base_url, new_token, app_id, app_secret)
    if not print_token_debug(data):
        raise RuntimeError("A Meta retornou um token novo, mas ele nao foi validado.")

    account = check_instagram_user(graph_base_url, new_token, ig_user_id)
    print(f"Instagram: @{account.get('username', 'desconhecido')}")

    if args.save:
        update_env_value(env_path, TOKEN_ENV_KEY, new_token)
        print(f"Token salvo em: {env_path}")
    else:
        print("Token nao salvo. Use --save para atualizar o .env.")

    print("Atualize tambem o secret IG_ACCESS_TOKEN no GitHub Actions antes da proxima execucao.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        sys.exit(1)
