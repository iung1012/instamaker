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
import queue
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
import ai_helper
import schedule_slots
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

# O GraphQL do x.com as vezes demora mais que os 20s padrao do yt-dlp, e um
# unico timeout matava o trabalho inteiro. Sao segundos de espera, nao um
# problema de cookies -- o mesmo link costuma funcionar na tentativa seguinte.
YTDLP_NET_ARGS = [
    "--socket-timeout", os.getenv("YTDLP_SOCKET_TIMEOUT", "60"),
    "--retries", os.getenv("YTDLP_RETRIES", "5"),
    "--extractor-retries", os.getenv("YTDLP_RETRIES", "5"),
    "--retry-sleep", "linear=1::3",
]

# Erros que realmente apontam para cookies/autenticacao. Fora desta lista, a
# dica sobre cookies.json so confunde: o caso comum e rede instavel.
AUTH_ERROR_HINTS = (
    "nsfw", "age-restricted", "log in", "login", "logged in", "private",
    "protected", "not authorized", "unauthorized", "401", "403",
)

# Faxina: arquivos de trabalho velhos somem sozinhos. O disco e compartilhado
# com a Evolution API e os renders sao grandes.
CLEANUP_AFTER_DAYS = int(os.getenv("CLEANUP_AFTER_DAYS", "7"))
CLEANUP_INTERVAL_SECONDS = 3600
# A sessao do Instagram expira sem avisar; checamos de tempos em tempos e
# guardamos o resultado para nao consultar a cada publicacao.
SESSION_CHECK_INTERVAL = 6 * 3600
SESSION_CACHE_SECONDS = 1800
# De quanto em quanto o agendador olha se algum Reel venceu. 30s da precisao
# de sobra para slots de hora cheia e nao pesa em nada.
SCHEDULE_TICK_SECONDS = 30


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
        *YTDLP_NET_ARGS,
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


def fetch_post_source(url: str, project_dir: Path, python_bin: str) -> dict:
    """Texto, autor e metricas do post.

    Para x.com o proprio site devolve 402 sem login, mas o espelho publico
    fxtwitter entrega tudo em JSON, inclusive as metricas. Fora do X, cai no
    fetch_url_description que o bot ja usava.
    """
    match = re.search(r"(?:twitter|x)\.com/([^/]+)/status/(\d+)", url)
    if match:
        user, status_id = match.group(1), match.group(2)
        try:
            with request.urlopen(
                f"https://api.fxtwitter.com/{user}/status/{status_id}", timeout=20
            ) as response:
                tweet = (json.loads(response.read().decode("utf-8"))
                         .get("tweet") or {})
            if tweet:
                return {
                    "text": tweet.get("text") or "",
                    "author": ((tweet.get("author") or {}).get("screen_name")) or user,
                    "url": url,
                    "views": tweet.get("views"),
                    "likes": tweet.get("likes"),
                }
        except Exception:  # noqa: BLE001 - qualquer falha cai no plano B
            pass
    return {"text": fetch_url_description(url, project_dir, python_bin),
            "author": "", "url": url, "views": None, "likes": None}


def download_failure_hint(error_text: str) -> str:
    """Dica coerente com o erro real.

    Sugerir cookies.json para um read timeout mandava o dono procurar defeito
    onde nao havia: o mesmo link costuma funcionar na tentativa seguinte.
    """
    lowered = error_text.lower()
    if any(marker in lowered for marker in AUTH_ERROR_HINTS):
        return "Parece post privado ou restrito: confira o cookies.json (/cookies)."
    if "timed out" in lowered or "timeout" in lowered or "connection" in lowered:
        return "Deu tempo esgotado falando com o site. Manda o link de novo."
    return "Manda o link de novo. Se insistir, confira o cookies.json (/cookies)."


def fetch_url_description(url: str, project_dir: Path, python_bin: str) -> str:
    """Pega titulo/descricao do post, que viram contexto do hook e da legenda."""
    cmd = [python_bin, "-m", "yt_dlp", "--skip-download", "--no-warnings",
           *YTDLP_NET_ARGS,
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
    avatar_start: float = 0.0,
) -> None:
    cmd = [
        python_bin, "compose_test_video.py",
        "--video", str(source_video),
        "--avatar", str(character),
        "--text-box-opacity", "0.0",
        "--output", str(output),
    ]
    if avatar_start:
        cmd.extend(["--avatar-start", f"{avatar_start:.2f}"])
    if hook_text:
        cmd.extend(["--hook-text", hook_text])

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(project_dir))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-400:]
        raise RuntimeError(f"Composicao falhou: {detail}")
    if not output.is_file():
        raise RuntimeError("Composicao terminou sem gerar o arquivo final.")


