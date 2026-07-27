import argparse
import asyncio
from playwright.async_api import async_playwright
import yt_dlp
import os
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Configurações
COOKIES_FILE = os.getenv("TWITTER_COOKIES_FILE", "cookies.json")
OUTPUT_DIR = 'timeline_downloads'
NAV_TIMEOUT_MS = 90000
NAV_RETRIES = 3
MAX_VIDEOS = 20
MAX_SCROLL_ROUNDS = 25
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def load_processed_tweet_ids(state_file):
    path = Path(state_file)
    if not path.is_file():
        return set()

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()

    tweet_ids = set()
    for source_name in state.get("processed_sources", []):
        match = re.search(r"_(\d+)\.[^.]+$", str(source_name))
        if match:
            tweet_ids.add(match.group(1))
    return tweet_ids


def resolve_executable(name):
    found = shutil.which(name)
    if found:
        return found

    executable_name = f"{name}.exe" if sys.platform.startswith("win") else name
    path_values = [os.environ.get("PATH", "")]

    if sys.platform.startswith("win"):
        try:
            import winreg

            for hive, subkey in (
                (winreg.HKEY_CURRENT_USER, "Environment"),
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ),
            ):
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        value, _ = winreg.QueryValueEx(key, "Path")
                        path_values.append(value)
                except OSError:
                    pass
        except ImportError:
            pass

    for raw_path in path_values:
        for item in raw_path.split(os.pathsep):
            if not item:
                continue
            candidate = Path(item.strip().strip('"')) / executable_name
            if candidate.exists():
                return str(candidate)

    if sys.platform.startswith("win"):
        winget_packages = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
        if winget_packages.exists():
            matches = list(winget_packages.glob(f"**/{executable_name}"))
            if matches:
                return str(matches[0])

    raise RuntimeError(
        f"{name} nao encontrado. Instale o FFmpeg e confirme que '{name} -version' funciona no terminal."
    )


def normalize_tweet_url(tweet_path):
    match = re.search(r"/([^/?#]+)/status/(\d+)", tweet_path)
    if not match:
        return None

    username, tweet_id = match.groups()
    return tweet_id, f"https://x.com/{username}/status/{tweet_id}"


