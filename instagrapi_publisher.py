"""Publica Reels logando com usuario e senha (instagrapi), sem Graph API.

Uso:
    --login                     le usuario e senha do stdin (uma por linha);
                                salva a sessao em .ig_session.json
    --publish VIDEO --caption T publica usando a sessao salva
    --whoami                    mostra quem esta logado na sessao

A senha entra por stdin de proposito: argumento de linha de comando vazaria
no `ps`. A sessao salva dispensa senha nas publicacoes seguintes — e tambem
evita logins repetidos, que e o que costuma disparar verificacao na conta.
"""

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

SESSION_FILENAME = ".ig_session.json"


def make_thumbnail(video: Path) -> Path:
    """Frame do video para capa; sem ela o instagrapi exigiria moviepy."""
    dest = video.with_suffix(".thumb.jpg")
    ffmpeg = Path(sys.executable).parent / "ffmpeg"
    result = subprocess.run(
        [str(ffmpeg), "-y", "-loglevel", "error", "-ss", "0.5", "-i", str(video),
         "-frames:v", "1", "-q:v", "3", str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not dest.is_file():
        raise RuntimeError(f"Nao consegui extrair a capa: {result.stderr.strip()[:200]}")
    return dest


def build_client(session_path: Path):
    from instagrapi import Client

    client = Client()
    client.delay_range = [1, 3]
    if session_path.is_file():
        client.load_settings(session_path)
    return client


def do_login(session_path: Path) -> int:
    username = sys.stdin.readline().strip()
    password = sys.stdin.readline().strip()
    if not username or not password:
        print("Uso: mande usuario e senha, um por linha, no stdin.", file=sys.stderr)
        return 1

    client = build_client(session_path)
    client.login(username, password)
    client.dump_settings(session_path)
    session_path.chmod(0o600)
    print(f"Logado como @{client.username}")
    return 0


def do_whoami(session_path: Path) -> int:
    if not session_path.is_file():
        print("Nenhuma sessao salva.", file=sys.stderr)
        return 1
    client = build_client(session_path)
    info = client.account_info()
    print(f"@{info.username}")
    return 0


def do_publish(session_path: Path, video: Path, caption: str) -> int:
    if not session_path.is_file():
        print("Nenhuma sessao salva. Faca /login primeiro.", file=sys.stderr)
        return 1

    client = build_client(session_path)
    thumbnail = make_thumbnail(video)
    try:
        media = client.clip_upload(video, caption, thumbnail=thumbnail)
    finally:
        thumbnail.unlink(missing_ok=True)
        # o instagrapi costuma deixar um .jpg proprio ao lado do video
        for leftover in video.parent.glob(f"{video.stem}.thumb.jpg.*"):
            leftover.unlink(missing_ok=True)

    print(f"Post ID: {media.pk} | https://www.instagram.com/reel/{media.code}/")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Publica Reels via instagrapi.")
    parser.add_argument("--session", default=SESSION_FILENAME, help="Arquivo da sessao.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--login", action="store_true", help="Loga com usuario/senha do stdin.")
    action.add_argument("--publish", metavar="VIDEO", help="Publica o video como Reel.")
    action.add_argument("--whoami", action="store_true", help="Mostra a conta da sessao.")
    parser.add_argument("--caption", default="", help="Legenda do post.")
    args = parser.parse_args()

    session_path = Path(args.session)
    try:
        if args.login:
            return do_login(session_path)
        if args.whoami:
            return do_whoami(session_path)
        video = Path(args.publish)
        if not video.is_file():
            print(f"Video nao encontrado: {video}", file=sys.stderr)
            return 1
        return do_publish(session_path, video, args.caption)
    except Exception as exc:  # erros do instagrapi viram mensagem legivel no chat
        name = type(exc).__name__
        hint = ""
        lowered = f"{name} {exc}".lower()
        if "challenge" in lowered or "checkpoint" in lowered:
            hint = ("\nO Instagram pediu verificacao: abra o app oficial, confirme que "
                    "e voce e tente de novo.")
        elif "two" in lowered and "factor" in lowered:
            hint = "\nConta com 2FA: gere uma senha de app ou desative o 2FA para o login."
        elif "password" in lowered or "credentials" in lowered:
            hint = "\nConfira usuario e senha."
        print(f"{name}: {exc}{hint}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
