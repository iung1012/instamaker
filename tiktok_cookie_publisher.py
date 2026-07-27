"""Publicador de vídeos no TikTok utilizando cookies de sessão via tiktok-uploader (Playwright).

Este script permite publicar vídeos diretamente no TikTok sem necessidade de uma conta
de desenvolvedor ou aprovação de aplicativo no TikTok for Developers.
"""

import argparse
import os
import sys
from pathlib import Path

try:
    from tiktok_uploader.upload import upload_video
except ImportError:
    upload_video = None


def resolve_cookies_file(custom_path: str | None) -> Path | None:
    """Verifica e resolve o caminho do arquivo de cookies."""
    candidates = []
    if custom_path:
        candidates.append(Path(custom_path))
    candidates.extend([
        Path("tiktok_cookies.txt"),
        Path("cookies.txt"),
        Path("cookies.json"),
    ])

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def publish_tiktok_cookie(
    video_path: str | Path,
    description: str = "",
    cookies_path: str | Path | None = None,
    dry_run: bool = False,
) -> bool:
    """Realiza o upload de um vídeo para o TikTok via tiktok-uploader."""
    video = Path(video_path).resolve()
    if not video.exists() or not video.is_file():
        print(f"Erro: Arquivo de vídeo não encontrado: {video}")
        return False

    resolved_cookies = resolve_cookies_file(str(cookies_path) if cookies_path else None)
    if not resolved_cookies:
        print("Erro: Nenhum arquivo de cookies válido encontrado.")
        print("Certifique-se de exportar os cookies do seu navegador TikTok como 'tiktok_cookies.txt' ou 'cookies.txt'.")
        return False

    print(f"--- Iniciando publicação no TikTok via Cookies ---")
    print(f"Vídeo: {video.name}")
    print(f"Cookies: {resolved_cookies.name}")
    if description:
        print(f"Descrição: {description[:80]}...")

    if dry_run:
        print("[dry-run] Upload para o TikTok via cookies simulado com sucesso.")
        return True

    if upload_video is None:
        print("Erro: A biblioteca 'tiktok-uploader' não está instalada no ambiente Python.")
        print("Instale executando: pip install tiktok-uploader")
        return False

    try:
        # Chama a função principal do tiktok-uploader
        failed_uploads = upload_video(
            filename=str(video),
            description=description or "",
            cookies=str(resolved_cookies),
        )
        if failed_uploads:
            print(f"Erro: O upload do vídeo falhou no TikTok: {failed_uploads}")
            return False

        print(f"Sucesso: Vídeo {video.name} publicado no TikTok com sucesso!")
        return True
    except Exception as exc:
        print(f"Exceção ao publicar no TikTok via cookies: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Publica vídeos no TikTok via cookies de sessão.")
    parser.add_argument("--video", required=True, help="Caminho do vídeo .mp4 para upload")
    parser.add_argument("--description", default="", help="Descrição / Legenda do vídeo")
    parser.add_argument("--cookies", default=None, help="Caminho do arquivo de cookies (txt ou json)")
    parser.add_argument("--dry-run", action="store_true", help="Simula a execução sem realizar upload real")

    args = parser.parse_args()
    success = publish_tiktok_cookie(
        video_path=args.video,
        description=args.description,
        cookies_path=args.cookies,
        dry_run=args.dry_run,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