def find_downloaded_video(uploader, tweet_id):
    base = Path(OUTPUT_DIR)
    candidates = [
        p for p in base.glob(f"{uploader}_{tweet_id}.*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def strip_video_metadata(video_path):
    if not video_path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado para limpeza: {video_path}")

    ffmpeg_bin = resolve_executable("ffmpeg")

    temp_output = video_path.with_name(f"{video_path.stem}.clean{video_path.suffix}")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-c",
        "copy",
        str(temp_output),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if temp_output.exists():
            temp_output.unlink()
        raise RuntimeError(
            f"Falha ao remover metadados de {video_path.name}: {result.stderr.strip() or result.stdout.strip()}"
        )

    original_size = video_path.stat().st_size
    cleaned_size = temp_output.stat().st_size
    temp_output.replace(video_path)
    print(
        f"Metadados removidos: {video_path.name} "
        f"({original_size} bytes -> {cleaned_size} bytes)"
    )
    return True

def download_video(url, description, uploader, tweet_id):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    ffmpeg_bin = resolve_executable("ffmpeg")
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'ffmpeg_location': str(Path(ffmpeg_bin).parent),
        'outtmpl': os.path.join(OUTPUT_DIR, f'{uploader}_{tweet_id}.%(ext)s'),
        'quiet': True,
        'noprogress': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"--- Baixando vídeo de @{uploader} ---")
            ydl.download([url])

            downloaded_video = find_downloaded_video(uploader, tweet_id)
            if downloaded_video:
                strip_video_metadata(downloaded_video)
            else:
                print("Aviso: nao foi possivel localizar o video baixado para limpeza de metadados.")
            
            # Salvar descrição
            info_path = os.path.join(OUTPUT_DIR, f'{uploader}_{tweet_id}_info.txt')
            with open(info_path, 'w', encoding='utf-8') as f:
                f.write(f"Autor: {uploader}\n")
                f.write(f"URL: {url}\n")
                f.write(f"Descrição:\n{description}\n")
            print(f"Sucesso: {uploader}_{tweet_id}")
            return True
    except Exception as e:
        print(f"Erro ao baixar vídeo de {url}: {e}")
        return False


async def goto_with_retry(page, url, retries=NAV_RETRIES, timeout_ms=NAV_TIMEOUT_MS):
    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
            return True
        except Exception as e:
            print(f"Tentativa {attempt}/{retries} falhou ao abrir {url}: {e}")
            if attempt < retries:
                await page.wait_for_timeout(3000)
    return False

async def main(max_videos=MAX_VIDEOS, state_file=".automation_state.json"):
    if not os.path.exists(COOKIES_FILE):
        print(f"Erro: Arquivo '{COOKIES_FILE}' não encontrado.")
        print("Por favor, exporte os cookies do seu navegador como 'twitter_cookies.json'.")
        return 1

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        # Carregar e validar formato dos cookies
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            cookies_data = json.load(f)
        
        # O Playwright EXIGE uma lista (array) de cookies.
        # Se os cookies vierem como um dicionário (objeto), precisamos converter.
        if isinstance(cookies_data, dict):
            print("Convertendo formato de cookies de objeto para lista...")
            cookies_list = []
            for name, value in cookies_data.items():
                cookies_list.append({
                    'name': name,
                    'value': str(value),
                    'domain': '.x.com', # Domínio padrão do Twitter
                    'path': '/'
                })
        else:
            cookies_list = cookies_data

        context = await browser.new_context()
        
        try:
            await context.add_cookies(cookies_list)
            print("Cookies carregados com sucesso!")
        except Exception as e:
            print(f"Erro ao carregar cookies no navegador: {e}")
            await browser.close()
            return 1
        
        page = await context.new_page()
        print("Acessando a timeline do Twitter...")
        loaded = await goto_with_retry(page, 'https://x.com/home')
        if not loaded:
            print("Erro: não foi possível abrir https://x.com/home após várias tentativas.")
            await browser.close()
            return 1
        
        # Esperar um pouco mais para garantir que os tweets carreguem
        await page.wait_for_timeout(5000)
        try:
            await page.wait_for_selector('article', timeout=30000)
        except Exception:
            print("Aviso: não encontrei tweets imediatamente; continuando mesmo assim.")
        
        print(f"Procurando vídeos na timeline (meta: {max_videos})...")
        found_count = 0
        seen_tweets = set()
        processed_tweet_ids = load_processed_tweet_ids(state_file)
        if processed_tweet_ids:
            print(f"IDs ja processados carregados: {len(processed_tweet_ids)}")

        for _ in range(MAX_SCROLL_ROUNDS):
            tweets = await page.query_selector_all('article')

            for tweet in tweets:
                if found_count >= max_videos:
                    break

                video_element = await tweet.query_selector('video')
                if not video_element:
                    continue

                link_element = await tweet.query_selector('a[href*="/status/"]')
                if not link_element:
                    continue

                tweet_path = await link_element.get_attribute('href')
                if not tweet_path or '/status/' not in tweet_path:
                    continue

                normalized_tweet = normalize_tweet_url(tweet_path)
                if not normalized_tweet:
                    continue

                tweet_id, tweet_url = normalized_tweet
                if tweet_id in seen_tweets:
                    continue
                seen_tweets.add(tweet_id)
                if tweet_id in processed_tweet_ids:
                    continue

                text_element = await tweet.query_selector('[data-testid="tweetText"]')
                description = await text_element.inner_text() if text_element else "Sem descrição"

                author_element = await tweet.query_selector('[data-testid="User-Name"]')
                author_text = await author_element.inner_text() if author_element else "unknown"
                uploader = author_text.split('\n')[1].replace('@', '') if '\n' in author_text else "unknown"

                print(f"Vídeo encontrado: {tweet_url}")
                if download_video(tweet_url, description, uploader, tweet_id):
                    found_count += 1
                    print(f"Progresso: {found_count}/{max_videos}")

            if found_count >= max_videos:
                break

            await page.mouse.wheel(0, 2200)
            await page.wait_for_timeout(2500)
        
        if found_count == 0:
            if seen_tweets:
                print(
                    "Nenhum video foi baixado com sucesso "
                    f"({len(seen_tweets)} tentativa(s))."
                )
            else:
                print("Nenhum vídeo visível na timeline no momento.")
            # Verificar se o login realmente funcionou
            if "login" in page.url:
                print("AVISO: Parece que você não está logado. Verifique se seus cookies são válidos.")
        elif found_count < max_videos:
            print(f"Finalizado com {found_count} vídeo(s). Não encontrei {max_videos} vídeos dentro do limite de rolagem.")
        else:
            print(f"Finalizado com sucesso: {found_count} vídeos baixados.")
            
        await browser.close()
        return 0 if found_count > 0 else 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Baixa videos novos da timeline do X/Twitter.")
    parser.add_argument("--max-videos", type=int, default=MAX_VIDEOS)
    parser.add_argument("--state-file", default=".automation_state.json")
    cli_args = parser.parse_args()
    if cli_args.max_videos < 1:
        parser.error("--max-videos deve ser 1 ou maior.")
    sys.exit(asyncio.run(main(cli_args.max_videos, cli_args.state_file)))