def space_caption(text: str) -> str:
    """Deixa a legenda respiravel no feed.

    Legenda chegando como um paragrafo unico e corrido rende mal no Instagram, que
    corta em "mais". Aqui garantimos linha em branco entre os blocos e as hashtags
    isoladas no fim, venha o texto da IA ou do gerador local.
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return ""

    tags: list[str] = []
    body_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        # linha so de hashtags vai toda para o rodape
        if stripped and all(tok.startswith("#") for tok in stripped.split()):
            tags.extend(stripped.split())
        else:
            body_lines.append(stripped)

    body = "\n".join(body_lines).strip()
    # hashtags soltas no fim de uma frase tambem sao puxadas para o rodape
    trailing = re.search(r"((?:\s#[\wÀ-ſ]+)+)\s*$", body)
    if trailing:
        tags.extend(trailing.group(1).split())
        body = body[: trailing.start()].rstrip()

    blocks = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]
    if len(blocks) == 1 and len(blocks[0]) > 180:
        # veio tudo grudado: quebra por frase, 2 frases por bloco
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", blocks[0]) if s.strip()]
        blocks = [" ".join(sentences[i:i + 2]) for i in range(0, len(sentences), 2)]

    out = "\n\n".join(blocks)
    seen: set[str] = set()
    unique = [t for t in tags if not (t.lower() in seen or seen.add(t.lower()))]
    if unique:
        out += "\n\n" + " ".join(unique)
    return out.strip()


def build_caption_for(context_text: str, hashtags_max: int) -> tuple[str, str]:
    try:
        from instagram_graph_publisher import build_viral_hashtags, detect_topics

        hashtags = build_viral_hashtags(detect_topics(context_text), hashtags_max)
    except ImportError:
        hashtags = ""
    caption, origin = generate_caption(
        context_text=context_text,
        profile_focus=os.getenv(
            "PROFILE_FOCUS", "programacao, tecnologia e IA com exemplos praticos"
        ),
        hashtags=hashtags,
    )
    return space_caption(caption), origin


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
# O fluxo tem dois tempos, separados pela aprovacao do hook: primeiro o
# preparo (barato), depois o render (caro) so quando o texto ja esta aprovado.
PREPARE_STEPS = ["Baixando o video", "Lendo o conteudo", "Criando o hook"]
REEL_STEPS = ["Montando o video", "Escrevendo a legenda", "Enviando"]


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
    rows = [
        [
            {"text": "📷 Instagram", "callback_data": f"p_ig:{job_id}"},
            {"text": "🎵 TikTok", "callback_data": f"p_tt:{job_id}"},
            {"text": "🚀 Ambos", "callback_data": f"p_all:{job_id}"},
        ],
        [
            {"text": "📅 Agendar", "callback_data": f"sch:{job_id}"},
            {"text": "✏️ Legenda", "callback_data": f"cap:{job_id}"},
            {"text": "🗑 Descartar", "callback_data": f"del:{job_id}"},
        ],
    ]
    if has_alternatives:
        rows.insert(0, [{"text": "🎭 Trocar personagem", "callback_data": f"chg:{job_id}"}])
    return {"inline_keyboard": rows}


def format_keyboard(pick_id: str) -> dict:
    """Video (Reel com personagem) ou carrossel (9 slides)."""
    return {"inline_keyboard": [
        [{"text": "🎬 Vídeo (Reel)", "callback_data": f"fmv:{pick_id}"}],
        [{"text": "🖼 Carrossel (9 slides)", "callback_data": f"fmc:{pick_id}"}],
    ]}


def schedule_keyboard(job_id: str, now: float | None = None) -> dict:
    """Slots de agendamento. O epoch vai inteiro no callback, ja resolvido em
    Brasilia, para o clique nao depender do fuso de quem clica."""
    rows = [
        [{"text": f"🕐 {rotulo}", "callback_data": f"sca:{job_id}:{int(epoch)}"}]
        for rotulo, epoch in schedule_slots.slot_options(now)
    ]
    rows.append([{"text": "↩️ Voltar", "callback_data": f"scb:{job_id}"}])
    return {"inline_keyboard": rows}


def scheduled_keyboard(job_id: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "🚀 Publicar agora", "callback_data": f"p_all:{job_id}"},
        {"text": "❌ Cancelar agendamento", "callback_data": f"scx:{job_id}"},
    ]]}


def caption_keyboard(job_id: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "🔄 Refazer", "callback_data": f"rcp:{job_id}"},
        {"text": "✏️ Escrever a minha", "callback_data": f"wcp:{job_id}"},
    ]]}


def hook_keyboard(draft_id: str, has_ai: bool = False) -> dict:
    rows = [
        [
            {"text": "✅ Usar este", "callback_data": f"hok:{draft_id}"},
            {"text": "🔄 Refazer", "callback_data": f"rhk:{draft_id}"},
        ],
        [
            {"text": "✏️ Escrever o meu", "callback_data": f"whk:{draft_id}"},
            {"text": "🗑 Cancelar", "callback_data": f"dhk:{draft_id}"},
        ],
    ]
    if has_ai:
        rows.insert(0, [{"text": "🤖 Sugerir Ganchos IA", "callback_data": f"aih:{draft_id}"}])
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
        # Revisao visual do render (cabeca cortada / texto cortado) antes do
        # Publicar. Custa uma chamada ao Gemini por Reel.
        self.review_renders = os.getenv("AI_REVIEW_RENDERS", "1").strip() not in {
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
        # chat_id -> ("hook"|"caption", id) enquanto esperamos o texto digitado
        self.pending_text: dict[int, tuple[str, str]] = {}
        # rascunhos aguardando aprovacao do hook, antes de gastar o render
        self.drafts: dict[str, dict] = {}
        # link recebido esperando o usuario escolher video ou carrossel
        self.pending_format: dict[str, dict] = {}
        # Uma fila com um worker: o render come CPU e a maquina tem 2 vCPU,
        # entao processar em paralelo so faria os dois demorarem mais.
        self.queue: queue.Queue = queue.Queue()
        self._session_ok: bool | None = None
        self._session_checked_at = 0.0
        self._session_warned = False
        # chats que pediram /cookies e vao mandar o arquivo/texto em seguida
        self.pending_cookies: set[int] = set()
        # chats que pediram /tiktok_cookies e vao mandar o arquivo/texto em seguida
        self.pending_tiktok_cookies: set[int] = set()

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
        conta_ig = "conectada" if (self.project_dir / IG_SESSION_FILENAME).is_file() else "nao conectada"
        tt_cookies_name = os.getenv("TIKTOK_COOKIES_FILE", "tiktok_cookies.txt")
        conta_tt = "conectada (cookies)" if (self.project_dir / tt_cookies_name).is_file() or (self.project_dir / "cookies.txt").is_file() or (self.project_dir / "cookies.json").is_file() else "nao conectada"
        linhas = [
            "🎬 *Reel Maker*",
            "",
            "Manda um *link* de video ou o *arquivo* que eu monto o Reel:",
            "conteudo em cima, faixa com o gancho no meio, personagem embaixo.",
            "",
            f"🎭 Personagem: {atual}",
        ]
        if self.is_admin(user_id):
            linhas.append(f"📷 Instagram: {conta_ig}")
            linhas.append(f"🎵 TikTok: {conta_tt}")
        linhas += ["", "Use os botoes abaixo ou /ajuda para ver tudo."]
        return "\n".join(linhas).replace("*", "")

    def help_text(self, is_admin: bool) -> str:
        linhas = [
            "❓ Como usar",
            "",
            "1. Manda um link (ou varios) ou um arquivo de video.",
            "2. Eu mostro o gancho da faixa preta: aprova, refaz ou escreve o seu.",
            "3. So depois eu monto o Reel — assim nao gasto render em texto ruim.",
            "4. No Reel pronto da para trocar o personagem, mexer na legenda",
            "   e publicar (no Instagram, TikTok ou Ambos).",
            "",
            "/fila — o que esta em andamento",
            "/agendados — Reels com hora marcada",
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
                "🎵 TikTok",
                "/tiktok_cookies — envia os cookies do TikTok (tiktok_cookies.txt ou JSON)",
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

    # -- fila --------------------------------------------------------------
    def enqueue(self, task: tuple) -> None:
        """Coloca o trabalho na fila e avisa quantos estao na frente."""
        waiting = self.queue.qsize()
        self.queue.put(task)
        if waiting:
            chat_id = task[1]
            self.say(chat_id, f"📥 Na fila: {waiting} trabalho(s) na frente. Ja te aviso.")

    def worker_loop(self) -> None:
        while True:
            task = self.queue.get()
            try:
                self.run_task(task)
            except Exception as exc:  # um trabalho ruim nao derruba a fila
                print(f"Erro no worker: {exc}", file=sys.stderr)
                try:
                    self.say(task[1], f"⚠️ Falhou: {exc}")
                except Exception:
                    pass
            finally:
                self.queue.task_done()

    def run_task(self, task: tuple) -> None:
        kind, chat_id = task[0], task[1]
        if kind == "url":
            self.prepare_from_url(chat_id, task[2])
        elif kind == "file":
            self.prepare_from_file(chat_id, task[2], task[3])
        elif kind == "render":
            exclude = Path(task[3]) if len(task) > 3 and task[3] else None
            self.render_draft(chat_id, task[2], exclude=exclude)
        elif kind == "carousel":
            self.build_carousel(chat_id, task[2])

    # -- fase 1: preparo e hook -------------------------------------------
    def prepare_from_url(self, chat_id: int, url: str) -> None:
        progress = ProgressMessage(self.token, chat_id, "Preparando", PREPARE_STEPS)
        progress.stage(0)
        try:
            source = download_from_url(url, self.downloads, self.project_dir, self.python_bin)
        except RuntimeError as exc:
            progress.fail(f"{exc}\n\n{download_failure_hint(str(exc))}")
            return
        progress.stage(1)
        context = fetch_url_description(url, self.project_dir, self.python_bin)
        self.offer_hook(chat_id, source, context, progress)

    # -- carrossel ---------------------------------------------------------
    def build_carousel(self, chat_id: int, url: str) -> None:
        """Link -> 9 slides 1080x1350 + legenda, tudo entregue aqui no chat."""
        import shutil as _shutil

        from carousel import (attach_images, build_deck, describe_frames,
                              extract_frames, render_deck)

        progress = ProgressMessage(self.token, chat_id, "Montando o carrossel",
                                   ["Lendo o post", "Pegando os frames",
                                    "Escrevendo os slides", "Renderizando"])
        work = self.outputs / f"carousel_{uuid.uuid4().hex[:10]}"
        try:
            progress.stage(0)
            source = fetch_post_source(url, self.project_dir, self.python_bin)
            if not (source.get("text") or "").strip():
                progress.fail("Nao consegui ler o texto desse post.")
                return

            progress.stage(1)
            images: list[str] = []
            try:
                video = download_from_url(url, self.downloads, self.project_dir,
                                          self.python_bin)
                images = [str(p) for p in extract_frames(video, work / "img", count=4)]
            except RuntimeError:
                # post sem video ou download bloqueado: o carrossel funciona so com texto
                images = []

            progress.stage(2)
            # Le o que esta escrito nas telas antes de escrever: o texto do post
            # costuma ser so uma frase de efeito, e o conteudo (precos, prazos,
            # numeros) esta na interface do video.
            screens = describe_frames(images)
            deck = build_deck(source,
                              status=os.getenv("CAROUSEL_STATUS", "nao_verificado"),
                              screens=screens)
            if images:
                attach_images(deck, images)

            progress.stage(3)
            slides = render_deck(deck, work / "out")
        except Exception as exc:  # noqa: BLE001 - qualquer falha vira aviso no chat
            progress.fail(f"Nao consegui montar o carrossel.\n{exc}")
            _shutil.rmtree(work, ignore_errors=True)
            return

        progress.done(f"{len(slides)} slides prontos")
        for slide in slides:
            api_upload(self.token, "sendPhoto", {"chat_id": chat_id}, "photo", slide)

        caption = space_caption(
            (deck.get("caption") or "") + "\n\n" + " ".join(deck.get("hashtags") or [])
        )
        self.say(chat_id, f"📝 Legenda:\n\n{caption}" if caption else
                 "Carrossel pronto (sem legenda gerada).")
        self.say(chat_id, f"🖼 {len(slides)} slides em {work / 'out'}")

    def prepare_from_file(self, chat_id: int, file_id: str, caption_text: str) -> None:
        progress = ProgressMessage(self.token, chat_id, "Preparando", PREPARE_STEPS)
        progress.stage(0)
        try:
            source = download_telegram_file(self.token, file_id, self.downloads)
        except RuntimeError as exc:
            progress.fail(str(exc))
            return
        progress.stage(1)
        self.offer_hook(chat_id, source, caption_text or "", progress)

    def make_hook(self, context_text: str) -> str:
        hook, _ = generate_hook(
            context_text,
            profile_focus=os.getenv(
                "PROFILE_FOCUS", "programacao, tecnologia e IA com exemplos praticos"
            ),
        )
        return hook

    def offer_hook(self, chat_id: int, source: Path, context: str,
                   progress: ProgressMessage) -> None:
        """Mostra o hook e espera o ok: render custa ~40s, texto custa nada."""
        progress.stage(2)
        hook = self.make_hook(context)
        progress.done("Hook pronto — confirma abaixo")

        draft_id = uuid.uuid4().hex[:10]
        self.drafts[draft_id] = {
            "chat_id": chat_id,
            "source_video": str(source),
            "context": context,
            "hook": hook,
            "created": time.time(),
        }
        self.say(chat_id, self.hook_preview(hook), hook_keyboard(draft_id, ai_helper.is_ai_available()))

    def hook_preview(self, hook: str) -> str:
        if not hook:
            return ("✍️ Nao consegui tirar um gancho do conteudo.\n\n"
                    "Se seguir assim, o video usa uma frase padrao. "
                    "Prefiro que voce escreva a sua.")
        return f"✍️ Gancho da faixa preta:\n\n「 {hook} 」\n\nUsa esse?"

    # -- fase 2: render ----------------------------------------------------
    def render_draft(self, chat_id: int, draft_id: str,
                     exclude: Path | None = None) -> None:
        draft = self.drafts.get(draft_id)
        if not draft:
            self.say(chat_id, "Esse rascunho expirou. Manda o link de novo.")
            return

        source_video = Path(draft["source_video"])
        if not source_video.is_file():
            self.say(chat_id, "O video de origem sumiu. Manda o link de novo.")
            return

        progress = ProgressMessage(self.token, chat_id, "Montando seu Reel", REEL_STEPS)
        character = character_lib.pick_character(self.project_dir, exclude=exclude)
        if character is None:
            progress.fail("Nenhum personagem disponivel. Coloque videos em characters/.")
            return

        label = character_lib.character_label(character)
        context_text = draft.get("context", "")
        output = self.outputs / f"tg_{uuid.uuid4().hex[:12]}_final.mp4"
        self.outputs.mkdir(parents=True, exist_ok=True)

        # Sorteia o trecho do clipe: mesmo com um personagem so na biblioteca, o
        # painel de baixo deixa de sair identico em todo post.
        avatar_start = character_lib.segment_start(
            character, clip_seconds=probe_video(source_video, self.python_bin).get("duration", 0) or 15
        )
        character_lib.record_use(self.project_dir, character)

        progress.stage(0)
        try:
            compose_video(
                self.project_dir, self.python_bin, source_video, character, output,
                hook_text=draft.get("hook", ""), avatar_start=avatar_start,
            )
        except RuntimeError as exc:
            progress.fail(f"Nao consegui montar o video.\n{exc}")
            return

        progress.stage(1)
        if draft.get("ai_caption") and draft.get("ai_approved"):
            caption, origin = draft["ai_caption"], "IA Gemini"
        else:
            caption, origin = build_caption_for(context_text, self.hashtags_max)

        job_id = uuid.uuid4().hex[:10]
        self.jobs[job_id] = {
            "chat_id": chat_id,
            "video": str(output),
            "source_video": str(source_video),
            "caption": caption,
            "context": context_text,
            "character": str(character),
            "hook": draft.get("hook", ""),
            "draft": draft_id,
            "created": time.time(),
        }
        save_jobs(self.project_dir, self.jobs)

        alternatives = (
            len(character_lib.list_characters(self.project_dir)) > 1
            or len(character_lib.variants(
                character_lib.entry_for(self.project_dir, character)
            )) > 1
        )
        progress.stage(2)
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
        self.show_caption(chat_id, job_id, caption)
        self.review_render(chat_id, output, draft.get("hook", ""), caption)

    def review_render(self, chat_id: int, video: Path, hook: str, caption: str) -> None:
        """Pede a revisao visual do Reel e avisa no chat antes do Publicar.

        Fora da thread do worker: a chamada leva alguns segundos e a fila nao
        pode parar por causa de uma conferencia opcional.
        """
        if not self.review_renders:
            return

        def _review() -> None:
            try:
                import video_qa

                result = video_qa.review_video(video, hook=hook, caption=caption)
            except Exception as exc:  # revisao nunca bloqueia a publicacao
                print(f"Revisao da IA indisponivel: {exc}", file=sys.stderr)
                return
            if result.get("aprovado"):
                return  # so avisa quando ha algo errado
            self.say(chat_id, video_qa.format_report(result))

        threading.Thread(target=_review, daemon=True).start()

    def show_caption(self, chat_id: int, job_id: str, caption: str) -> None:
        self.say(chat_id, f"📝 Legenda do post:\n\n{caption}", caption_keyboard(job_id))

    # -- manutencao --------------------------------------------------------
    def protected_paths(self) -> set[str]:
        keep = set()
        for job in self.jobs.values():
            keep.add(str(job.get("video", "")))
            keep.add(str(job.get("source_video", "")))
        for draft in self.drafts.values():
            keep.add(str(draft.get("source_video", "")))
        return keep

    def cleanup_old_files(self) -> int:
        """Apaga material de trabalho velho, menos o que ainda esta em uso."""
        cutoff = time.time() - CLEANUP_AFTER_DAYS * 86400
        keep = self.protected_paths()
        removed = 0
        for folder in (self.downloads, self.outputs):
            if not folder.is_dir():
                continue
            for item in folder.iterdir():
                if not item.is_file() or str(item) in keep:
                    continue
                try:
                    if item.stat().st_mtime < cutoff:
                        item.unlink()
                        removed += 1
                except OSError:
                    continue
        if removed:
            print(f"Faxina: {removed} arquivo(s) com mais de "
                  f"{CLEANUP_AFTER_DAYS} dias removidos.")
        return removed

    def cleanup_loop(self) -> None:
        while True:
            try:
                self.cleanup_old_files()
                self.expire_drafts()
            except Exception as exc:
                print(f"Faxina falhou: {exc}", file=sys.stderr)
            time.sleep(CLEANUP_INTERVAL_SECONDS)

    def expire_drafts(self) -> None:
        cutoff = time.time() - 86400
        for draft_id in [k for k, v in self.drafts.items() if v.get("created", 0) < cutoff]:
            self.drafts.pop(draft_id, None)

    def session_ok(self, force: bool = False) -> bool:
        """Sessao do Instagram viva? O resultado fica em cache por 30 min."""
        if not (self.project_dir / IG_SESSION_FILENAME).is_file():
            return False
        fresh = time.time() - self._session_checked_at < SESSION_CACHE_SECONDS
        if not force and fresh and self._session_ok is not None:
            return self._session_ok
        result = subprocess.run(
            [self.python_bin, "instagrapi_publisher.py", "--whoami"],
            capture_output=True, text=True, cwd=str(self.project_dir),
        )
        self._session_ok = result.returncode == 0
        self._session_checked_at = time.time()
        return self._session_ok

    def session_watch_loop(self) -> None:
        while True:
            time.sleep(SESSION_CHECK_INTERVAL)
            try:
                if not (self.project_dir / IG_SESSION_FILENAME).is_file():
                    continue
                if self.session_ok(force=True):
                    self._session_warned = False
                    continue
                if self._session_warned:
                    continue
                self._session_warned = True
                for admin in sorted(self.admins):
                    self.say(admin, "⚠️ A sessao do Instagram expirou.\n"
                                    "Reconecte com /cookies ou /login — sem isso "
                                    "o botao Publicar vai falhar.")
            except Exception as exc:
                print(f"Checagem de sessao falhou: {exc}", file=sys.stderr)

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

    def save_tiktok_cookies(self, chat_id: int, payload: str) -> None:
        tt_file_name = os.getenv("TIKTOK_COOKIES_FILE", "tiktok_cookies.txt")
        tt_file = self.project_dir / tt_file_name
        tt_file.write_text(payload.strip(), encoding="utf-8")
        self.say(
            chat_id,
            f"🎵 Cookies do TikTok salvos em '{tt_file_name}'!\n"
            "O botao 🎵 TikTok ou 🚀 Ambos no chat ja pode ser utilizado para publicacao.",
        )

    def publish_instagram(self, chat_id: int, job: dict) -> bool:
        video = Path(job["video"])
        if not video.is_file():
            self.say(chat_id, "O arquivo do video sumiu. Manda o link de novo.")
            return False

        session = self.project_dir / IG_SESSION_FILENAME
        if session.is_file() and not self.session_ok():
            self.say(chat_id, "⚠️ A sessao do Instagram expirou.\n"
                              "Reconecte com /cookies ou /login e clique em Publicar de novo. "
                              "O Reel continua guardado aqui.")
            return False

        progress = ProgressMessage(self.token, chat_id, "Publicando no Instagram",
                                   ["Enviando o video", "Finalizando o post"])
        progress.stage(0)
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
            progress.fail(f"Publicacao no Instagram falhou.\n{detail}")
            return False

        post_line = next(
            (line for line in output.splitlines() if "Post ID" in line), "Publicado."
        )
        music_line = next(
            (line for line in output.splitlines() if line.startswith("Musica:")), ""
        )
        progress.done(f"Pronto. Instagram: {post_line}" + (f"\n🎵 {music_line}" if music_line else ""))
        return True

    def publish_tiktok(self, chat_id: int, job: dict) -> bool:
        video = Path(job["video"])
        if not video.is_file():
            self.say(chat_id, "O arquivo do video sumiu. Manda o link de novo.")
            return False

        progress = ProgressMessage(self.token, chat_id, "Publicando no TikTok",
                                   ["Enviando o video", "Finalizando o post"])
        progress.stage(0)

        tiktok_method = os.getenv("TIKTOK_METHOD", "cookie").strip().lower()
        if tiktok_method == "cookie":
            cmd = [
                self.python_bin, "tiktok_cookie_publisher.py",
                "--video", str(video),
                "--description", job.get("caption", ""),
            ]
            cookies_file = os.getenv("TIKTOK_COOKIES_FILE", "tiktok_cookies.txt")
            if (self.project_dir / cookies_file).is_file():
                cmd.extend(["--cookies", str(self.project_dir / cookies_file)])
        else:
            cmd = [
                self.python_bin, "tiktok_sandbox_uploader.py",
                "--token-only",
                "--video", str(video),
            ]

        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.project_dir))
        output = (result.stdout or "").strip()
        if result.returncode != 0:
            detail = (result.stderr or output).strip()[-500:]
            progress.fail(f"Publicacao no TikTok falhou.\n{detail}")
            return False

        progress.done("Pronto! TikTok publicado com sucesso.")
        return True

    def publish(self, chat_id: int, job: dict, destination: str = "all") -> None:
        if destination in ("instagram", "ig", "all"):
            self.publish_instagram(chat_id, job)
        if destination in ("tiktok", "tt", "all"):
            self.publish_tiktok(chat_id, job)

    # -- agendamento -------------------------------------------------------
    def handle_schedule(self, chat_id: int, cb_id: str, action: str,
                        partes: list[str], job: dict) -> None:
        job_id = partes[0]
        if action == "sch":
            answer_callback(self.token, cb_id)
            self.say(chat_id, "📅 Publicar quando? (horario de Brasilia)",
                     schedule_keyboard(job_id))
            return

        if action == "scb":
            answer_callback(self.token, cb_id)
            self.say(chat_id, "Ok, sem agendar.", job_keyboard(job_id, False))
            return

        if action == "scx":
            answer_callback(self.token, cb_id, "Agendamento cancelado.")
            job.pop("scheduled_for", None)
            save_jobs(self.project_dir, self.jobs)
            self.say(chat_id, "❌ Agendamento cancelado. O Reel continua aqui.",
                     job_keyboard(job_id, False))
            return

        # sca: o epoch vem no proprio callback, ja resolvido em Brasilia.
        try:
            quando = float(partes[1])
        except (IndexError, ValueError):
            answer_callback(self.token, cb_id, "Horario invalido.")
            return

        job["scheduled_for"] = quando
        save_jobs(self.project_dir, self.jobs)
        answer_callback(self.token, cb_id, "Agendado.")
        self.say(
            chat_id,
            f"📅 Agendado para {schedule_slots.format_when(quando)} "
            "(Instagram + TikTok).\n"
            "O bot publica sozinho na hora. /agendados lista tudo.",
            scheduled_keyboard(job_id),
        )

    def schedule_loop(self) -> None:
        """Publica os agendados quando a hora chega.

        Fora da fila de render: um job vencido nao deve esperar um render de
        40s terminar para ir ao ar.
        """
        while True:
            try:
                for job_id in schedule_slots.due_job_ids(self.jobs):
                    job = self.jobs.get(job_id)
                    if not job:
                        continue
                    job.pop("scheduled_for", None)
                    save_jobs(self.project_dir, self.jobs)
                    chat_id = job.get("chat_id")
                    self.say(chat_id, "📅 Chegou a hora do agendamento. Publicando...")
                    self.publish(chat_id, job, destination="all")
                    self.jobs.pop(job_id, None)
                    save_jobs(self.project_dir, self.jobs)
            except Exception as exc:  # um job ruim nao para o agendador
                print(f"Erro no agendador: {exc}", file=sys.stderr)
            time.sleep(SCHEDULE_TICK_SECONDS)

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

        # -- escolha do formato, antes de baixar qualquer coisa ---------
        if action in {"fmv", "fmc"}:
            pick = self.pending_format.pop(job_id, None)
            if not pick:
                answer_callback(self.token, cb_id, "Esse link expirou. Manda de novo.")
                return
            if action == "fmv":
                answer_callback(self.token, cb_id, "Montando o Reel...")
                self.say(chat_id, "🎬 Beleza, vai de vídeo.")
                self.enqueue(("url", chat_id, pick["url"]))
            else:
                answer_callback(self.token, cb_id, "Montando o carrossel...")
                self.say(chat_id, "🖼 Beleza, vai de carrossel.")
                self.enqueue(("carousel", chat_id, pick["url"]))
            return

        # -- aprovacao do hook, antes de existir job --------------------
        if action in {"hok", "rhk", "whk", "dhk", "aih", "aio"}:
            actual_job_id = job_id.split(":")[0] if ":" in job_id else job_id
            draft = self.drafts.get(actual_job_id)
            if not draft:
                answer_callback(self.token, cb_id, "Esse rascunho expirou.")
                return
            if action == "hok":
                answer_callback(self.token, cb_id, "Montando...")
                self.enqueue(("render", chat_id, actual_job_id))
            elif action == "aih":
                answer_callback(self.token, cb_id, "Pensando...")
                self.say(chat_id, "🤖 Analisando o vídeo e gerando ganchos com IA...")
                def _run_ai():
                    try:
                        res = ai_helper.generate_hooks_and_caption(draft.get("context", ""))
                        draft["ai_hooks"] = res.get("opcoes_gancho", [])
                        draft["ai_caption"] = res.get("legenda", "")
                        rows = []
                        for idx, opt in enumerate(draft["ai_hooks"]):
                            rows.append([{"text": f"✅ {opt}", "callback_data": f"aio:{actual_job_id}:{idx}"}])
                        rows.append([{"text": "🔄 Gerar outros", "callback_data": f"aih:{actual_job_id}"}])
                        rows.append([{"text": "❌ Cancelar IA", "callback_data": f"rhk:{actual_job_id}"}])
                        self.say(chat_id, "🤖 Escolha um gancho (a legenda já será gerada):", {"inline_keyboard": rows})
                    except Exception as e:
                        self.say(chat_id, f"⚠️ Falha na IA: {e}", hook_keyboard(actual_job_id, ai_helper.is_ai_available()))
                threading.Thread(target=_run_ai, daemon=True).start()
            elif action == "aio":
                answer_callback(self.token, cb_id, "Montando...")
                idx = int(job_id.split(":")[1])
                draft["hook"] = draft["ai_hooks"][idx]
                draft["ai_approved"] = True
                self.say(chat_id, self.hook_preview(draft["hook"]))
                self.enqueue(("render", chat_id, actual_job_id))
            elif action == "rhk":
                answer_callback(self.token, cb_id, "Gerando outro...")
                novo = self.make_hook(draft.get("context", ""))
                if novo and novo != draft.get("hook"):
                    draft["hook"] = novo
                    self.say(chat_id, self.hook_preview(novo), hook_keyboard(actual_job_id, ai_helper.is_ai_available()))
                else:
                    self.say(chat_id, "Saiu igual ao anterior. Escreva o seu com "
                                      "'✏️ Escrever o meu' se quiser outro.",
                             hook_keyboard(actual_job_id, ai_helper.is_ai_available()))
            elif action == "whk":
                answer_callback(self.token, cb_id, "Manda o texto")
                self.pending_text[chat_id] = ("hook", actual_job_id)
                self.say(chat_id, "✏️ Escreve o gancho (curto, ate ~45 caracteres).")
            else:
                answer_callback(self.token, cb_id, "Cancelado.")
                Path(draft["source_video"]).unlink(missing_ok=True)
                self.drafts.pop(actual_job_id, None)
                self.say(chat_id, "Cancelado.")
            return

        # Agendamento: o "sca" carrega o epoch junto do id, entao o parse vem
        # antes da busca do job.
        if action in {"sch", "sca", "scb", "scx"}:
            # Agendar e publicar depois: mesma permissao do Publicar, senao
            # seria uma porta lateral para quem nao pode postar.
            if not (self.is_admin(user_id) or self.guests_can_publish):
                answer_callback(self.token, cb_id, "So o dono publica.")
                return
            partes = job_id.split(":")
            alvo = self.jobs.get(partes[0])
            if not alvo:
                answer_callback(self.token, cb_id, "Esse job expirou.")
                return
            self.handle_schedule(chat_id, cb_id, action, partes, alvo)
            return

        job = self.jobs.get(job_id)
        if not job:
            answer_callback(self.token, cb_id, "Esse job expirou.")
            return

        if action == "cap":
            answer_callback(self.token, cb_id)
            self.say(chat_id, "📝 O que fazer com a legenda?", caption_keyboard(job_id))
            return
        if action == "rcp":
            answer_callback(self.token, cb_id, "Refazendo...")
            caption, origin = build_caption_for(job.get("context", ""), self.hashtags_max)
            job["caption"] = caption
            save_jobs(self.project_dir, self.jobs)
            self.say(chat_id, f"📝 Legenda nova (via {origin}):\n\n{caption}",
                     caption_keyboard(job_id))
            return
        if action == "wcp":
            answer_callback(self.token, cb_id, "Manda o texto")
            self.pending_text[chat_id] = ("caption", job_id)
            self.say(chat_id, "✏️ Escreve a legenda que vai no post.")
            return

        if action in ("pub", "p_ig", "p_tt", "p_all"):
            if not (self.is_admin(user_id) or self.guests_can_publish):
                answer_callback(self.token, cb_id, "So o dono publica.")
                self.say(chat_id, "Voce pode montar Reels, mas a publicacao e do dono do bot.")
                return
            job.pop("scheduled_for", None)  # publicar agora cancela o agendamento
            answer_callback(self.token, cb_id, "Publicando...")
            dest = "ig" if action == "p_ig" else ("tt" if action == "p_tt" else "all")
            self.publish(chat_id, job, destination=dest)
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
            # O hook ja foi aprovado: remonta direto, sem perguntar de novo.
            draft_id = job.get("draft") or uuid.uuid4().hex[:10]
            self.drafts[draft_id] = {
                "chat_id": chat_id,
                "source_video": str(source),
                "context": job.get("context", ""),
                "hook": job.get("hook", ""),
                "created": time.time(),
            }
            self.enqueue(("render", chat_id, draft_id, str(job.get("character", ""))))
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

        # Texto pedido por um botao (gancho ou legenda) tem prioridade, mas
        # um comando cancela a espera: senao o usuario fica preso no modo.
        if chat_id in self.pending_text and text and not text.startswith("/"):
            mode, target = self.pending_text.pop(chat_id)
            if mode == "hook":
                draft = self.drafts.get(target)
                if not draft:
                    self.say(chat_id, "Esse rascunho expirou. Manda o link de novo.")
                    return
                draft["hook"] = text[:80]
                self.say(chat_id, f"✍️ Gancho definido:\n\n「 {draft['hook']} 」")
                self.enqueue(("render", chat_id, target))
            else:
                job = self.jobs.get(target)
                if not job:
                    self.say(chat_id, "Esse Reel expirou. Monta de novo.")
                    return
                job["caption"] = text
                save_jobs(self.project_dir, self.jobs)
                self.say(chat_id, f"📝 Legenda definida:\n\n{text}",
                         caption_keyboard(target))
            return
        if chat_id in self.pending_text and text.startswith("/"):
            self.pending_text.pop(chat_id, None)

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
        if text.startswith("/agendados"):
            self.say(chat_id, schedule_slots.scheduled_summary(self.jobs))
            return
        if text.startswith("/fila"):
            pendentes = self.queue.qsize()
            rascunhos = sum(1 for d in self.drafts.values() if d.get("chat_id") == chat_id)
            prontos = sum(1 for j in self.jobs.values() if j.get("chat_id") == chat_id)
            self.say(chat_id, f"📥 Fila: {pendentes} esperando\n"
                              f"✍️ Aguardando seu ok no gancho: {rascunhos}\n"
                              f"🎬 Reels prontos sem publicar: {prontos}")
            return

        # -- comandos so do dono ------------------------------------------
        admin_only = (
            "/autorizar", "/remover", "/usuarios", "/login", "/cookies", "/tiktok_cookies", "/conta",
            "/logout", "/novopersonagem", "/addpersonagem", "/personagem ",
            "/musica", "/musicas", "/relatorio"
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
        if text.startswith("/relatorio"):
            self.say(chat_id, "📊 Buscando estatísticas dos últimos 48h...")
            def _fetch_stats():
                try:
                    result = subprocess.run(
                        [self.python_bin, "analytics_tracker.py"],
                        capture_output=True, text=True, cwd=str(self.project_dir), timeout=60
                    )
                    out = (result.stdout or result.stderr or "").strip()
                    if result.returncode != 0 or not out:
                        self.say(chat_id, "⚠️ Falha ao buscar relatório.")
                    else:
                        self.say(chat_id, out)
                except Exception as e:
                    self.say(chat_id, f"⚠️ Erro no relatório: {e}")
            threading.Thread(target=_fetch_stats, daemon=True).start()
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
        if text.startswith("/tiktok_cookies"):
            self._delete_message(chat_id, message.get("message_id"))
            payload = text.partition(" ")[2].strip()
            if payload:
                self.save_tiktok_cookies(chat_id, payload)
            else:
                self.pending_tiktok_cookies.add(chat_id)
                self.say(
                    chat_id,
                    "Manda agora os cookies do TikTok — como arquivo (.txt / .json) ou em texto.\n"
                    "Eles serao salvos em 'tiktok_cookies.txt' para habilitar a publicacao direta no TikTok.",
                )
            return
        if chat_id in self.pending_tiktok_cookies and text:
            self.pending_tiktok_cookies.discard(chat_id)
            self._delete_message(chat_id, message.get("message_id"))
            self.save_tiktok_cookies(chat_id, text)
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
                self.enqueue(("file", chat_id, video["file_id"], caption_text))
            return

        # Varios links numa mensagem so entram todos na fila, em ordem.
        urls = URL_RE.findall(text)
        if urls:
            if len(urls) > 1:
                self.say(chat_id, f"📥 {len(urls)} links recebidos. "
                                  "Vou montar um de cada vez.")
            # Antes de baixar qualquer coisa, pergunta o formato: video (Reel com
            # personagem) ou carrossel (9 slides). O link fica guardado ate a escolha.
            for url in urls:
                pick_id = uuid.uuid4().hex[:10]
                self.pending_format[pick_id] = {
                    "chat_id": chat_id, "url": url, "created": time.time(),
                }
                self.say(chat_id, f"🔗 {url}\n\nO que eu monto com isso?",
                         format_keyboard(pick_id))
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

        # O render bloqueia por ~40s: fora da thread do polling, o bot fica
        # surdo nesse tempo e o Telegram reentrega os updates.
        for target, name in (
            (self.worker_loop, "worker"),
            (self.cleanup_loop, "faxina"),
            (self.session_watch_loop, "sessao"),
            (self.schedule_loop, "agendador"),
        ):
            threading.Thread(target=target, name=name, daemon=True).start()

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
