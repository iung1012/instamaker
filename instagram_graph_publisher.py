import argparse
import json
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib import error, parse, request

GRAPH_BASE_URL = "https://graph.facebook.com/v22.0"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_INSTAGRAM_HASHTAGS = 30
MAX_CAROUSEL_ITEMS = 10
TMPFILES_DOWNLOAD_RE = re.compile(
    r'<a[^>]*class="download"[^>]*href="(?P<url>https://tmpfiles\.org/dl/[^"]+)"',
    re.IGNORECASE,
)


def load_dotenv(dotenv_path: str = ".env") -> None:
    path = Path(dotenv_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def iso_to_unix(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            "Data invalida. Use formato ISO local: YYYY-MM-DDTHH:MM:SS (ex: 2026-05-20T18:30:00)"
        ) from exc
    return int(parsed.timestamp())


def graph_api_post(endpoint: str, payload: dict) -> dict:
    data = parse.urlencode(payload).encode("utf-8")
    req = request.Request(
        url=f"{GRAPH_BASE_URL}/{endpoint}",
        data=data,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Erro HTTP {exc.code}: {raw}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Erro de rede: {exc.reason}") from exc


def graph_api_get(endpoint: str, query: dict) -> dict:
    qs = parse.urlencode(query)
    url = f"{GRAPH_BASE_URL}/{endpoint}?{qs}"
    req = request.Request(url=url, method="GET")
    try:
        with request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Erro HTTP {exc.code}: {raw}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Erro de rede: {exc.reason}") from exc


def upload_file_to_tmpfiles(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        raise ValueError(f"Arquivo nao encontrado: {path}")

    boundary = f"----Boundary{uuid.uuid4().hex}"
    content_type = "application/octet-stream"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = request.Request(
        url="https://tmpfiles.org/api/v1/upload",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "curl/8.0",
        },
    )
    try:
        with request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
            result = json.loads(raw)
            page_url = result.get("data", {}).get("url")
            if not page_url:
                raise RuntimeError(f"Resposta inesperada do tmpfiles: {result}")
            return resolve_tmpfiles_download_url(page_url)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Falha no upload temporario (HTTP {exc.code}): {raw}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Falha no upload temporario: {exc.reason}") from exc


def extract_tmpfiles_download_url(page_html: str) -> str:
    match = TMPFILES_DOWNLOAD_RE.search(page_html)
    if not match:
        raise RuntimeError("Nao foi possivel extrair o link de download do tmpfiles.")
    return match.group("url")


def resolve_tmpfiles_download_url(page_url: str) -> str:
    headers = {"User-Agent": "curl/8.0"}

    head_req = request.Request(url=page_url, method="HEAD", headers=headers)
    try:
        with request.urlopen(head_req, timeout=60) as resp:
            if resp.headers.get_content_type().startswith("video/"):
                return page_url
    except error.HTTPError as exc:
        if exc.code not in {405, 501}:
            raise
    except error.URLError:
        pass

    get_req = request.Request(url=page_url, method="GET", headers=headers)
    try:
        with request.urlopen(get_req, timeout=60) as resp:
            if resp.headers.get_content_type().startswith("video/"):
                return page_url
            html = resp.read().decode("utf-8", errors="ignore")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(
            f"Falha ao resolver link de download do tmpfiles (HTTP {exc.code}): {raw}"
        ) from exc
    except error.URLError as exc:
        raise RuntimeError(f"Falha ao resolver link de download do tmpfiles: {exc.reason}") from exc

    return extract_tmpfiles_download_url(html)


def pick_media_from_dir(from_dir: str, file_name: str | None = None) -> Path:
    base = Path(from_dir)
    if not base.exists() or not base.is_dir():
        raise ValueError(f"Diretorio invalido: {base}")

    if file_name:
        selected = base / file_name
        if not selected.exists() or not selected.is_file():
            raise ValueError(f"Arquivo nao encontrado em {base}: {file_name}")
        return selected

    candidates = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in (VIDEO_EXTENSIONS | IMAGE_EXTENSIONS)]
    if not candidates:
        raise ValueError(f"Nenhuma midia suportada encontrada em {base}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def list_media_from_dir(from_dir: str) -> list[Path]:
    base = Path(from_dir)
    if not base.exists() or not base.is_dir():
        raise ValueError(f"Diretorio invalido: {base}")
    media = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in (VIDEO_EXTENSIONS | IMAGE_EXTENSIONS)]
    if not media:
        raise ValueError(f"Nenhuma midia suportada encontrada em {base}")
    return sorted(media, key=lambda p: p.stat().st_mtime)


def list_carousel_images(carousel_dir: str, max_items: int = MAX_CAROUSEL_ITEMS) -> list[Path]:
    base = Path(carousel_dir)
    if not base.exists() or not base.is_dir():
        raise ValueError(f"Diretorio de carrossel invalido: {base}")

    images = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
    if not images:
        raise ValueError(f"Nenhuma imagem encontrada em {base}")

    images = sorted(images, key=lambda p: p.name.lower())
    if len(images) < 2:
        raise ValueError("Carrossel precisa de no minimo 2 imagens.")

    limited_max = max(2, min(max_items, MAX_CAROUSEL_ITEMS))
    if len(images) > limited_max:
        print(
            f"Aviso: {len(images)} imagens encontradas, usando apenas as primeiras "
            f"{limited_max} (limite API)."
        )
        images = images[:limited_max]

    return images


def read_carousel_caption(carousel_dir: str) -> str:
    caption_file = Path(carousel_dir) / "caption.txt"
    if not caption_file.exists() or not caption_file.is_file():
        return ""
    return caption_file.read_text(encoding="utf-8", errors="ignore").strip()


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
        if candidate.exists() and candidate.is_file():
            content = candidate.read_text(encoding="utf-8", errors="ignore").strip()
            if "Descricao:" in content:
                content = content.split("Descricao:", 1)[1].strip()
            if "Descrição:" in content:
                content = content.split("Descrição:", 1)[1].strip()
            if content:
                return content
    return ""


def detect_topics(text: str) -> list[str]:
    t = text.lower()
    topics: list[str] = []
    checks = [
        ("chatgpt", ["chatgpt", "gpt"]),
        ("automacao", ["automacao", "automation", "automat"]),
        ("prompts", ["prompt", "prompts"]),
        ("ferramentas", ["tool", "ferramenta", "app", "saas"]),
        ("programacao", ["python", "codigo", "code", "dev"]),
        ("marketing", ["marketing", "vendas", "leads", "conteudo"]),
        ("reels", ["reel", "video", "shorts"]),
    ]
    for topic, words in checks:
        if any(w in t for w in words):
            topics.append(topic)
    if "ia" not in t and "ai" not in t:
        topics.insert(0, "ia")
    return topics[:4]


def build_viral_hashtags(topics: list[str], max_tags: int) -> str:
    core = [
        "ia",
        "inteligenciaartificial",
        "artificialintelligence",
        "automacao",
        "tecnologia",
        "inovacao",
        "chatgpt",
        "openai",
        "machinelearning",
        "deeplearning",
        "produtividade",
        "negocios",
        "empreendedorismo",
        "marketingdigital",
        "futuro",
        "ferramentas",
        "prompts",
        "viral",
        "reels",
        "reelsbrasil",
        "conteudodigital",
        "criadores",
        "growth",
        "socialmedia",
        "automatizacoes",
        "engenhariadesoftware",
        "programacao",
        "startup",
        "saas",
        "techtips",
    ]
    topic_map = {
        "chatgpt": ["chatgpttips", "chatgptbrasil", "gpt4", "gpt"],
        "automacao": ["automacaoia", "automacaodedados", "nocode", "workflow"],
        "prompts": ["promptengineering", "prompttips", "promptdesign"],
        "ferramentas": ["aitools", "digitaltools", "ferramentasdigitais"],
        "programacao": ["python", "coding", "devbrasil", "programador"],
        "marketing": ["copywriting", "trafegopago", "estrategiadigital"],
        "reels": ["videomarketing", "instagramreels", "shortformvideo"],
        "ia": ["aimarketing", "aiagent", "aiworkflow"],
    }
    tags = core.copy()
    for topic in topics:
        tags.extend(topic_map.get(topic, []))

    seen = set()
    unique_tags = []
    for tag in tags:
        cleaned = re.sub(r"[^a-z0-9_]", "", tag.lower())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique_tags.append(cleaned)

    random.shuffle(unique_tags)
    limited = unique_tags[: max(1, min(max_tags, MAX_INSTAGRAM_HASHTAGS))]
    return " ".join(f"#{t}" for t in limited)


def generate_caption(style: str, context_text: str, hashtags_max: int) -> str:
    topics = detect_topics(context_text)
    topic_line = " - ".join(["IA", "Automacao", "Tecnologia"])
    if topics:
        mapped = {
            "chatgpt": "ChatGPT",
            "automacao": "Automacao",
            "prompts": "Prompts",
            "ferramentas": "Ferramentas",
            "programacao": "Codigo",
            "marketing": "Marketing",
            "reels": "Reels",
            "ia": "IA",
        }
        words = [mapped[t] for t in topics if t in mapped]
        if words:
            topic_line = " - ".join(words[:3])

    templates = {
        "classic": [
            f"{topic_line}\nHacks, prompts e ferramentas avancadas\nTransformando ideias em sistemas inteligentes\nConteudo diario sobre o futuro da IA",
        ],
        "premium": [
            f"Explorando o futuro da Inteligencia Artificial\n{topic_line}\nIA aplicada no mundo real\nEntre no modo avancado",
        ],
        "growth": [
            f"Conteudo insano de IA todos os dias\n{topic_line}\nAprenda a usar IA como um profissional\nEntre para o futuro da tecnologia",
        ],
    }
    base = random.choice(templates.get(style, templates["classic"]))
    hashtags = build_viral_hashtags(topics, hashtags_max)
    return f"{base}\n\n{hashtags}"


def create_media_container(
    ig_user_id: str,
    access_token: str,
    caption: str,
    image_url: str | None,
    video_url: str | None,
    schedule_at: str | None,
    story: bool = False,
) -> str:
    if bool(image_url) == bool(video_url):
        raise ValueError("Informe apenas um: --image-url ou --video-url.")
    if story and not video_url:
        raise ValueError("Stories neste script aceitam apenas video. Use --video-url, --video-path ou --from-dir com video.")
    if story and schedule_at:
        raise ValueError("Agendamento nao esta disponivel para Stories. Use --publish-now.")

    payload = {
        "access_token": access_token,
    }

    if caption and not story:
        payload["caption"] = caption

    if image_url:
        payload["image_url"] = image_url
    else:
        payload["video_url"] = video_url
        payload["media_type"] = "STORIES" if story else "REELS"

    if schedule_at:
        payload["published"] = "false"
        payload["scheduled_publish_time"] = str(iso_to_unix(schedule_at))

    result = graph_api_post(f"{ig_user_id}/media", payload)
    container_id = result.get("id")
    if not container_id:
        raise RuntimeError(f"Resposta inesperada ao criar container: {result}")
    return container_id


def create_carousel_item_container(ig_user_id: str, access_token: str, image_url: str) -> str:
    payload = {
        "image_url": image_url,
        "is_carousel_item": "true",
        "access_token": access_token,
    }
    result = graph_api_post(f"{ig_user_id}/media", payload)
    container_id = result.get("id")
    if not container_id:
        raise RuntimeError(f"Resposta inesperada ao criar item do carrossel: {result}")
    return container_id


def create_carousel_container(
    ig_user_id: str,
    access_token: str,
    caption: str,
    children_ids: list[str],
    schedule_at: str | None,
) -> str:
    if len(children_ids) < 2:
        raise ValueError("Carrossel exige no minimo 2 itens.")
    if len(children_ids) > MAX_CAROUSEL_ITEMS:
        raise ValueError(f"Carrossel excede limite da API ({MAX_CAROUSEL_ITEMS} itens).")

    payload = {
        "caption": caption,
        "media_type": "CAROUSEL",
        "children": ",".join(children_ids),
        "access_token": access_token,
    }

    if schedule_at:
        payload["published"] = "false"
        payload["scheduled_publish_time"] = str(iso_to_unix(schedule_at))

    result = graph_api_post(f"{ig_user_id}/media", payload)
    container_id = result.get("id")
    if not container_id:
        raise RuntimeError(f"Resposta inesperada ao criar container de carrossel: {result}")
    return container_id


def publish_media(ig_user_id: str, access_token: str, creation_id: str) -> str:
    payload = {
        "creation_id": creation_id,
        "access_token": access_token,
    }
    result = graph_api_post(f"{ig_user_id}/media_publish", payload)
    post_id = result.get("id")
    if not post_id:
        raise RuntimeError(f"Resposta inesperada ao publicar: {result}")
    return post_id


def wait_until_media_ready(
    creation_id: str,
    access_token: str,
    timeout_seconds: int = 300,
    interval_seconds: int = 5,
) -> None:
    started = time.time()
    while True:
        result = graph_api_get(
            creation_id,
            {
                "fields": "status_code,status",
                "access_token": access_token,
            },
        )
        status_code = result.get("status_code") or result.get("status")
        print(f"Status da midia {creation_id}: {status_code}")

        if status_code == "FINISHED":
            return
        if status_code in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Processamento da midia falhou: {result}")

        if time.time() - started > timeout_seconds:
            raise RuntimeError(
                f"Timeout aguardando processamento da midia ({timeout_seconds}s). Ultimo status: {status_code}"
            )
        time.sleep(interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publica no Instagram via Graph API (modo oficial)."
    )
    parser.add_argument("--ig-user-id", default=os.getenv("IG_USER_ID"), help="Instagram User ID")
    parser.add_argument(
        "--access-token",
        default=os.getenv("IG_ACCESS_TOKEN"),
        help="Access Token da Graph API",
    )
    parser.add_argument("--caption", default="", help="Legenda do post")
    parser.add_argument(
        "--auto-caption",
        action="store_true",
        help="Gera legenda automatica com variacoes e hashtags",
    )
    parser.add_argument(
        "--caption-style",
        choices=["classic", "premium", "growth"],
        default="classic",
        help="Estilo da legenda automatica",
    )
    parser.add_argument(
        "--hashtags-max",
        type=int,
        default=30,
        help="Quantidade maxima de hashtags na legenda automatica (max 30)",
    )
    parser.add_argument("--image-url", help="URL publica da imagem")
    parser.add_argument("--video-url", help="URL publica do video (Reel)")
    parser.add_argument(
        "--video-path",
        help="Caminho local do video para upload temporario automatico",
    )
    parser.add_argument(
        "--story",
        action="store_true",
        help="Publica o video como Instagram Story em vez de Reel",
    )
    parser.add_argument(
        "--from-dir",
        help="Pasta para buscar midia automaticamente (ex: outputs_ig)",
    )
    parser.add_argument(
        "--file-name",
        help="Nome do arquivo dentro de --from-dir. Se omitido, usa o mais recente.",
    )
    parser.add_argument(
        "--carousel-dir",
        help=(
            "Diretorio de um pacote de carrossel com imagens (ex: outputs_ig/carousels/carousel_YYYY...). "
            "Usa caption.txt automaticamente se existir."
        ),
    )
    parser.add_argument(
        "--carousel-max-items",
        type=int,
        default=MAX_CAROUSEL_ITEMS,
        help=f"Maximo de imagens a publicar por carrossel (limite API {MAX_CAROUSEL_ITEMS}).",
    )
    parser.add_argument(
        "--post-all-from-dir",
        action="store_true",
        help="Publica todas as midias da pasta --from-dir em sequencia",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=8,
        help="Intervalo entre publicacoes em segundos no modo --post-all-from-dir",
    )
    parser.add_argument(
        "--schedule-at",
        help="Data/hora local ISO para agendar (YYYY-MM-DDTHH:MM:SS)",
    )
    parser.add_argument(
        "--publish-now",
        action="store_true",
        help="Se definido, publica imediatamente apos criar o container",
    )
    parser.add_argument(
        "--creation-id",
        help="Publica um container ja criado (sem criar novo)",
    )
    parser.add_argument(
        "--wait-for-ready",
        action="store_true",
        help="Aguarda processamento do container antes de publicar (recomendado para video)",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=300,
        help="Timeout maximo em segundos para aguardar midia pronta",
    )
    return parser


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    if not args.ig_user_id:
        print("Erro: defina --ig-user-id ou IG_USER_ID.")
        return 1
    if not args.access_token:
        print("Erro: defina --access-token ou IG_ACCESS_TOKEN.")
        return 1

    try:
        if args.story and args.carousel_dir:
            raise ValueError("Use --story apenas com video, nao com --carousel-dir.")
        if args.story and args.creation_id:
            raise ValueError("--story deve ser usado ao criar um novo container de video, nao com --creation-id.")

        if args.carousel_dir:
            if args.schedule_at and args.publish_now:
                raise ValueError("Use apenas um: --schedule-at ou --publish-now.")

            images = list_carousel_images(args.carousel_dir, max_items=args.carousel_max_items)
            print(f"Imagens encontradas para carrossel: {len(images)}")

            caption_text = args.caption.strip()
            if not caption_text:
                caption_text = read_carousel_caption(args.carousel_dir)

            context_text = " ".join(p.stem for p in images)
            if args.auto_caption or not caption_text:
                hint = context_text or Path(args.carousel_dir).name
                caption_text = generate_caption(args.caption_style, hint, args.hashtags_max)
                print(f"Legenda automatica gerada ({args.caption_style}).")

            children_ids: list[str] = []
            for idx, image_file in enumerate(images, start=1):
                image_url = upload_file_to_tmpfiles(str(image_file))
                print(f"[{idx}/{len(images)}] Imagem enviada: {image_file.name}")
                child_id = create_carousel_item_container(
                    ig_user_id=args.ig_user_id,
                    access_token=args.access_token,
                    image_url=image_url,
                )
                print(f"[{idx}/{len(images)}] Item container criado: {child_id}")
                wait_until_media_ready(child_id, args.access_token, args.wait_timeout)
                children_ids.append(child_id)

            carousel_container_id = create_carousel_container(
                ig_user_id=args.ig_user_id,
                access_token=args.access_token,
                caption=caption_text,
                children_ids=children_ids,
                schedule_at=args.schedule_at,
            )
            print(f"Container de carrossel criado: {carousel_container_id}")

            if args.publish_now:
                wait_until_media_ready(carousel_container_id, args.access_token, args.wait_timeout)
                post_id = publish_media(args.ig_user_id, args.access_token, carousel_container_id)
                print(f"Carrossel publicado com sucesso. Post ID: {post_id}")
            elif args.schedule_at:
                print("Carrossel agendado com sucesso.")
            else:
                print("Container pronto. Use --publish-now ou rode novamente com --creation-id.")
            return 0

        if args.post_all_from_dir:
            if not args.from_dir:
                raise ValueError("Use --from-dir junto com --post-all-from-dir.")
            media_files = list_media_from_dir(args.from_dir)
            print(f"Total de midias para publicar: {len(media_files)}")
            ok = 0
            failed = 0

            for idx, selected in enumerate(media_files, start=1):
                print(f"\n[{idx}/{len(media_files)}] Publicando: {selected.name}")
                ext = selected.suffix.lower()
                caption_text = args.caption
                context_text = read_related_description(selected) or selected.name
                if not args.story and (args.auto_caption or not caption_text.strip()):
                    caption_text = generate_caption(args.caption_style, context_text, args.hashtags_max)
                    print(f"Legenda automatica gerada ({args.caption_style}).")

                image_url = None
                video_url = None
                if ext in VIDEO_EXTENSIONS:
                    video_url = upload_file_to_tmpfiles(str(selected))
                    print(f"Video local enviado para URL temporaria: {video_url}")
                elif ext in IMAGE_EXTENSIONS:
                    if args.story:
                        print(f"Ignorado (Stories aceitam apenas video neste script): {selected.name}")
                        failed += 1
                        continue
                    image_url = upload_file_to_tmpfiles(str(selected))
                    print(f"Imagem local enviada para URL temporaria: {image_url}")
                else:
                    print(f"Ignorado (extensao nao suportada): {selected.name}")
                    failed += 1
                    continue

                try:
                    container_id = create_media_container(
                        ig_user_id=args.ig_user_id,
                        access_token=args.access_token,
                        caption=caption_text,
                        image_url=image_url,
                        video_url=video_url,
                        schedule_at=args.schedule_at,
                        story=args.story,
                    )
                    print(f"Container criado: {container_id}")

                    if not args.schedule_at:
                        if video_url:
                            wait_until_media_ready(container_id, args.access_token, args.wait_timeout)
                        post_id = publish_media(args.ig_user_id, args.access_token, container_id)
                        print(f"Publicado com sucesso. Post ID: {post_id}")
                    else:
                        print("Agendado com sucesso (sem publicar agora).")
                    ok += 1
                except (ValueError, RuntimeError) as item_exc:
                    print(f"Falhou em {selected.name}: {item_exc}")
                    failed += 1

                if idx < len(media_files):
                    time.sleep(max(0, args.interval_seconds))

            print(f"\nResumo: {ok} publicado(s), {failed} falha(s), total {len(media_files)}.")
            return 0 if failed == 0 else 1

        if args.creation_id:
            if args.wait_for_ready:
                wait_until_media_ready(args.creation_id, args.access_token, args.wait_timeout)
            post_id = publish_media(args.ig_user_id, args.access_token, args.creation_id)
            print(f"Publicado com sucesso. Post ID: {post_id}")
            return 0

        video_url = args.video_url
        image_url = args.image_url

        selected_media: Path | None = None
        context_text = ""
        if args.from_dir:
            selected = pick_media_from_dir(args.from_dir, args.file_name)
            selected_media = selected
            print(f"Midia selecionada da pasta: {selected}")
            ext = selected.suffix.lower()
            if ext in VIDEO_EXTENSIONS:
                args.video_path = str(selected)
            elif ext in IMAGE_EXTENSIONS:
                if args.story:
                    raise ValueError("Stories neste script aceitam apenas video. Selecione um arquivo de video.")
                uploaded_image_url = upload_file_to_tmpfiles(str(selected))
                image_url = uploaded_image_url
                print(f"Imagem local enviada para URL temporaria: {image_url}")
            else:
                raise ValueError(f"Extensao nao suportada: {ext}")
            context_text = read_related_description(selected)

        if args.video_path:
            video_url = upload_file_to_tmpfiles(args.video_path)
            print(f"Video local enviado para URL temporaria: {video_url}")
            if not context_text:
                context_text = read_related_description(Path(args.video_path))

        if not context_text and selected_media:
            context_text = selected_media.name

        if not args.story and (args.auto_caption or (not args.caption.strip())):
            hint = context_text or (selected_media.name if selected_media else "video de IA e automacao")
            args.caption = generate_caption(args.caption_style, hint, args.hashtags_max)
            print(f"Legenda automatica gerada ({args.caption_style}).")

        container_id = create_media_container(
            ig_user_id=args.ig_user_id,
            access_token=args.access_token,
            caption=args.caption,
            image_url=image_url,
            video_url=video_url,
            schedule_at=args.schedule_at,
            story=args.story,
        )
        print(f"Container criado com sucesso. Creation ID: {container_id}")

        if args.publish_now:
            if args.video_url or args.video_path:
                wait_until_media_ready(container_id, args.access_token, args.wait_timeout)
            post_id = publish_media(args.ig_user_id, args.access_token, container_id)
            print(f"Publicado com sucesso. Post ID: {post_id}")
        else:
            print("Container pronto. Use --publish-now ou rode novamente com --creation-id.")

        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"Erro: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
