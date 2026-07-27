"""Bot do Telegram: manda um link ou um video, recebe o Reel pronto.

Fluxo: link/video -> baixa -> escolhe personagem -> monta o video -> gera a
legenda com o Gemini -> devolve no chat com botoes. Nada e publicado sem o
seu "Publicar".

Rode com:  python telegram_bot.py
"""

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib import error, parse, request

import characters as character_lib
import nethelp
from caption_generator import generate_caption, generate_hook, load_dotenv

TELEGRAM_API = "https://api.telegram.org"
JOBS_FILENAME = ".telegram_jobs.json"
DOWNLOAD_DIRNAME = "telegram_downloads"
OUTPUT_DIRNAME = "outputs_ig"
POLL_TIMEOUT = 50
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # limite do getFile na Bot API
IG_SESSION_FILENAME = ".ig_session.json"
GUESTS_FILENAME = ".allowed_users.json"
URL_RE = re.compile(r"https?://\S+")


# --------------------------------------------------------------------------
# Camada HTTP da Bot API
# --------------------------------------------------------------------------

def api_url(token: str, method: str) -> str:
    return f"{TELEGRAM_API}/bot{token}/{method}"


def api_call(token: str, method: str, payload: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload or {}).encode("utf-8")
    req = request.Request(
        url=api_url(token, method),
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")[:400]
        raise RuntimeError(f"Telegram HTTP {exc.code} em {method}: {raw}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Telegram indisponivel em {method}: {exc.reason}") from exc


def api_upload(token: str, method: str, fields: dict, file_field: str, file_path: Path,
               timeout: int = 300) -> dict:
    """POST multipart, para enviar o arquivo de video."""
    boundary = f"----crvBoundary{uuid.uuid4().hex}"
    body = bytearray()

    for key, value in fields.items():
        if value is None:
            continue
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.extend(f"{value}\r\n".encode("utf-8"))

    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{file_path.name}"\r\n'.encode()
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
    body.extend(file_path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    req = request.Request(
        url=api_url(token, method),
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")[:400]
        raise RuntimeError(f"Telegram HTTP {exc.code} em {method}: {raw}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Telegram indisponivel em {method}: {exc.reason}") from exc


def send_message(token: str, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return api_call(token, "sendMessage", payload)


def probe_video(video: Path, python_bin: str) -> dict:
    """Dimensoes e duracao do arquivo, para o Telegram nao ter que adivinhar."""
    ffprobe = Path(python_bin).parent / "ffprobe"
    cmd = [
        str(ffprobe), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(video),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        return {
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
            "duration": int(float((data.get("format") or {}).get("duration") or 0)),
        }
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        return {}


def send_video(token: str, chat_id: int, video: Path, caption: str,
               reply_markup: dict | None = None, python_bin: str = sys.executable) -> dict:
    fields = {
        "chat_id": str(chat_id),
        "caption": caption[:TELEGRAM_CAPTION_LIMIT],
        "supports_streaming": "true",
    }
    # Sem width/height o cliente do Telegram chuta o enquadramento e mostra o
    # Reel achatado, mesmo com o arquivo em 9:16 correto.
    info = probe_video(video, python_bin)
    for key in ("width", "height", "duration"):
        if info.get(key):
            fields[key] = str(info[key])
    if reply_markup:
        fields["reply_markup"] = json.dumps(reply_markup)
    return api_upload(token, "sendVideo", fields, "video", video)


def answer_callback(token: str, callback_id: str, text: str = "") -> dict:
    return api_call(token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def download_telegram_file(token: str, file_id: str, dest_dir: Path) -> Path:
    info = api_call(token, "getFile", {"file_id": file_id})
    if not info.get("ok"):
        raise RuntimeError(f"getFile falhou: {info}")
    result = info["result"]
    size = result.get("file_size") or 0
    if size > TELEGRAM_DOWNLOAD_LIMIT:
        raise RuntimeError(
            f"Arquivo de {size / 1024 / 1024:.0f} MB. A Bot API do Telegram so "
            f"entrega ate {TELEGRAM_DOWNLOAD_LIMIT // 1024 // 1024} MB. "
            "Manda o link em vez do arquivo."
        )

    remote_path = result["file_path"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(remote_path).suffix or ".mp4"
    dest = dest_dir / f"tg_{uuid.uuid4().hex[:12]}{suffix}"
    url = f"{TELEGRAM_API}/file/bot{token}/{remote_path}"
    with request.urlopen(url, timeout=300) as resp, dest.open("wb") as handle:
        shutil.copyfileobj(resp, handle)
    return dest


# --------------------------------------------------------------------------
# Download de link
# --------------------------------------------------------------------------

def cookies_json_to_netscape(cookies_json: Path, dest: Path) -> Path | None:
    """Converte o cookies.json do scraper para o formato que o yt-dlp aceita.

    O projeto guarda os cookies como objeto {nome: valor} (twikit) ou como
    lista de objetos (extensao de navegador); o yt-dlp quer Netscape.
    """
    try:
        data = json.loads(cookies_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if isinstance(data, dict):
        pairs = [(str(k), str(v)) for k, v in data.items()]
    elif isinstance(data, list):
        pairs = [
            (str(c.get("name")), str(c.get("value")))
            for c in data
            if isinstance(c, dict) and c.get("name") and c.get("value") is not None
        ]
    else:
        return None

    if not pairs:
        return None

    expiry = int(time.time()) + 365 * 24 * 3600
    lines = ["# Netscape HTTP Cookie File"]
    for name, value in pairs:
        lines.append("\t".join([".x.com", "TRUE", "/", "TRUE", str(expiry), name, value]))
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def download_from_url(url: str, dest_dir: Path, project_dir: Path, python_bin: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = f"tg_{uuid.uuid4().hex[:12]}"
    template = str(dest_dir / f"{stem}.%(ext)s")

    cmd = [
        python_bin, "-m", "yt_dlp",
        "--no-playlist",
        "--quiet", "--no-warnings",
        "-f", "bestvideo+bestaudio/best",
        "-o", template,
    ]

    cookies_json = project_dir / os.getenv("TWITTER_COOKIES_FILE", "cookies.json")
    if cookies_json.is_file():
        netscape = cookies_json_to_netscape(cookies_json, dest_dir / f"{stem}_cookies.txt")
        if netscape:
            cmd.extend(["--cookies", str(netscape)])

    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(project_dir))

    for leftover in dest_dir.glob(f"{stem}_cookies.txt"):
        leftover.unlink(missing_ok=True)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:300]
        raise RuntimeError(f"Download falhou: {detail}")

    produced = [p for p in dest_dir.glob(f"{stem}.*") if p.suffix.lower() != ".txt"]
    if not produced:
        raise RuntimeError("Download terminou sem gerar arquivo de video.")
    return max(produced, key=lambda p: p.stat().st_size)


def fetch_url_description(url: str, project_dir: Path, python_bin: str) -> str:
    """Pega titulo/descricao do post, que viram contexto do hook e da legenda."""
    cmd = [python_bin, "-m", "yt_dlp", "--skip-download", "--no-warnings",
           "--print", "%(description)s|||%(title)s", url]
    cookies_json = project_dir / os.getenv("TWITTER_COOKIES_FILE", "cookies.json")
    tmp_cookie = None
    if cookies_json.is_file():
        tmp_cookie = cookies_json_to_netscape(cookies_json, project_dir / f".tg_cookies_{uuid.uuid4().hex[:6]}.txt")
        if tmp_cookie:
            cmd.extend(["--cookies", str(tmp_cookie)])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(project_dir), timeout=90)
    except subprocess.TimeoutExpired:
        return ""
    finally:
        if tmp_cookie:
            Path(tmp_cookie).unlink(missing_ok=True)

    if result.returncode != 0:
        return ""
    raw = (result.stdout or "").strip()
    description, _, title = raw.partition("|||")
    for value in (description, title):
        cleaned = value.strip()
        if cleaned and cleaned.upper() != "NA":
            return cleaned
    return ""


# --------------------------------------------------------------------------
# Estado dos jobs pendentes
# --------------------------------------------------------------------------

def guests_path(project_dir: Path) -> Path:
    return Path(project_dir) / GUESTS_FILENAME


def load_guests(project_dir: Path) -> set[int]:
    """Convidados liberados pelo dono via /autorizar (admins ficam no .env)."""
    path = guests_path(project_dir)
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(i) for i in data.get("guests", [])}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return set()


def save_guests(project_dir: Path, guests: set[int]) -> None:
    guests_path(project_dir).write_text(
        json.dumps({"guests": sorted(guests)}, indent=2), encoding="utf-8"
    )


def jobs_path(project_dir: Path) -> Path:
    return Path(project_dir) / JOBS_FILENAME


def load_jobs(project_dir: Path) -> dict:
    path = jobs_path(project_dir)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_jobs(project_dir: Path, jobs: dict) -> None:
    jobs_path(project_dir).write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# Montagem
# --------------------------------------------------------------------------

def compose_video(
    project_dir: Path,
    python_bin: str,
    source_video: Path,
    character: Path,
    output: Path,
    hook_text: str = "",
) -> None:
    cmd = [
        python_bin, "compose_test_video.py",
        "--video", str(source_video),
        "--avatar", str(character),
        "--text-box-opacity", "0.0",
        "--output", str(output),
    ]
    if hook_text:
        cmd.extend(["--hook-text", hook_text])

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(project_dir))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-400:]
        raise RuntimeError(f"Composicao falhou: {detail}")
    if not output.is_file():
        raise RuntimeError("Composicao terminou sem gerar o arquivo final.")


def build_caption_for(context_text: str, hashtags_max: int) -> tuple[str, str]:
    try:
        from instagram_graph_publisher import build_viral_hashtags, detect_topics

        hashtags = build_viral_hashtags(detect_topics(context_text), hashtags_max)
    except ImportError:
        hashtags = ""
    return generate_caption(
        context_text=context_text,
        profile_focus=os.getenv(
            "PROFILE_FOCUS", "programacao, tecnologia e IA com exemplos praticos"
        ),
        hashtags=hashtags,
    )


def parse_cookie_payload(raw: str) -> dict[str, str]:
    """Aceita cookies em JSON ({nome: valor} ou export de extensao), Netscape
    ou "nome=valor; nome2=valor2" e devolve sempre {nome: valor}."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items() if v is not None}
    if isinstance(data, list):
        return {
            str(c["name"]): str(c.get("value", ""))
            for c in data
            if isinstance(c, dict) and c.get("name")
        }

    cookies: dict[str, str] = {}
    if "\t" in raw:
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) >= 7 and not line.lstrip().startswith("#"):
                cookies[parts[5].strip()] = parts[6].strip()
    else:
        for chunk in re.split(r";\s*|\n", raw):
            name, sep, value = chunk.partition("=")
            if sep and name.strip():
                cookies[name.strip()] = value.strip()
    return cookies


SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
REEL_STEPS = [
    "Baixando o video",
    "Criando o hook",
    "Montando o video",
    "Escrevendo a legenda",
    "Enviando",
]


class ProgressMessage:
    """Uma unica mensagem que mostra a lista de etapas se completando.

    Etapa concluida ganha check, a atual gira um spinner, as futuras ficam
    apagadas. Uma thread reedita a mensagem de tempos em tempos so para o
    spinner girar, entao o chat continua vivo durante o render, que e a
    parte longa.
    """

    TICK_SECONDS = 2.5

    def __init__(self, token: str, chat_id: int, title: str = "Montando seu Reel",
                 steps: list[str] | None = None):
        self.token = token
        self.chat_id = chat_id
        self.title = title
        self.steps = list(steps or REEL_STEPS)
        self.index = -1
        self.message_id = None
        self._frame = 0
        self._last_text = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _render(self) -> str:
        spin = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        lines = [f"🎬 {self.title}", ""]
        for position, step in enumerate(self.steps):
            if position < self.index:
                lines.append(f"✅ {step}")
            elif position == self.index:
                lines.append(f"{spin} {step}...")
            else:
                lines.append(f"▫️ {step}")
        return "\n".join(lines)

    def _edit(self, text: str) -> None:
        if self.message_id is None or text == self._last_text:
            return
        self._last_text = text
        try:
            api_call(self.token, "editMessageText", {
                "chat_id": self.chat_id, "message_id": self.message_id, "text": text,
            })
        except RuntimeError:
            pass  # progresso e cosmetico: falha de edicao nunca para o fluxo

    def _tick_loop(self) -> None:
        while not self._stop.wait(self.TICK_SECONDS):
            with self._lock:
                self._frame += 1
                text = self._render()
            self._edit(text)

    def stage(self, index: int) -> None:
        """Marca a etapa `index` como a atual; as anteriores ficam concluidas."""
        with self._lock:
            self.index = index
            self._frame += 1
            text = self._render()
        if self.message_id is None:
            try:
                resp = send_message(self.token, self.chat_id, text)
            except RuntimeError:
                return
            self.message_id = (resp.get("result") or {}).get("message_id")
            self._last_text = text
            threading.Thread(target=self._tick_loop, daemon=True).start()
        else:
            self._edit(text)

    def done(self, label: str) -> None:
        self._stop.set()
        with self._lock:
            self.index = len(self.steps)
            body = self._render()
        self._edit(f"{body}\n\n✨ {label}")

    def fail(self, text: str) -> None:
        self._stop.set()
        message = f"⚠️ {text}"
        if self.message_id is not None:
            self._edit(message)
        else:
            try:
                send_message(self.token, self.chat_id, message)
            except RuntimeError:
                pass


def job_keyboard(job_id: str, has_alternatives: bool) -> dict:
    rows = [[
        {"text": "🚀 Publicar", "callback_data": f"pub:{job_id}"},
        {"text": "🗑 Descartar", "callback_data": f"del:{job_id}"},
    ]]
    if has_alternatives:
        rows.insert(0, [{"text": "🎭 Trocar personagem", "callback_data": f"chg:{job_id}"}])
    return {"inline_keyboard": rows}


def menu_keyboard(is_admin: bool) -> dict:
    rows = [[
        {"text": "🎭 Personagens", "callback_data": "menu:personagens"},
        {"text": "❓ Ajuda", "callback_data": "menu:ajuda"},
    ]]
    if is_admin:
        rows.append([
            {"text": "👥 Usuarios", "callback_data": "menu:usuarios"},
            {"text": "📷 Conta", "callback_data": "menu:conta"},
        ])
    return {"inline_keyboard": rows}


class Bot:
    def __init__(self, token: str, project_dir: Path, admins: set[int], python_bin: str,
                 hashtags_max: int = 10, guests_can_publish: bool = False):
        self.token = token
        self.project_dir = Path(project_dir)
        # Admins vem do .env e mandam no bot; convidados sao geridos no chat e
        # nao podem se promover nem mexer na conta do Instagram.
        self.admins = admins
        self.guests = load_guests(self.project_dir)
        self.guests_can_publish = guests_can_publish
        self.use_saved_music = os.getenv("IG_USE_SAVED_MUSIC", "1").strip() not in {
            "0", "false", "no", "nao"
        }
        self.python_bin = python_bin
        self.hashtags_max = hashtags_max
        self.downloads = self.project_dir / DOWNLOAD_DIRNAME
        self.outputs = self.project_dir / OUTPUT_DIRNAME
        self.jobs = load_jobs(self.project_dir)
        # chat_id -> nome do personagem aguardando o video chegar
        self.pending_character: dict[int, str] = {}
        # chats que pediram /cookies e ainda vao mandar o JSON
        self.pending_cookies: set[int] = set()
        # chats que pediram /cookies e vao mandar o arquivo/texto em seguida
        self.pending_cookies: set[int] = set()

    # -- helpers ----------------------------------------------------------
    def say(self, chat_id: int, text: str, markup: dict | None = None) -> None:
        try:
            send_message(self.token, chat_id, text, markup)
        except RuntimeError as exc:
            print(f"Falha ao responder no chat {chat_id}: {exc}", file=sys.stderr)

    @property
    def allowed(self) -> set[int]:
        return self.admins | self.guests

    def authorized(self, user_id: int | None) -> bool:
        return bool(user_id) and user_id in self.allowed

    def is_admin(self, user_id: int | None) -> bool:
        return bool(user_id) and user_id in self.admins

    def welcome_text(self, user_id: int | None) -> str:
        character = character_lib.pick_character(self.project_dir)
        atual = character_lib.character_label(character) if character else "nenhum"
        conta = "conectada" if (self.project_dir / IG_SESSION_FILENAME).is_file() else "nao conectada"
        linhas = [
            "🎬 *Reel Maker*",
            "",
            "Manda um *link* de video ou o *arquivo* que eu monto o Reel:",
            "conteudo em cima, faixa com o gancho no meio, personagem embaixo.",
            "",
            f"🎭 Personagem: {atual}",
        ]
        if self.is_admin(user_id):
            linhas.append(f"📷 Instagram: {conta}")
        linhas += ["", "Use os botoes abaixo ou /ajuda para ver tudo."]
        return "\n".join(linhas).replace("*", "")

    def help_text(self, is_admin: bool) -> str:
        linhas = [
            "❓ Como usar",
            "",
            "Manda um link ou um arquivo de video. Quando o Reel ficar pronto,",
            "voce recebe com os botoes Publicar, Trocar personagem e Descartar.",
            "",
            "🎭 Personagens",
            "/personagens — lista a biblioteca",
        ]
        if is_admin:
            linhas += [
                "/personagem <nome> — fixa o personagem padrao",
                "/novopersonagem <nome> — adiciona um personagem ou uma variacao:",
                "   manda o comando e depois o video (ou o video ja com o",
                "   comando na legenda). Com o mesmo nome, os videos viram",
                "   variacoes sorteadas a cada Reel",
                "",
                "👥 Usuarios",
                "/usuarios — quem pode usar o bot",
                "/autorizar <id> — libera alguem (a pessoa manda /id para descobrir o dela)",
                "/remover <id> — tira o acesso",
                "",
                "📷 Instagram",
                "/login <usuario> <senha> — conecta a conta (a mensagem e apagada)",
                "/cookies — conecta pelos cookies do navegador (JSON com o sessionid)",
                "/logout — desconecta",
                "",
                "🎵 Musica",
                "/musicas — lista os audios salvos na sua conta",
                "/musica on|off — usar (ou nao) uma musica salva no Reel",
            ]
        linhas += ["", "/id — mostra seu id do Telegram"]
        return "\n".join(linhas)

    def describe_users(self) -> str:
        lines = [f"- {uid} (dono)" for uid in sorted(self.admins)]
        lines += [f"- {uid}" for uid in sorted(self.guests)]
        if not lines:
            return "Ninguem autorizado."
        extra = "" if self.guests_can_publish else "\n\nConvidados montam Reels, mas nao publicam."
        return "Quem pode usar o bot:\n" + "\n".join(lines) + extra

    # -- pipeline ---------------------------------------------------------
    def process_source(self, chat_id: int, source_video: Path, context_text: str,
                       character_name: str | None = None, exclude: Path | None = None,
                       progress: ProgressMessage | None = None) -> None:
        progress = progress or ProgressMessage(self.token, chat_id)
        character = character_lib.pick_character(
            self.project_dir, name=character_name, exclude=exclude
        )
        if character is None:
            progress.fail("Nenhum personagem disponivel. Coloque videos em characters/.")
            return

        label = character_lib.character_label(character)

        # Hook da faixa preta: gerado do conteudo real. Vazio deixa o
        # compositor cair nas frases fixas dele.
        progress.stage(1)
        hook, _ = generate_hook(
            context_text,
            profile_focus=os.getenv(
                "PROFILE_FOCUS", "programacao, tecnologia e IA com exemplos praticos"
            ),
        )

        self.outputs.mkdir(parents=True, exist_ok=True)
        output = self.outputs / f"tg_{uuid.uuid4().hex[:12]}_final.mp4"
        progress.stage(2)
        try:
            compose_video(
                self.project_dir, self.python_bin, source_video, character, output,
                hook_text=hook,
            )
        except RuntimeError as exc:
            progress.fail(f"Nao consegui montar o video.\n{exc}")
            return

        progress.stage(3)
        caption, origin = build_caption_for(context_text, self.hashtags_max)

        job_id = uuid.uuid4().hex[:10]
        self.jobs[job_id] = {
            "chat_id": chat_id,
            "video": str(output),
            "source_video": str(source_video),
            "caption": caption,
            "context": context_text,
            "character": str(character),
            "created": time.time(),
        }
        save_jobs(self.project_dir, self.jobs)

        alternatives = (
            len(character_lib.list_characters(self.project_dir)) > 1
            or len(character_lib.variants(
                character_lib.entry_for(self.project_dir, character)
            )) > 1
        )
        progress.stage(4)
        try:
            send_video(
                self.token, chat_id, output,
                caption=f"Personagem: {label}  |  legenda via {origin}",
                reply_markup=job_keyboard(job_id, alternatives),
                python_bin=self.python_bin,
            )
        except RuntimeError as exc:
            progress.fail(f"Video montado em {output.name}, mas o envio falhou: {exc}")
            return

        progress.done("Reel pronto 👆")
        self.say(chat_id, f"Legenda que vai no post:\n\n{caption}")

    def handle_url(self, chat_id: int, url: str) -> None:
        progress = ProgressMessage(self.token, chat_id)
        progress.stage(0)
        try:
            source = download_from_url(url, self.downloads, self.project_dir, self.python_bin)
        except RuntimeError as exc:
            progress.fail(f"{exc}\n\nSe for post privado, confira o cookies.json.")
            return
        progress.stage(0)
        context = fetch_url_description(url, self.project_dir, self.python_bin)
        self.process_source(chat_id, source, context, progress=progress)

    def handle_video_file(self, chat_id: int, file_id: str, caption_text: str) -> None:
        progress = ProgressMessage(self.token, chat_id)
        progress.stage(0)
        try:
            source = download_telegram_file(self.token, file_id, self.downloads)
        except RuntimeError as exc:
            progress.fail(str(exc))
            return
        self.process_source(chat_id, source, caption_text or "", progress=progress)

    def save_character_video(self, chat_id: int, file_id: str, name: str) -> None:
        clean = re.sub(r"[^a-z0-9_-]+", "_", name.strip().lower()).strip("_")
        if not clean:
            self.say(chat_id, "Nome invalido. Uso: /novopersonagem <nome>")
            return

        self.say(chat_id, f"Baixando o video do personagem '{clean}'...")
        try:
            temp = download_telegram_file(self.token, file_id, self.downloads)
        except RuntimeError as exc:
            self.say(chat_id, str(exc))
            return

        base = character_lib.characters_dir(self.project_dir)
        base.mkdir(parents=True, exist_ok=True)
        suffix = temp.suffix.lower()
        if suffix not in character_lib.MEDIA_EXTENSIONS:
            suffix = ".mp4"

        folder = base / clean
        loose = [p for p in base.glob(f"{clean}.*") if p.is_file()]
        if folder.is_dir() or loose:
            # Personagem ja existe: vira (ou ja e) pasta e o video novo entra
            # como variacao, sorteada a cada render junto com as antigas.
            folder.mkdir(exist_ok=True)
            for old in loose:
                shutil.move(str(old), folder / f"{clean}_1{old.suffix.lower()}")
            seq = 1
            while any(folder.glob(f"{clean}_{seq}.*")):
                seq += 1
            shutil.move(str(temp), folder / f"{clean}_{seq}{suffix}")
            total = len(character_lib.variants(folder))
            headline = (
                f"Variacao adicionada: '{clean}' agora tem {total} videos, "
                "sorteados a cada Reel."
            )
        else:
            shutil.move(str(temp), base / f"{clean}{suffix}")
            headline = f"Personagem '{clean}' adicionado."

        self.say(
            chat_id,
            f"{headline}\n\nBiblioteca:\n"
            f"{character_lib.describe_library(self.project_dir)}",
        )

    # -- publicacao -------------------------------------------------------
    def _delete_message(self, chat_id: int, message_id: int | None) -> None:
        if not message_id:
            return
        try:
            api_call(self.token, "deleteMessage",
                     {"chat_id": chat_id, "message_id": message_id})
        except RuntimeError:
            pass

    def instagram_login_cookies(self, chat_id: int, cookies_json: str) -> None:
        progress = ProgressMessage(self.token, chat_id, "Conectando o Instagram",
                                   ["Validando os cookies", "Abrindo a sessao"])
        progress.stage(0)
        result = subprocess.run(
            [self.python_bin, "instagrapi_publisher.py", "--login-cookies"],
            input=cookies_json,
            capture_output=True, text=True, cwd=str(self.project_dir),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-400:]
            progress.fail(f"Login com cookies falhou.\n{detail}")
            return
        lines = (result.stdout or "").strip().splitlines()
        who = lines[-1] if lines else "Logado."
        progress.done(f"{who} — o botao Publicar agora posta direto pela sua conta.")

    def instagram_login(self, chat_id: int, username: str, password: str) -> None:
        progress = ProgressMessage(self.token, chat_id, "Conectando o Instagram",
                                   ["Enviando as credenciais", "Abrindo a sessao"])
        progress.stage(0)
        # Credenciais via stdin: linha de comando vazaria a senha no `ps`.
        result = subprocess.run(
            [self.python_bin, "instagrapi_publisher.py", "--login"],
            input=f"{username}\n{password}\n",
            capture_output=True, text=True, cwd=str(self.project_dir),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-400:]
            progress.fail(f"Login falhou.\n{detail}")
            return
        lines = (result.stdout or "").strip().splitlines()
        who = lines[-1] if lines else "Logado."
        progress.done(f"{who} — o botao Publicar agora posta direto pela sua conta.")

    def publish(self, chat_id: int, job: dict) -> None:
        video = Path(job["video"])
        if not video.is_file():
            self.say(chat_id, "O arquivo do video sumiu. Manda o link de novo.")
            return

        progress = ProgressMessage(self.token, chat_id, "Publicando no Instagram",
                                   ["Enviando o video", "Finalizando o post"])
        progress.stage(0)
        session = self.project_dir / IG_SESSION_FILENAME
        if session.is_file():
            # Sessao de usuario/senha (instagrapi) tem prioridade sobre a Graph API.
            cmd = [
                self.python_bin, "instagrapi_publisher.py",
                "--publish", str(video),
                "--caption", job["caption"],
            ]
            if self.use_saved_music:
                cmd.append("--music")
        else:
            cmd = [
                self.python_bin, "instagram_graph_publisher.py",
                "--from-dir", str(video.parent),
                "--file-name", video.name,
                "--publish-now",
                "--caption", job["caption"],
            ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.project_dir))
        output = (result.stdout or "").strip()
        if result.returncode != 0:
            detail = (result.stderr or output).strip()[-500:]
            progress.fail(f"Publicacao falhou.\n{detail}")
            return

        post_line = next(
            (line for line in output.splitlines() if "Post ID" in line), "Publicado."
        )
        progress.done(f"Pronto. {post_line}")

    # -- callbacks --------------------------------------------------------
    def handle_menu(self, chat_id: int, user_id: int | None, item: str) -> None:
        admin = self.is_admin(user_id)
        if item == "personagens":
            self.say(chat_id, "🎭 Biblioteca\n\n"
                     + character_lib.describe_library(self.project_dir))
        elif item == "ajuda":
            self.say(chat_id, self.help_text(admin), menu_keyboard(admin))
        elif item == "usuarios" and admin:
            self.say(chat_id, "👥 " + self.describe_users())
        elif item == "conta" and admin:
            result = subprocess.run(
                [self.python_bin, "instagrapi_publisher.py", "--whoami"],
                capture_output=True, text=True, cwd=str(self.project_dir),
            )
            who = (result.stdout or "").strip()
            self.say(chat_id, f"📷 Instagram: {who}" if result.returncode == 0
                     else "📷 Nenhuma conta conectada. Use /login ou /cookies.")
        else:
            self.say(chat_id, "Opcao indisponivel.")

    def handle_callback(self, callback: dict) -> None:
        cb_id = callback.get("id")
        user_id = (callback.get("from") or {}).get("id")
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        data = callback.get("data") or ""

        if not self.authorized(user_id):
            answer_callback(self.token, cb_id, "Nao autorizado.")
            return

        action, _, job_id = data.partition(":")

        if action == "menu":
            answer_callback(self.token, cb_id)
            self.handle_menu(chat_id, user_id, job_id)
            return

        job = self.jobs.get(job_id)
        if not job:
            answer_callback(self.token, cb_id, "Esse job expirou.")
            return

        if action == "pub":
            if not (self.is_admin(user_id) or self.guests_can_publish):
                answer_callback(self.token, cb_id, "So o dono publica.")
                self.say(chat_id, "Voce pode montar Reels, mas a publicacao e do dono do bot.")
                return
            answer_callback(self.token, cb_id, "Publicando...")
            self.publish(chat_id, job)
            self.jobs.pop(job_id, None)
            save_jobs(self.project_dir, self.jobs)
        elif action == "del":
            answer_callback(self.token, cb_id, "Descartado.")
            Path(job["video"]).unlink(missing_ok=True)
            self.jobs.pop(job_id, None)
            save_jobs(self.project_dir, self.jobs)
            self.say(chat_id, "Descartado.")
        elif action == "chg":
            answer_callback(self.token, cb_id, "Trocando personagem...")
            source = Path(job["source_video"])
            if not source.is_file():
                self.say(chat_id, "O video de origem sumiu. Manda o link de novo.")
                return
            Path(job["video"]).unlink(missing_ok=True)
            self.jobs.pop(job_id, None)
            save_jobs(self.project_dir, self.jobs)
            self.process_source(
                chat_id, source, job.get("context", ""), exclude=Path(job["character"])
            )
        else:
            answer_callback(self.token, cb_id, "Acao desconhecida.")

    # -- mensagens --------------------------------------------------------
    def handle_message(self, message: dict) -> None:
        chat_id = (message.get("chat") or {}).get("id")
        user_id = (message.get("from") or {}).get("id")
        text = (message.get("text") or "").strip()

        if not self.authorized(user_id):
            # Sem vazar nada: so informa o id, para o dono se autorizar.
            self.say(
                chat_id,
                f"Nao autorizado.\nSeu id do Telegram e: {user_id}\n"
                "Adicione em TELEGRAM_ALLOWED_USER_IDS no .env.",
            )
            return

        if text.startswith("/start"):
            self.say(chat_id, self.welcome_text(user_id),
                     menu_keyboard(self.is_admin(user_id)))
            return
        if text.startswith("/ajuda") or text.startswith("/help"):
            self.say(chat_id, self.help_text(self.is_admin(user_id)),
                     menu_keyboard(self.is_admin(user_id)))
            return
        if text.startswith("/id"):
            self.say(chat_id, f"Seu id: {user_id}")
            return

        # -- comandos so do dono ------------------------------------------
        admin_only = (
            "/autorizar", "/remover", "/usuarios", "/login", "/cookies",
            "/logout", "/novopersonagem", "/addpersonagem", "/personagem ",
            "/musica", "/musicas",
        )
        if text.startswith(admin_only) or text.strip() == "/personagem":
            if not self.is_admin(user_id):
                self.say(chat_id, "Esse comando e so do dono do bot.")
                return

        if text.startswith("/usuarios"):
            self.say(chat_id, self.describe_users())
            return
        if text.startswith("/musicas"):
            result = subprocess.run(
                [self.python_bin, "instagrapi_publisher.py", "--list-music"],
                capture_output=True, text=True, cwd=str(self.project_dir),
            )
            listagem = (result.stdout or result.stderr or "").strip()
            estado = "ligada" if self.use_saved_music else "desligada"
            self.say(chat_id, f"🎵 Musica no Reel: {estado}\n\nSalvas na conta:\n{listagem}\n\n"
                              "/musica on|off liga ou desliga.")
            return
        if text.startswith("/musica"):
            escolha = text.partition(" ")[2].strip().lower()
            if escolha in {"on", "ligar", "sim"}:
                self.use_saved_music = True
            elif escolha in {"off", "desligar", "nao"}:
                self.use_saved_music = False
            else:
                self.say(chat_id, "Uso: /musica on  ou  /musica off")
                return
            estado = "ligada" if self.use_saved_music else "desligada"
            self.say(chat_id, f"🎵 Musica no Reel: {estado}.")
            return
        if text.startswith("/autorizar"):
            wanted = text.partition(" ")[2].strip()
            if not wanted.lstrip("-").isdigit():
                self.say(chat_id, "Uso: /autorizar <id do telegram>\n"
                                  "A pessoa descobre o id dela mandando /id para o bot.")
                return
            new_id = int(wanted)
            if new_id in self.allowed:
                self.say(chat_id, f"{new_id} ja podia usar o bot.")
                return
            self.guests.add(new_id)
            save_guests(self.project_dir, self.guests)
            self.say(chat_id, f"{new_id} autorizado.\n\n{self.describe_users()}")
            return
        if text.startswith("/remover"):
            wanted = text.partition(" ")[2].strip()
            if not wanted.lstrip("-").isdigit():
                self.say(chat_id, "Uso: /remover <id do telegram>")
                return
            old_id = int(wanted)
            if old_id in self.admins:
                self.say(chat_id, "Esse id e dono do bot: so da para tirar "
                                  "no TELEGRAM_ADMIN_IDS do .env.")
                return
            if old_id not in self.guests:
                self.say(chat_id, f"{old_id} nao estava autorizado.")
                return
            self.guests.discard(old_id)
            save_guests(self.project_dir, self.guests)
            self.say(chat_id, f"{old_id} removido.\n\n{self.describe_users()}")
            return
        if text.startswith("/logout"):
            session = self.project_dir / IG_SESSION_FILENAME
            if session.is_file():
                session.unlink()
                self.say(chat_id, "Sessao do Instagram apagada. Publicacao volta a exigir /login.")
            else:
                self.say(chat_id, "Nao havia sessao do Instagram salva.")
            return
        if text.startswith("/login"):
            # A senha nao deve ficar no historico: apaga a mensagem primeiro.
            self._delete_message(chat_id, message.get("message_id"))
            parts = text.split()
            if len(parts) != 3:
                self.say(chat_id, "Uso: /login <usuario> <senha>\n"
                                  "A mensagem com a senha e apagada do chat em seguida.")
                return
            self.instagram_login(chat_id, parts[1], parts[2])
            return
        if text.startswith("/cookies"):
            # sessionid e credencial: a mensagem sai do historico como no /login
            self._delete_message(chat_id, message.get("message_id"))
            payload = text.partition(" ")[2].strip()
            if payload:
                self.instagram_login_cookies(chat_id, payload)
            else:
                self.pending_cookies.add(chat_id)
                self.say(
                    chat_id,
                    "Manda agora o JSON dos cookies — como texto ou arquivo .json.\n"
                    "Precisa conter o cookie 'sessionid' do instagram.com "
                    "(DevTools > Application > Cookies, ou uma extensao de exportar cookies).",
                )
            return
        if chat_id in self.pending_cookies and text.startswith(("{", "[")):
            self.pending_cookies.discard(chat_id)
            self._delete_message(chat_id, message.get("message_id"))
            self.instagram_login_cookies(chat_id, text)
            return
        if text.startswith("/personagens"):
            self.say(chat_id, character_lib.describe_library(self.project_dir))
            return
        if text.startswith("/personagem"):
            wanted = text.partition(" ")[2].strip()
            if not wanted:
                self.say(chat_id, "Uso: /personagem <nome>")
                return
            chosen = character_lib.resolve_character(self.project_dir, wanted)
            if not chosen:
                self.say(chat_id, f"Nao achei '{wanted}'.\n\n{character_lib.describe_library(self.project_dir)}")
                return
            character_lib.save_default_character(self.project_dir, chosen)
            self.say(chat_id, f"Personagem padrao: {character_lib.character_label(chosen)}")
            return
        if text.startswith("/novopersonagem") or text.startswith("/addpersonagem"):
            wanted = text.partition(" ")[2].strip()
            if not wanted:
                self.say(chat_id, "Uso: /novopersonagem <nome>\nDepois manda o video (ate 20 MB).")
                return
            self.pending_character[chat_id] = wanted
            self.say(
                chat_id,
                f"Beleza. Agora manda o video do personagem '{wanted}' (ate 20 MB).",
            )
            return

        video = message.get("video") or message.get("document")
        if video and video.get("file_id"):
            doc_name = (video.get("file_name") or "").lower()
            if doc_name.endswith(".json") or (
                chat_id in self.pending_cookies
                and "json" in (video.get("mime_type") or "")
            ):
                self.pending_cookies.discard(chat_id)
                self._delete_message(chat_id, message.get("message_id"))
                try:
                    path = download_telegram_file(self.token, video["file_id"], self.downloads)
                except RuntimeError as exc:
                    self.say(chat_id, str(exc))
                    return
                payload = path.read_text(encoding="utf-8", errors="ignore")
                path.unlink(missing_ok=True)
                self.instagram_login_cookies(chat_id, payload)
                return
            caption_text = (message.get("caption") or "").strip()
            cap_cmd = re.match(
                r"/(?:novo|add)personagem\s+(.+)", caption_text, re.IGNORECASE
            )
            pending = self.pending_character.pop(chat_id, None)
            if cap_cmd:
                self.save_character_video(chat_id, video["file_id"], cap_cmd.group(1))
            elif pending:
                self.save_character_video(chat_id, video["file_id"], pending)
            else:
                self.handle_video_file(chat_id, video["file_id"], caption_text)
            return

        match = URL_RE.search(text)
        if match:
            self.handle_url(chat_id, match.group(0))
            return

        self.say(chat_id, "Manda um link de video ou o arquivo. /ajuda mostra os comandos.")

    # -- loop -------------------------------------------------------------
    def run(self) -> int:
        me = api_call(self.token, "getMe")
        if not me.get("ok"):
            print(f"Token invalido: {me}", file=sys.stderr)
            return 1
        username = (me.get("result") or {}).get("username")
        print(f"Bot @{username} no ar. Autorizados: {sorted(self.allowed) or 'NENHUM'}")
        if not self.allowed:
            print(
                "Aviso: TELEGRAM_ALLOWED_USER_IDS esta vazio. O bot vai recusar "
                "todo mundo e responder com o id de quem escrever, para voce se autorizar.",
                file=sys.stderr,
            )

        offset = None
        while True:
            try:
                payload = {"timeout": POLL_TIMEOUT}
                if offset is not None:
                    payload["offset"] = offset
                response = api_call(self.token, "getUpdates", payload, timeout=POLL_TIMEOUT + 15)
            except RuntimeError as exc:
                print(f"getUpdates falhou: {exc}", file=sys.stderr)
                time.sleep(5)
                continue

            for update in response.get("result", []):
                offset = update["update_id"] + 1
                try:
                    if "callback_query" in update:
                        self.handle_callback(update["callback_query"])
                    elif "message" in update:
                        self.handle_message(update["message"])
                except Exception as exc:  # um update ruim nao derruba o bot
                    print(f"Erro tratando update {update.get('update_id')}: {exc}", file=sys.stderr)


def parse_allowed_ids(raw: str) -> set[int]:
    ids = set()
    for chunk in re.split(r"[,\s]+", raw or ""):
        if chunk.strip().lstrip("-").isdigit():
            ids.add(int(chunk.strip()))
    return ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bot do Telegram que monta e publica Reels.")
    parser.add_argument("--project-dir", default=".", help="Raiz do projeto.")
    parser.add_argument("--python-bin", default=sys.executable, help="Python usado nos subprocessos.")
    parser.add_argument("--hashtags-max", type=int, default=10, help="Hashtags na legenda.")
    parser.add_argument("--token", default=None, help="Token do bot. Padrao: TELEGRAM_BOT_TOKEN do .env")
    return parser


def main() -> int:
    load_dotenv()
    nethelp.prefer_ipv4()
    args = build_parser().parse_args()

    token = args.token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("Erro: defina TELEGRAM_BOT_TOKEN no .env ou passe --token.", file=sys.stderr)
        return 1

    project_dir = Path(args.project_dir).resolve()
    # Sem TELEGRAM_ADMIN_IDS, quem ja estava no .env vira dono: instalacoes
    # antigas continuam funcionando sem editar configuracao.
    admins = parse_allowed_ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))
    if not admins:
        admins = parse_allowed_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))

    bot = Bot(
        token=token,
        project_dir=project_dir,
        admins=admins,
        python_bin=args.python_bin,
        hashtags_max=args.hashtags_max,
        guests_can_publish=os.getenv("TELEGRAM_GUESTS_CAN_PUBLISH", "0").strip()
        in {"1", "true", "yes", "sim"},
    )
    try:
        return bot.run()
    except KeyboardInterrupt:
        print("\nEncerrado.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
