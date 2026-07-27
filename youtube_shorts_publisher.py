#!/usr/bin/env python3
"""YouTube Shorts OAuth + upload helper."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent
ENV_PATH = PROJECT_DIR / ".env"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_TAGS = "Shorts,IA,automacao,tecnologia,programacao"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


def load_env_file(env_path: Path = ENV_PATH) -> None:
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


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "y", "on", "sim"}


def resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_DIR / path).resolve()


def parse_tags(raw_tags: str) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for part in re.split(r"[,\n;]+", raw_tags or ""):
        tag = part.strip().lstrip("#")
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)

    if "shorts" not in seen:
        tags.insert(0, "Shorts")
    return tags


def trim_text(text: str, max_length: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= max_length:
        return normalized

    candidate = normalized[: max(0, max_length - 3)].rstrip()
    if " " in candidate:
        candidate = candidate.rsplit(" ", 1)[0].rstrip()
    return candidate.rstrip(" .,-") + "..."


def read_related_description(media_path: Path) -> str:
    candidates = [
        media_path.with_name(f"{media_path.stem}_info.txt"),
        media_path.with_suffix(".txt"),
    ]
    if media_path.stem.endswith("_final"):
        base_stem = media_path.stem[:-6]
        candidates.append(media_path.with_name(f"{base_stem}_info.txt"))
        candidates.append(Path("timeline_downloads") / f"{base_stem}_info.txt")

    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        content = candidate.read_text(encoding="utf-8", errors="ignore").strip()
        for marker in ("Descricao:", "Descrição:"):
            if marker in content:
                content = content.split(marker, 1)[1].strip()
        if content:
            return content
    return ""


def first_meaningful_line(text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith(("autor:", "url:", "tweet:", "post:")):
            continue
        return line
    return ""


def build_title(
    video_path: Path,
    explicit_title: str,
    title_prefix: str,
    context_text: str,
) -> str:
    if explicit_title.strip():
        base = explicit_title.strip()
    else:
        base = first_meaningful_line(context_text)
        if not base:
            base = video_path.stem
            if base.endswith("_final"):
                base = base[:-6]
            base = base.replace("_", " ").replace("-", " ")

    title = f"{title_prefix.strip()} {base}".strip() if title_prefix.strip() else base
    return trim_text(title, 100) or "Short"


def ensure_shorts_hashtag(title: str, description: str, enabled: bool) -> str:
    if not enabled:
        return description.strip()

    combined = f"{title}\n{description}".lower()
    if "#shorts" in combined:
        return description.strip()

    if description.strip():
        return f"{description.strip()}\n\n#Shorts"
    return "#Shorts"


def build_video_resource(
    video_path: Path,
    title: str,
    title_prefix: str,
    description: str,
    tags: str,
    category_id: str,
    privacy_status: str,
    made_for_kids: bool,
    shorts_hashtag: bool,
) -> dict[str, Any]:
    context_text = read_related_description(video_path)
    resolved_title = build_title(
        video_path=video_path,
        explicit_title=title,
        title_prefix=title_prefix,
        context_text=context_text,
    )
    resolved_description = description.strip() or context_text.strip()
    resolved_description = ensure_shorts_hashtag(
        title=resolved_title,
        description=resolved_description,
        enabled=shorts_hashtag,
    )

    return {
        "snippet": {
            "title": resolved_title,
            "description": resolved_description,
            "tags": parse_tags(tags),
            "categoryId": category_id.strip() or "28",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }


def load_json_value(raw_json: str, label: str) -> dict[str, Any] | None:
    normalized_json = raw_json.lstrip("\ufeff")
    if not normalized_json.strip():
        return None
    try:
        payload = json.loads(normalized_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} nao contem JSON valido: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} precisa conter um objeto JSON.")
    return payload


def load_json_file(path: Path, label: str) -> dict[str, Any]:
    try:
        raw_json = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SystemExit(f"Nao foi possivel ler {label}: {path} ({exc})") from exc

    if not raw_json.strip():
        raise ValueError(f"{label} esta vazio: {path}")

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} nao contem JSON valido: {path} ({exc})") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{label} precisa conter um objeto JSON: {path}")
    return payload


def save_credentials(token_file: Path, credentials: Any) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Token do YouTube salvo em: {token_file}")


def load_credentials(
    client_secrets_file: Path,
    client_secrets_json: str,
    token_file: Path,
    token_json: str,
    no_browser: bool,
    auth_port: int,
) -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Dependencias do Google nao instaladas. Rode: pip install -r requirements.txt"
        ) from exc

    credentials = None
    token_payload = load_json_value(token_json, "YOUTUBE_TOKEN_JSON")
    if token_payload is not None:
        credentials = Credentials.from_authorized_user_info(token_payload, SCOPES)
    elif token_file.exists():
        try:
            token_payload = load_json_file(token_file, "YOUTUBE_TOKEN_FILE")
        except ValueError as exc:
            print(f"Token salvo do YouTube invalido; sera feita nova autorizacao. {exc}")
        else:
            credentials = Credentials.from_authorized_user_info(token_payload, SCOPES)

    if credentials and credentials.expired and credentials.refresh_token:
        print("Renovando token do YouTube...")
        credentials.refresh(Request())
        save_credentials(token_file, credentials)

    if credentials and credentials.valid:
        return credentials

    client_config = load_json_value(client_secrets_json, "YOUTUBE_CLIENT_SECRETS_JSON")
    if client_config is not None:
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    else:
        if not client_secrets_file.exists():
            raise SystemExit(
                "Credenciais OAuth do YouTube nao encontradas. Defina "
                "YOUTUBE_CLIENT_SECRETS_FILE, YOUTUBE_CLIENT_SECRETS_JSON ou use "
                "--client-secrets-file."
            )
        try:
            client_config = load_json_file(client_secrets_file, "YOUTUBE_CLIENT_SECRETS_FILE")
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    print("Abrindo autorizacao OAuth do YouTube...")
    credentials = flow.run_local_server(
        port=auth_port,
        open_browser=not no_browser,
        authorization_prompt_message="Abra esta URL no navegador:\n{url}\n",
        success_message="Autorizacao recebida. Pode fechar esta aba.",
        prompt="consent",
    )
    save_credentials(token_file, credentials)
    return credentials


def format_google_error(exc: Exception) -> str:
    status = getattr(getattr(exc, "resp", None), "status", "")
    raw_content = getattr(exc, "content", b"")
    if isinstance(raw_content, bytes):
        raw_text = raw_content.decode("utf-8", errors="replace")
    else:
        raw_text = str(raw_content)

    message = raw_text.strip()
    try:
        payload = json.loads(raw_text)
        error = payload.get("error") or {}
        if isinstance(error, dict):
            message = error.get("message") or message
    except json.JSONDecodeError:
        pass

    prefix = f"YouTube API HTTP {status}" if status else "YouTube API error"
    return f"{prefix}: {message}"


def detect_mime_type(video_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(video_path))
    return mime_type or "video/*"


def upload_video(
    credentials: Any,
    video_path: Path,
    body: dict[str, Any],
    notify_subscribers: bool,
) -> str:
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Dependencias do Google nao instaladas. Rode: pip install -r requirements.txt"
        ) from exc

    service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    media = MediaFileUpload(
        str(video_path),
        mimetype=detect_mime_type(video_path),
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )
    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
        notifySubscribers=notify_subscribers,
    )

    response = None
    try:
        while response is None:
            status, response = request.next_chunk(num_retries=3)
            if status:
                print(f"Upload YouTube: {int(status.progress() * 100)}%")
    except HttpError as exc:
        raise RuntimeError(format_google_error(exc)) from exc

    if not isinstance(response, dict) or not response.get("id"):
        raise RuntimeError(f"Resposta inesperada do YouTube: {response}")
    return str(response["id"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publica um video local como YouTube Short via YouTube Data API."
    )
    parser.add_argument("--video", help="Arquivo de video local para upload.")
    parser.add_argument(
        "--client-secrets-file",
        default=os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "youtube_client_secret.json"),
        help="JSON OAuth client do Google Cloud.",
    )
    parser.add_argument(
        "--client-secrets-json",
        default=os.getenv("YOUTUBE_CLIENT_SECRETS_JSON", ""),
        help="Conteudo JSON OAuth client, util para secrets.",
    )
    parser.add_argument(
        "--token-file",
        default=os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json"),
        help="Arquivo onde o OAuth token sera salvo/reutilizado.",
    )
    parser.add_argument(
        "--token-json",
        default=os.getenv("YOUTUBE_TOKEN_JSON", ""),
        help="Conteudo JSON de token autorizado.",
    )
    parser.add_argument("--auth-only", action="store_true", help="Faz apenas OAuth e salva o token.")
    parser.add_argument(
        "--auth-port",
        type=int,
        default=int(os.getenv("YOUTUBE_AUTH_PORT", "8766") or "8766"),
        help="Porta local usada no callback OAuth.",
    )
    parser.add_argument("--no-browser", action="store_true", help="Mostra URL sem abrir navegador.")
    parser.add_argument("--title", default=os.getenv("YOUTUBE_SHORTS_TITLE", ""), help="Titulo do video.")
    parser.add_argument(
        "--title-prefix",
        default=os.getenv("YOUTUBE_SHORTS_TITLE_PREFIX", ""),
        help="Prefixo opcional para titulos automaticos.",
    )
    parser.add_argument(
        "--description",
        default=os.getenv("YOUTUBE_SHORTS_DESCRIPTION", ""),
        help="Descricao do video. Se vazia, tenta usar o _info.txt relacionado.",
    )
    parser.add_argument(
        "--tags",
        default=os.getenv("YOUTUBE_SHORTS_TAGS", DEFAULT_TAGS),
        help="Tags separadas por virgula.",
    )
    parser.add_argument(
        "--category-id",
        default=os.getenv("YOUTUBE_CATEGORY_ID", "28"),
        help="Categoria YouTube. 28 = Science & Technology.",
    )
    parser.add_argument(
        "--privacy-status",
        choices=["private", "unlisted", "public"],
        default=os.getenv("YOUTUBE_PRIVACY_STATUS", "public"),
        help="Privacidade do upload.",
    )

    kids_group = parser.add_mutually_exclusive_group()
    kids_group.add_argument("--made-for-kids", dest="made_for_kids", action="store_true")
    kids_group.add_argument("--not-made-for-kids", dest="made_for_kids", action="store_false")
    parser.set_defaults(made_for_kids=parse_bool(os.getenv("YOUTUBE_MADE_FOR_KIDS"), False))

    notify_group = parser.add_mutually_exclusive_group()
    notify_group.add_argument("--notify-subscribers", dest="notify_subscribers", action="store_true")
    notify_group.add_argument("--no-notify-subscribers", dest="notify_subscribers", action="store_false")
    parser.set_defaults(
        notify_subscribers=parse_bool(os.getenv("YOUTUBE_NOTIFY_SUBSCRIBERS"), False)
    )

    shorts_group = parser.add_mutually_exclusive_group()
    shorts_group.add_argument("--shorts-hashtag", dest="shorts_hashtag", action="store_true")
    shorts_group.add_argument("--no-shorts-hashtag", dest="shorts_hashtag", action="store_false")
    parser.set_defaults(shorts_hashtag=parse_bool(os.getenv("YOUTUBE_SHORTS_HASHTAG"), True))

    parser.add_argument("--dry-run", action="store_true", help="Mostra metadados sem enviar.")
    return parser


def main() -> int:
    load_env_file()
    args = build_parser().parse_args()

    client_secrets_file = resolve_project_path(args.client_secrets_file)
    token_file = resolve_project_path(args.token_file)

    if args.auth_only:
        load_credentials(
            client_secrets_file=client_secrets_file,
            client_secrets_json=args.client_secrets_json,
            token_file=token_file,
            token_json=args.token_json,
            no_browser=args.no_browser,
            auth_port=args.auth_port,
        )
        print("Autorizacao do YouTube concluida.")
        return 0

    if not args.video:
        raise SystemExit("Informe --video ou use --auth-only.")

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists() or not video_path.is_file():
        raise SystemExit(f"Video nao encontrado: {video_path}")
    if video_path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise SystemExit(f"Extensao de video nao suportada: {video_path.suffix}")

    body = build_video_resource(
        video_path=video_path,
        title=args.title,
        title_prefix=args.title_prefix,
        description=args.description,
        tags=args.tags,
        category_id=args.category_id,
        privacy_status=args.privacy_status,
        made_for_kids=args.made_for_kids,
        shorts_hashtag=args.shorts_hashtag,
    )

    print("Metadados YouTube:")
    print(json.dumps(body, ensure_ascii=False, indent=2))

    if args.dry_run:
        print("Dry-run finalizado. Nenhum upload foi enviado.")
        return 0

    credentials = load_credentials(
        client_secrets_file=client_secrets_file,
        client_secrets_json=args.client_secrets_json,
        token_file=token_file,
        token_json=args.token_json,
        no_browser=args.no_browser,
        auth_port=args.auth_port,
    )
    video_id = upload_video(
        credentials=credentials,
        video_path=video_path,
        body=body,
        notify_subscribers=args.notify_subscribers,
    )
    print(f"YouTube Short enviado com sucesso. Video ID: {video_id}")
    print(f"URL: https://www.youtube.com/watch?v={video_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
