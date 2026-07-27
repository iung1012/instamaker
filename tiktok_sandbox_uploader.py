#!/usr/bin/env python3
"""TikTok Sandbox login + upload helper."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import secrets
import time
import threading
from datetime import datetime, timedelta, timezone
import string
import urllib.parse
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


TIKTOK_AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
TIKTOK_DIRECT_POST_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_PUBLISH_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
ENV_PATH = Path(__file__).resolve().parent / ".env"


@dataclass(frozen=True)
class OAuthResult:
    code: str
    state: str
    error: str | None = None
    error_description: str | None = None


def load_env_file(env_path: Path) -> None:
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


def save_env_values(env_path: Path, values: dict[str, str]) -> None:
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated_keys = set(values)
    output_lines: list[str] = []
    seen_keys: set[str] = set()

    for raw_line in existing_lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            output_lines.append(raw_line)
            continue

        key, _ = line.split("=", 1)
        key = key.strip()
        if key in values:
            output_lines.append(f"{key}={values[key]}")
            seen_keys.add(key)
        else:
            output_lines.append(raw_line)

    for key in sorted(updated_keys - seen_keys):
        output_lines.append(f"{key}={values[key]}")

    env_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")


def generate_state() -> str:
    return secrets.token_urlsafe(24)


def generate_code_verifier() -> str:
    alphabet = string.ascii_letters + string.digits + "-._~"
    return "".join(secrets.choice(alphabet) for _ in range(64))


def build_code_challenge(code_verifier: str) -> str:
    return hashlib.sha256(code_verifier.encode("utf-8")).hexdigest()


def build_authorize_url(
    client_key: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "client_key": client_key,
        "response_type": "code",
        "scope": scope,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{TIKTOK_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _read_json_response(response: urllib.response.addinfourl) -> dict:
    raw = response.read().decode("utf-8")
    if not raw.strip():
        return {}
    return json.loads(raw)


def _request_json(request: urllib.request.Request, timeout: int) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _read_json_response(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if raw.strip():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"error": f"http_{exc.code}", "message": raw}
        else:
            payload = {"error": f"http_{exc.code}", "message": exc.reason}
        raise RuntimeError(format_tiktok_error(payload)) from exc


def is_tiktok_token_error(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(
        marker in lowered
        for marker in (
            "access_token_invalid",
            "invalid_grant",
            "token expired",
            "refresh_token",
        )
    )


def format_tiktok_error(payload: dict) -> str:
    code = payload.get("error") or payload.get("code") or "unknown_error"
    description = payload.get("error_description") or payload.get("message") or ""
    log_id = payload.get("log_id")
    pieces = [f"TikTok error: {code}"]
    if description:
        pieces.append(description)
    if log_id:
        pieces.append(f"log_id={log_id}")
    return " | ".join(pieces)


def tiktok_error_code(exc: Exception) -> str:
    text = str(exc)
    prefix = "TikTok error: "
    if not text.startswith(prefix):
        return ""
    remainder = text[len(prefix) :]
    return remainder.split(" | ", 1)[0].strip()


def exchange_code_for_token(
    client_key: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict:
    body = urllib.parse.urlencode(
        {
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        TIKTOK_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    payload = _request_json(request, timeout=30)
    if "error" in payload and "access_token" not in payload:
        raise RuntimeError(format_tiktok_error(payload))
    return payload


def refresh_access_token(client_key: str, client_secret: str, refresh_token: str) -> dict:
    body = urllib.parse.urlencode(
        {
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        TIKTOK_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    payload = _request_json(request, timeout=30)
    if "error" in payload and "access_token" not in payload:
        raise RuntimeError(format_tiktok_error(payload))
    return payload


def query_creator_info(access_token: str) -> dict:
    request = urllib.request.Request(
        TIKTOK_CREATOR_INFO_URL,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        method="POST",
    )
    payload = _request_json(request, timeout=30)
    if "error" in payload and "data" not in payload:
        raise RuntimeError(format_tiktok_error(payload))
    return payload


def choose_privacy_level(privacy_level_options: list[str], requested: str = "auto") -> str:
    normalized = [option.strip() for option in privacy_level_options if option]
    if not normalized:
        raise ValueError("TikTok nao retornou privacy_level_options.")
    if requested != "auto":
        if requested not in normalized:
            raise ValueError(
                f"privacy_level '{requested}' nao esta disponivel. Opcoes: {', '.join(normalized)}"
            )
        return requested
    for preferred in ("SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE"):
        if preferred in normalized:
            return preferred
    return normalized[0]


def token_expiry_utc(expires_in: int | str | None) -> str:
    try:
        seconds = int(expires_in or 0)
    except (TypeError, ValueError):
        seconds = 0
    expiry = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return expiry.isoformat(timespec="seconds")


def write_token_bundle_to_env(env_path: Path, token_payload: dict) -> None:
    updates = {}
    if token_payload.get("access_token"):
        updates["TIKTOK_ACCESS_TOKEN"] = str(token_payload.get("access_token", ""))
    if token_payload.get("refresh_token"):
        updates["TIKTOK_REFRESH_TOKEN"] = str(token_payload.get("refresh_token", ""))
    if token_payload.get("expires_in") is not None:
        updates["TIKTOK_TOKEN_EXPIRES_IN"] = str(token_payload.get("expires_in", ""))
        updates["TIKTOK_TOKEN_EXPIRES_AT_UTC"] = token_expiry_utc(token_payload.get("expires_in"))
    save_env_values(env_path, updates)


def load_token_bundle() -> dict[str, str]:
    return {
        "access_token": os.getenv("TIKTOK_ACCESS_TOKEN", "").strip(),
        "refresh_token": os.getenv("TIKTOK_REFRESH_TOKEN", "").strip(),
        "expires_at_utc": os.getenv("TIKTOK_TOKEN_EXPIRES_AT_UTC", "").strip(),
    }


def token_is_expired(expires_at_utc: str) -> bool:
    if not expires_at_utc:
        return False
    try:
        expires_at = datetime.fromisoformat(expires_at_utc.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) >= expires_at


def detect_mime_type(video_path: Path) -> str:
    suffix = video_path.suffix.lower()
    if suffix == ".mp4":
        return "video/mp4"
    if suffix in {".mov", ".m4v"}:
        return "video/quicktime"
    if suffix == ".webm":
        return "video/webm"
    raise ValueError(
        f"Extensao nao suportada para upload TikTok: {video_path.suffix}. "
        "Use .mp4, .mov, .m4v ou .webm."
    )


def init_direct_post(
    access_token: str,
    video_path: Path,
    privacy_level: str,
    title: str,
) -> dict:
    size = video_path.stat().st_size
    post_info: dict[str, object] = {
        "privacy_level": privacy_level,
    }
    if title:
        post_info["title"] = title

    body = {
        "post_info": post_info,
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": size,
            "total_chunk_count": 1,
        }
    }

    request = urllib.request.Request(
        TIKTOK_DIRECT_POST_INIT_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        method="POST",
    )

    payload = _request_json(request, timeout=30)
    if "error" in payload and "data" not in payload:
        raise RuntimeError(format_tiktok_error(payload))
    return payload


def upload_file_to_tiktok(upload_url: str, video_path: Path) -> None:
    mime_type = detect_mime_type(video_path)
    total_size = video_path.stat().st_size

    with video_path.open("rb") as file_handle:
        payload = file_handle.read()
        request = urllib.request.Request(
            upload_url,
            data=payload,
            headers={
                "Content-Type": mime_type,
                "Content-Length": str(total_size),
                "Content-Range": f"bytes 0-{total_size - 1}/{total_size}",
            },
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if raw.strip():
                raise RuntimeError(raw) from exc
            raise


def fetch_publish_status(access_token: str, publish_id: str) -> dict:
    request = urllib.request.Request(
        TIKTOK_PUBLISH_STATUS_URL,
        data=json.dumps({"publish_id": publish_id}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        method="POST",
    )
    payload = _request_json(request, timeout=30)
    if "error" in payload and "data" not in payload:
        raise RuntimeError(format_tiktok_error(payload))
    return payload


def wait_for_publish_complete(
    access_token: str,
    publish_id: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> dict:
    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
    last_payload: dict = {}

    while datetime.now(timezone.utc) < deadline:
        last_payload = fetch_publish_status(access_token=access_token, publish_id=publish_id)
        data = last_payload.get("data") or {}
        status = data.get("status", "")
        print(f"   status: {status}")

        if status == "PUBLISH_COMPLETE":
            return last_payload
        if status == "FAILED":
            raise RuntimeError(
                f"TikTok publish failed: {data.get('fail_reason') or 'unknown_fail_reason'}"
            )

        time.sleep(poll_interval_seconds)

    return last_payload


class OAuthCallbackServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)
        self.oauth_result: OAuthResult | None = None
        self.oauth_event = threading.Event()


class TikTokCallbackHandler(BaseHTTPRequestHandler):
    server: OAuthCallbackServer

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path.rstrip("/") != "/tiktok/callback":
            self.send_error(404, "Not Found")
            return

        error = query.get("error", [None])[0]
        error_description = query.get("error_description", [None])[0]
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0]

        if error:
            self.server.oauth_result = OAuthResult(
                code="",
                state=state or "",
                error=error,
                error_description=error_description,
            )
        elif not code or not state:
            self.server.oauth_result = OAuthResult(
                code="",
                state=state or "",
                error="invalid_callback",
                error_description="TikTok callback did not include code and state.",
            )
        else:
            self.server.oauth_result = OAuthResult(code=code, state=state)

        self.server.oauth_event.set()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        message = "Authorization received. You can close this tab and return to the terminal."
        self.wfile.write(
            f"<!doctype html><html><body><p>{html.escape(message)}</p></body></html>".encode("utf-8")
        )

    def log_message(self, format: str, *args) -> None:
        return


def wait_for_oauth_callback(redirect_uri: str, timeout_seconds: int) -> OAuthResult:
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http":
        raise ValueError("Para teste local, use redirect_uri com esquema http.")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if port is None:
        raise ValueError("redirect_uri precisa ter porta explicita.")

    server = OAuthCallbackServer((host, port), TikTokCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        if not server.oauth_event.wait(timeout=timeout_seconds):
            raise TimeoutError("Tempo esgotado aguardando o callback do TikTok.")
        if server.oauth_result is None:
            raise RuntimeError("Callback recebido sem payload de OAuth.")
        return server.oauth_result
    finally:
        server.shutdown()
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Conecta no TikTok Sandbox via Login Kit e envia um video de teste."
    )
    parser.add_argument(
        "--client-key",
        default=os.getenv("TIKTOK_CLIENT_KEY", ""),
        help="TikTok client key",
    )
    parser.add_argument(
        "--client-secret",
        default=os.getenv("TIKTOK_CLIENT_SECRET", ""),
        help="TikTok client secret",
    )
    parser.add_argument(
        "--redirect-uri",
        default="http://127.0.0.1:8765/tiktok/callback/",
        help="Redirect URI cadastrado no TikTok para a app desktop",
    )
    parser.add_argument(
        "--scope",
        default="user.info.basic,video.publish",
        help="Scopes separados por virgula",
    )
    parser.add_argument(
        "--video",
        default="avatar_video.mp4",
        help="Arquivo de video local para upload",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Tempo maximo aguardando autorizacao do usuario",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Nao abre o navegador automaticamente",
    )
    parser.add_argument(
        "--token-only",
        action="store_true",
        help="Pula o login e usa TIKTOK_ACCESS_TOKEN/TIKTOK_REFRESH_TOKEN do .env.",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Titulo/caption do video no TikTok.",
    )
    parser.add_argument(
        "--privacy-level",
        default="auto",
        help="Nivel de privacidade: auto, SELF_ONLY, MUTUAL_FOLLOW_FRIENDS, FOLLOWER_OF_CREATOR ou PUBLIC_TO_EVERYONE.",
    )
    parser.add_argument(
        "--status-timeout",
        type=int,
        default=180,
        help="Tempo maximo aguardando o PUBLISH_COMPLETE.",
    )
    parser.add_argument(
        "--status-poll-interval",
        type=int,
        default=5,
        help="Intervalo entre consultas de status.",
    )
    return parser


def main() -> int:
    load_env_file(ENV_PATH)
    args = build_parser().parse_args()

    client_key = args.client_key.strip()
    client_secret = args.client_secret.strip()
    if not client_key:
        raise SystemExit("Defina --client-key antes de executar.")
    if not client_secret:
        raise SystemExit("Defina --client-secret antes de executar.")

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        raise SystemExit(f"Video nao encontrado: {video_path}")

    token_bundle = load_token_bundle()
    access_token = token_bundle["access_token"]
    refresh_token = token_bundle["refresh_token"]
    redirect_uri = args.redirect_uri.strip()
    token_payload: dict | None = None

    if args.token_only:
        if not access_token and not refresh_token:
            raise SystemExit(
                "Defina TIKTOK_ACCESS_TOKEN ou TIKTOK_REFRESH_TOKEN no .env para usar --token-only."
            )
        if not access_token or token_is_expired(token_bundle["expires_at_utc"]):
            if not refresh_token:
                raise SystemExit(
                    "TIKTOK_ACCESS_TOKEN expirou e TIKTOK_REFRESH_TOKEN nao foi definido."
                )
            print("1) Renovando access token salvo no .env...")
            token_payload = refresh_access_token(
                client_key=client_key,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            access_token = token_payload.get("access_token", "")
            refresh_token = token_payload.get("refresh_token", refresh_token)
            if not access_token:
                raise SystemExit("TikTok nao retornou access_token na renovacao.")
            write_token_bundle_to_env(ENV_PATH, token_payload)
            print("   Token renovado e salvo no .env.")
        else:
            print("1) Usando access token salvo no .env.")
    else:
        state = generate_state()
        code_verifier = generate_code_verifier()
        code_challenge = build_code_challenge(code_verifier)
        auth_url = build_authorize_url(
            client_key=client_key,
            redirect_uri=redirect_uri,
            scope=args.scope,
            state=state,
            code_challenge=code_challenge,
        )

        print("1) Abra a autorizacao do TikTok no navegador.")
        print(auth_url)
        if not args.no_browser:
            webbrowser.open(auth_url)

        print(f"2) Aguarde o callback em {redirect_uri}")
        oauth_result = wait_for_oauth_callback(redirect_uri=redirect_uri, timeout_seconds=args.timeout)

        if oauth_result.error:
            raise SystemExit(
                f"Autorizacao recusada pelo TikTok: {oauth_result.error}"
                + (f" - {oauth_result.error_description}" if oauth_result.error_description else "")
            )

        if oauth_result.state != state:
            raise SystemExit("State invalido no callback do TikTok. Fluxo abortado.")

        print("3) Trocando authorization code por access token...")
        token_payload = exchange_code_for_token(
            client_key=client_key,
            client_secret=client_secret,
            code=oauth_result.code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )
        print(f"   open_id: {token_payload.get('open_id')}")
        print(f"   scope: {token_payload.get('scope')}")
        print(f"   expires_in: {token_payload.get('expires_in')}")

        access_token = token_payload.get("access_token")
        refresh_token = token_payload.get("refresh_token", "")
        if not access_token:
            raise SystemExit("TikTok nao retornou access_token.")
        write_token_bundle_to_env(ENV_PATH, token_payload)
        print("   Tokens salvos no .env.")

    print("2) Consultando creator_info...")
    creator_payload = query_creator_info(access_token=access_token)
    creator_data = creator_payload.get("data") or {}
    privacy_options = creator_data.get("privacy_level_options") or []
    privacy_level = choose_privacy_level(privacy_options, requested=args.privacy_level)
    print(f"   privacy_level: {privacy_level}")

    print("3) Inicializando Direct Post...")
    try:
        init_payload = init_direct_post(
            access_token=access_token,
            video_path=video_path,
            privacy_level=privacy_level,
            title=args.title.strip(),
        )
    except RuntimeError as exc:
        error_code = tiktok_error_code(exc)
        if error_code == "unaudited_client_can_only_post_to_private_accounts":
            raise SystemExit(
                "TikTok bloqueou o post porque a conta alvo nao esta privada. "
                "No Sandbox, a conta usada para publicar precisa estar em modo privado "
                "no app do TikTok antes do upload. Se quiser publicar para conta publica, "
                "o app precisa passar pela auditoria."
            ) from exc
        if args.token_only and refresh_token and is_tiktok_token_error(str(exc)):
            print("   Access token rejeitado, renovando via refresh_token...")
            token_payload = refresh_access_token(
                client_key=client_key,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
            access_token = token_payload.get("access_token", "")
            refresh_token = token_payload.get("refresh_token", refresh_token)
            if not access_token:
                raise SystemExit("TikTok nao retornou access_token na renovacao.")
            write_token_bundle_to_env(ENV_PATH, token_payload)
            creator_payload = query_creator_info(access_token=access_token)
            creator_data = creator_payload.get("data") or {}
            privacy_options = creator_data.get("privacy_level_options") or []
            privacy_level = choose_privacy_level(privacy_options, requested=args.privacy_level)
            init_payload = init_direct_post(
                access_token=access_token,
                video_path=video_path,
                privacy_level=privacy_level,
                title=args.title.strip(),
            )
        else:
            raise

    data = init_payload.get("data") or {}
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")
    if not upload_url:
        raise SystemExit(f"Resposta inesperada do TikTok na inicializacao do upload: {init_payload}")

    print(f"   publish_id: {publish_id}")
    print("4) Enviando arquivo para os servidores do TikTok...")
    upload_file_to_tiktok(upload_url=upload_url, video_path=video_path)

    print("5) Aguardando conclusao do post...")
    if publish_id:
        status_payload = wait_for_publish_complete(
            access_token=access_token,
            publish_id=str(publish_id),
            timeout_seconds=args.status_timeout,
            poll_interval_seconds=args.status_poll_interval,
        )
        status_data = status_payload.get("data") or {}
        print(f"   status final: {status_data.get('status')}")
        print(f"   post_id publico: {status_data.get('publicaly_available_post_id')}")
    print("Direct Post concluido.")
    print("Observacao: se o cliente ainda estiver sem auditoria, o TikTok pode restringir a visibilidade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
