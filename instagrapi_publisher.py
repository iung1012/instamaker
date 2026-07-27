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


def do_login_cookies(session_path: Path) -> int:
    """Loga com o cookie `sessionid` (JSON {nome: valor} no stdin), sem senha."""
    import json

    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("Esperava um JSON {nome: valor} no stdin.", file=sys.stderr)
        return 1
    if isinstance(data, list):
        # formato exportado por extensoes de navegador: [{name, value}, ...]
        cookies = {
            str(c.get("name")): str(c.get("value"))
            for c in data
            if isinstance(c, dict) and c.get("name")
        }
    elif isinstance(data, dict):
        cookies = data
    else:
        print("Esperava um JSON {nome: valor} ou uma lista de cookies.", file=sys.stderr)
        return 1
    sessionid = str(cookies.get("sessionid") or "").strip()
    if not sessionid:
        print("Cookie 'sessionid' nao encontrado.", file=sys.stderr)
        return 1

    client = build_client(session_path)
    client.login_by_sessionid(sessionid)
    client.dump_settings(session_path)
    session_path.chmod(0o600)
    info = client.account_info()
    print(f"Logado como @{info.username}")
    return 0


def do_whoami(session_path: Path) -> int:
    if not session_path.is_file():
        print("Nenhuma sessao salva.", file=sys.stderr)
        return 1
    client = build_client(session_path)
    info = client.account_info()
    print(f"@{info.username}")
    return 0


def pick_saved_track(client, wanted: str = "") -> dict | None:
    """Uma das musicas que o dono salvou no Instagram (aba de audios salvos)."""
    import random

    try:
        items = (client.music_bookmarked() or {}).get("items") or []
    except Exception as exc:
        print(f"Aviso: nao consegui ler as musicas salvas ({exc}).", file=sys.stderr)
        return None

    tracks = [i.get("track") for i in items if isinstance(i, dict) and i.get("track")]
    if not tracks:
        return None
    if wanted:
        alvo = wanted.strip().lower()
        for track in tracks:
            texto = f"{track.get('title','')} {track.get('display_artist','')}".lower()
            if alvo in texto:
                return track
    return random.choice(tracks)


def remove_audio(video: Path) -> Path:
    """Cria uma copia do video sem a faixa de audio."""
    dest = video.with_suffix(".muted.mp4")
    ffmpeg = Path(sys.executable).parent / "ffmpeg"
    # Em vez de remover a trilha inteira (-an), recodificamos com volume 0
    # para garantir que o Instagram veja que existe áudio no arquivo
    result = subprocess.run(
        [str(ffmpeg), "-y", "-loglevel", "error", "-i", str(video),
         "-c:v", "copy", "-c:a", "aac", "-af", "volume=0", str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not dest.is_file():
        raise RuntimeError(f"Nao consegui mutar o video: {result.stderr.strip()[:200]}")
    return dest


def do_publish(session_path: Path, video: Path, caption: str,
               music: bool = False, music_name: str = "") -> int:
    if not session_path.is_file():
        print("Nenhuma sessao salva. Faca /login primeiro.", file=sys.stderr)
        return 1

    client = build_client(session_path)
    thumbnail = make_thumbnail(video)
    track = pick_saved_track(client, music_name) if music else None
    upload_video = video
    try:
        if track:
            # O Instagram as vezes ignora original_volume=0.0, entao removemos o audio via ffmpeg
            upload_video = remove_audio(video)
            media = client.clip_upload_with_music(
                upload_video, caption, track, thumbnail=thumbnail,
                original_volume=0.0, music_volume=1.0,
            )
            print(f"Musica: {track.get('title')} - {track.get('display_artist')}")
        else:
            if music:
                print("Aviso: nenhuma musica salva encontrada; publicando sem.",
                      file=sys.stderr)
            media = client.clip_upload(video, caption, thumbnail=thumbnail)
    finally:
        if upload_video != video:
            upload_video.unlink(missing_ok=True)
        thumbnail.unlink(missing_ok=True)
        # o instagrapi costuma deixar um .jpg proprio ao lado do video
        for leftover in video.parent.glob(f"{video.stem}.thumb.jpg.*"):
            leftover.unlink(missing_ok=True)

    print(f"Post ID: {media.pk} | https://www.instagram.com/reel/{media.code}/")
    
    # Salvar no historico para analytics
    import time, json
    history_file = Path("history.json")
    try:
        history = json.loads(history_file.read_text()) if history_file.is_file() else []
    except Exception:
        history = []
    history.append({
        "pk": media.pk,
        "code": media.code,
        "timestamp": time.time()
    })
    # Manter só os últimos 50
    history_file.write_text(json.dumps(history[-50:], indent=2))
    
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Publica Reels via instagrapi.")
    parser.add_argument("--session", default=SESSION_FILENAME, help="Arquivo da sessao.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--login", action="store_true", help="Loga com usuario/senha do stdin.")
    action.add_argument("--login-cookies", action="store_true",
                        help="Loga com cookies (JSON {nome: valor} no stdin).")
    action.add_argument("--publish", metavar="VIDEO", help="Publica o video como Reel.")
    action.add_argument("--whoami", action="store_true", help="Mostra a conta da sessao.")
    action.add_argument("--list-music", action="store_true", help="Lista as musicas salvas.")
    parser.add_argument("--caption", default="", help="Legenda do post.")
    parser.add_argument("--music", action="store_true",
                        help="Usa uma das musicas salvas no Instagram.")
    parser.add_argument("--music-name", default="",
                        help="Escolhe a musica salva pelo titulo/artista.")
    args = parser.parse_args()

    session_path = Path(args.session)
    try:
        if args.login:
            return do_login(session_path)
        if args.login_cookies:
            return do_login_cookies(session_path)
        if args.whoami:
            return do_whoami(session_path)
        if args.list_music:
            if not session_path.is_file():
                print("Nenhuma sessao salva.", file=sys.stderr)
                return 1
            client = build_client(session_path)
            items = (client.music_bookmarked() or {}).get("items") or []
            if not items:
                print("Nenhuma musica salva. Salve audios no app do Instagram.")
                return 0
            for item in items:
                track = item.get("track") or {}
                print(f"- {track.get('title')} — {track.get('display_artist')}")
            return 0
        video = Path(args.publish)
        if not video.is_file():
            print(f"Video nao encontrado: {video}", file=sys.stderr)
            return 1
        return do_publish(session_path, video, args.caption,
                          music=args.music, music_name=args.music_name)
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
