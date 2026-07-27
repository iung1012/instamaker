import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
INSTAGRAM_REELS_TARGET = "instagram_reels"
INSTAGRAM_STORIES_TARGET = "instagram_stories"
INSTAGRAM_CAROUSEL_TARGET = "instagram_carousel"
YOUTUBE_SHORTS_TARGET = "youtube_shorts"
TIKTOK_TARGET = "tiktok"
ENV_PATH = Path(__file__).resolve().parent / ".env"
TARGET_STATE_KEYS = [
    INSTAGRAM_REELS_TARGET,
    INSTAGRAM_STORIES_TARGET,
    INSTAGRAM_CAROUSEL_TARGET,
    YOUTUBE_SHORTS_TARGET,
    TIKTOK_TARGET,
]


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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def abs_path(base: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return (base / candidate).resolve()


def load_state(path: Path) -> dict:
    default_state = {
        "processed_sources": [],
        "published_outputs": [],
        "published_outputs_by_target": {key: [] for key in TARGET_STATE_KEYS},
        "last_run_utc": "",
        "last_success_utc": "",
    }
    if not path.exists():
        return default_state

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_state

    for key, default_value in default_state.items():
        loaded.setdefault(key, default_value)
    return loaded


def normalize_state(state: dict) -> dict:
    legacy_published = state.get("published_outputs", [])
    by_target = state.get("published_outputs_by_target")

    if not isinstance(legacy_published, list):
        legacy_published = []
    if not isinstance(by_target, dict):
        by_target = {}

    legacy_instagram_outputs = by_target.get("instagram")
    reels_outputs = by_target.get(INSTAGRAM_REELS_TARGET)
    stories_outputs = by_target.get(INSTAGRAM_STORIES_TARGET)
    carousel_outputs = by_target.get(INSTAGRAM_CAROUSEL_TARGET)
    youtube_shorts_outputs = by_target.get(YOUTUBE_SHORTS_TARGET)
    tiktok_outputs = by_target.get(TIKTOK_TARGET)

    if not isinstance(legacy_instagram_outputs, list):
        legacy_instagram_outputs = []
    if not isinstance(reels_outputs, list):
        reels_outputs = []
    if not isinstance(stories_outputs, list):
        stories_outputs = []
    if not isinstance(carousel_outputs, list):
        carousel_outputs = []
    if not isinstance(youtube_shorts_outputs, list):
        youtube_shorts_outputs = []
    if not isinstance(tiktok_outputs, list):
        tiktok_outputs = []

    legacy_instagram = list(dict.fromkeys(legacy_instagram_outputs + legacy_published))
    if legacy_instagram and not reels_outputs:
        reels_outputs = list(legacy_instagram)
    if legacy_instagram and not carousel_outputs:
        carousel_outputs = list(legacy_instagram)

    by_target = {
        INSTAGRAM_REELS_TARGET: reels_outputs,
        INSTAGRAM_STORIES_TARGET: stories_outputs,
        INSTAGRAM_CAROUSEL_TARGET: carousel_outputs,
        YOUTUBE_SHORTS_TARGET: youtube_shorts_outputs,
        TIKTOK_TARGET: tiktok_outputs,
    }

    state["published_outputs_by_target"] = by_target
    state["published_outputs"] = sorted(set().union(*(set(by_target[key]) for key in TARGET_STATE_KEYS)))
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def run_command(command: list[str], cwd: Path, dry_run: bool = False, allow_failure: bool = False) -> int:
    rendered = " ".join(f'"{c}"' if " " in c else c for c in command)
    print(f"\n$ {rendered}")

    if dry_run:
        return 0

    result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)

    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip())

    if result.returncode != 0 and not allow_failure:
        raise RuntimeError(f"Comando falhou com codigo {result.returncode}: {rendered}")

    return result.returncode


def list_source_videos(source_dir: Path) -> list[Path]:
    if not source_dir.exists():
        return []
    files = [
        p
        for p in source_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and not p.name.endswith("_final.mp4")
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def list_carousel_packages(carousels_dir: Path) -> list[Path]:
    if not carousels_dir.exists():
        return []
    packages = [
        p
        for p in carousels_dir.iterdir()
        if p.is_dir() and p.name.startswith("carousel_")
    ]
    return sorted(packages, key=lambda p: p.stat().st_mtime)


def clear_directory_contents(
    directory: Path,
    project_dir: Path,
    dry_run: bool = False,
) -> int:
    directory = directory.resolve()
    project_dir = project_dir.resolve()

    try:
        project_dir.relative_to(directory)
    except ValueError:
        pass
    else:
        raise ValueError(
            f"Limpeza recusada: {directory} e a raiz do projeto ou uma pasta acima dela."
        )

    if not directory.exists():
        if dry_run:
            print(f"[dry-run] Pasta seria criada vazia: {directory}")
        else:
            directory.mkdir(parents=True, exist_ok=True)
        return 0

    if not directory.is_dir():
        raise ValueError(f"Caminho de limpeza nao e uma pasta: {directory}")

    entries = list(directory.iterdir())
    if dry_run:
        print(f"[dry-run] Conteudo seria removido de {directory}: {len(entries)} item(ns)")
        return len(entries)

    for entry in entries:
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry)

    print(f"Pasta limpa: {directory} ({len(entries)} item(ns) removido(s))")
    return len(entries)


def cleanup_after_publish(
    published_media: Path,
    timeline_dir: Path,
    keep_published_files: bool,
) -> None:
    if keep_published_files:
        return

    candidates: list[Path] = [published_media]
    stem = published_media.stem

    if stem.endswith("_final"):
        source_stem = stem[: -len("_final")]
        for ext in VIDEO_EXTENSIONS:
            source_candidate = timeline_dir / f"{source_stem}{ext}"
            if source_candidate.exists() and source_candidate.is_file():
                candidates.append(source_candidate)

        info_candidate = timeline_dir / f"{source_stem}_info.txt"
        if info_candidate.exists() and info_candidate.is_file():
            candidates.append(info_candidate)

    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not path.exists() or not path.is_file():
            continue
        path.unlink()
        print(f"Arquivo removido apos publicacao: {path}")


def cleanup_carousel_package(package_dir: Path, keep_published_files: bool) -> None:
    if keep_published_files:
        return
    if not package_dir.exists() or not package_dir.is_dir():
        return

    for file in package_dir.iterdir():
        if file.is_file():
            file.unlink()
    package_dir.rmdir()
    print(f"Pacote de carrossel removido apos publicacao: {package_dir}")


def purge_old_files(directory: Path, max_age_days: int = 7, dry_run: bool = False) -> int:
    """Remove arquivos da pasta especificada cuja data de modificacao seja mais antiga que max_age_days."""
    if not directory.exists() or not directory.is_dir() or max_age_days <= 0:
        return 0

    now = datetime.now(timezone.utc).timestamp()
    max_age_seconds = max_age_days * 86400
    removed_count = 0

    for entry in list(directory.rglob("*")):
        if not entry.is_file():
            continue
        try:
            mtime = entry.stat().st_mtime
            if (now - mtime) > max_age_seconds:
                if dry_run:
                    print(f"[dry-run] Purga por idade: {entry.name} ({max_age_days}+ dias)")
                else:
                    entry.unlink()
                    print(f"Purga por idade: {entry.name} removido ({max_age_days}+ dias)")
                removed_count += 1
        except OSError:
            pass

    return removed_count


def build_requested_publish_keys(publish_kind: str) -> list[str]:
    keys: list[str] = []
    if publish_kind in {"reel", "both"}:
        keys.append(INSTAGRAM_REELS_TARGET)
    if publish_kind in {"story", "both"}:
        keys.append(INSTAGRAM_STORIES_TARGET)
    return keys


def all_targets_published(
    media_name: str,
    published_by_target: dict[str, set[str]],
    requested_targets: list[str],
) -> bool:
    return all(
        media_name in published_by_target[target]
        for target in requested_targets
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pipeline automatica: captura, composicao e publicacao no Instagram."
    )
    parser.add_argument("--project-dir", default=".", help="Diretorio raiz do projeto")
    parser.add_argument("--python-bin", default=sys.executable, help="Python para executar os scripts")
    parser.add_argument("--state-file", default=".automation_state.json", help="Arquivo de estado")
    parser.add_argument(
        "--mode",
        choices=["reels", "carousel"],
        default="reels",
        help="Modo da pipeline: reels (video) ou carousel (carrossel de imagens).",
    )
    parser.add_argument("--timeline-dir", default="timeline_downloads", help="Pasta com videos capturados")
    parser.add_argument("--outputs-dir", default="outputs_ig", help="Pasta de saida dos videos finais")
    parser.add_argument(
        "--carousels-dir",
        default="outputs_ig/carousels",
        help="Pasta de pacotes de carrossel gerados.",
    )
    parser.add_argument("--avatar", default="avatar_video.mp4", help="Avatar usado no compose")
    parser.add_argument(
        "--bg-music",
        default="",
        help="Musica de fundo. Vazio (padrao) mantem so o audio original do video.",
    )
    parser.add_argument("--bg-volume", type=float, default=0.10, help="Volume da musica de fundo")
    parser.add_argument(
        "--music-only",
        action="store_true",
        help="Substitui o audio original pela musica de fundo. Exige --bg-music.",
    )
    parser.add_argument(
        "--keep-original-audio",
        action="store_true",
        help="Aceito por compatibilidade: o audio original ja e mantido por padrao.",
    )
    parser.add_argument("--intro-seconds", type=float, default=1.4, help="Duracao do gancho inicial")
    parser.add_argument("--outro-seconds", type=float, default=1.8, help="Duracao do CTA final")
    parser.add_argument(
        "--text-box-opacity",
        type=float,
        default=0.0,
        help="Opacidade das caixas atras dos textos (0 remove os quadros).",
    )
    parser.add_argument("--max-compose", type=int, default=3, help="Maximo de novos videos para compor por execucao")
    parser.add_argument(
        "--max-carousels",
        type=int,
        default=1,
        help="Maximo de novos carrosseis para gerar por execucao.",
    )
    parser.add_argument("--carousel-slides", type=int, default=7, help="Quantidade de slides do carrossel (2-10).")
    parser.add_argument(
        "--profile-name",
        default="@seu_perfil",
        help="Assinatura exibida nos slides do carrossel.",
    )
    parser.add_argument(
        "--profile-keywords",
        default="programacao,tecnologia,ia,python,automacao",
        help="Palavras-chave do nicho para gerar carrossel (separadas por virgula).",
    )
    parser.add_argument(
        "--profile-focus",
        default="ensinar programacao, tecnologia e IA com exemplos praticos",
        help="Objetivo principal do perfil usado no texto dos slides.",
    )
    parser.add_argument(
        "--fallback-theme",
        default="Engenharia",
        help="Tema padrao se nao houver contexto suficiente.",
    )
    parser.add_argument("--caption-style", choices=["classic", "premium", "growth"], default="growth")
    parser.add_argument("--skip-scrape", action="store_true", help="Pula captura da timeline")
    parser.add_argument("--skip-compose", action="store_true", help="Pula composicao de videos")
    parser.add_argument("--skip-publish", action="store_true", help="Pula qualquer publicacao")
    parser.add_argument(
        "--skip-instagram",
        action="store_true",
        default=parse_bool(os.getenv("SKIP_INSTAGRAM"), False),
        help="Nao publica no Instagram. Use com --publish-youtube-shorts para publicar apenas no YouTube.",
    )
    parser.add_argument(
        "--publish-kind",
        choices=["reel", "story", "both"],
        default="reel",
        help="Formato no Instagram: reel, story ou both para publicar nos dois.",
    )
    parser.add_argument(
        "--publish-count",
        type=int,
        default=1,
        help="Quantidade maxima de midias a publicar por execucao.",
    )
    parser.add_argument(
        "--publish-youtube-shorts",
        action="store_true",
        default=parse_bool(os.getenv("PUBLISH_YOUTUBE_SHORTS"), False),
        help="Publica os videos gerados tambem como YouTube Shorts.",
    )
    parser.add_argument(
        "--youtube-privacy-status",
        choices=["private", "unlisted", "public"],
        default=os.getenv("YOUTUBE_PRIVACY_STATUS", "public"),
        help="Privacidade dos uploads no YouTube.",
    )
    parser.add_argument(
        "--youtube-title-prefix",
        default=os.getenv("YOUTUBE_SHORTS_TITLE_PREFIX", ""),
        help="Prefixo opcional para titulos automaticos dos Shorts.",
    )
    parser.add_argument(
        "--youtube-description",
        default=os.getenv("YOUTUBE_SHORTS_DESCRIPTION", ""),
        help="Descricao fixa para Shorts. Se vazia, usa o _info.txt relacionado quando existir.",
    )
    parser.add_argument(
        "--youtube-tags",
        default=os.getenv("YOUTUBE_SHORTS_TAGS", "Shorts,IA,automacao,tecnologia,programacao"),
        help="Tags do YouTube separadas por virgula.",
    )
    parser.add_argument(
        "--youtube-category-id",
        default=os.getenv("YOUTUBE_CATEGORY_ID", "28"),
        help="Categoria YouTube. 28 = Science & Technology.",
    )
    parser.add_argument(
        "--youtube-made-for-kids",
        action="store_true",
        default=parse_bool(os.getenv("YOUTUBE_MADE_FOR_KIDS"), False),
        help="Marca o upload como conteudo feito para criancas.",
    )
    parser.add_argument(
        "--youtube-notify-subscribers",
        action="store_true",
        default=parse_bool(os.getenv("YOUTUBE_NOTIFY_SUBSCRIBERS"), False),
        help="Notifica inscritos sobre os uploads no YouTube.",
    )
    parser.add_argument("--strict-scrape", action="store_true", help="Falha a execucao se a captura falhar")
    parser.add_argument("--dry-run", action="store_true", help="Mostra comandos sem executar")
    parser.add_argument(
        "--keep-published-files",
        action="store_true",
        help="Nao remove arquivos locais apos publicar.",
    )
    parser.add_argument(
        "--purge-days",
        type=int,
        default=int(os.getenv("PURGE_DAYS", "0")),
        help="Remove arquivos temporarios/logs mais antigos que N dias (0 = desativado).",
    )
    parser.add_argument(
        "--publish-tiktok",
        action="store_true",
        default=parse_bool(os.getenv("PUBLISH_TIKTOK"), False),
        help="Publica os videos gerados tambem no TikTok.",
    )
    parser.add_argument(
        "--tiktok-method",
        choices=["cookie", "api"],
        default=os.getenv("TIKTOK_METHOD", "cookie"),
        help="Metodo de publicacao no TikTok: cookie (tiktok-uploader via Playwright) ou api (API Oficial).",
    )
    parser.add_argument(
        "--tiktok-cookies",
        default=os.getenv("TIKTOK_COOKIES_FILE", "tiktok_cookies.txt"),
        help="Arquivo de cookies para o metodo de publicacao via cookies no TikTok.",
    )
    return parser


def main() -> int:
    load_env_file()
    parser = build_parser()
    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve()
    if "\\" in args.python_bin or "/" in args.python_bin or args.python_bin.endswith(".exe"):
        python_bin = abs_path(project_dir, args.python_bin)
    else:
        python_bin = Path(args.python_bin)
    state_path = abs_path(project_dir, args.state_file)
    timeline_dir = abs_path(project_dir, args.timeline_dir)
    outputs_dir = abs_path(project_dir, args.outputs_dir)
    carousels_dir = abs_path(project_dir, args.carousels_dir)
    avatar_path = abs_path(project_dir, args.avatar)
    bg_music_path = abs_path(project_dir, args.bg_music) if args.bg_music else None

    state = normalize_state(load_state(state_path))
    processed_sources = set(state.get("processed_sources", []))
    published_by_target = {
        key: set(state.get("published_outputs_by_target", {}).get(key, []))
        for key in TARGET_STATE_KEYS
    }
    if args.mode == "carousel":
        requested_targets = [] if args.skip_instagram else [INSTAGRAM_CAROUSEL_TARGET]
    else:
        requested_targets = []
        if not args.skip_instagram:
            requested_targets.extend(build_requested_publish_keys(args.publish_kind))
        if args.publish_youtube_shorts:
            requested_targets.append(YOUTUBE_SHORTS_TARGET)
        if args.publish_tiktok:
            requested_targets.append(TIKTOK_TARGET)
    composed_outputs: list[Path] = []
    composed_source_names: dict[str, str] = {}

    if args.publish_count < 1:
        parser.error("--publish-count deve ser 1 ou maior.")
    if not args.skip_publish and args.mode == "carousel" and (args.publish_youtube_shorts or args.publish_tiktok):
        parser.error("--publish-youtube-shorts e --publish-tiktok estao disponiveis apenas no modo reels.")
    if not args.skip_publish and not requested_targets:
        parser.error("Nenhum destino de publicacao selecionado.")

    print(f"Inicio da pipeline: {utc_now_iso()}")
    print(f"Projeto: {project_dir}")
    print(f"State file: {state_path}")
    print(f"Modo: {args.mode}")
    if args.skip_publish:
        print("Publicacao: desativada")
    else:
        destination_names = []
        if not args.skip_instagram:
            destination_names.append("instagram")
        if args.publish_youtube_shorts:
            destination_names.append("youtube_shorts")
        if args.publish_tiktok:
            destination_names.append(f"tiktok ({args.tiktok_method})")
        print(f"Destinos de publicacao: {', '.join(destination_names)}")
    if not args.skip_instagram:
        print(f"Formato Instagram: {args.publish_kind}")
    if args.publish_youtube_shorts:
        print(f"YouTube Shorts: privacy={args.youtube_privacy_status}")
    if args.publish_tiktok:
        print(f"TikTok: metodo={args.tiktok_method}")
    print(f"Maximo de publicacoes por execucao: {args.publish_count}")

    try:
        if args.mode == "carousel" and not args.skip_instagram and args.publish_kind != "reel":
            raise ValueError("O modo carousel nao usa Stories. Use --publish-kind reel.")

        print("\nLimpando arquivos da execucao anterior...")
        clear_directory_contents(timeline_dir, project_dir, dry_run=args.dry_run)
        clear_directory_contents(outputs_dir, project_dir, dry_run=args.dry_run)
        if args.purge_days > 0:
            print(f"Executando purga por idade (>{args.purge_days} dias)...")
            logs_dir = project_dir / "logs"
            purge_old_files(logs_dir, max_age_days=args.purge_days, dry_run=args.dry_run)

        if not args.skip_scrape:
            scrape_limit = args.max_carousels if args.mode == "carousel" else args.max_compose
            if scrape_limit < 1:
                scrape_limit = args.publish_count
            scrape_cmd = [
                str(python_bin),
                "twitter_timeline_scraper.py",
                "--max-videos",
                str(scrape_limit),
                "--state-file",
                str(state_path),
            ]
            rc = run_command(scrape_cmd, cwd=project_dir, dry_run=args.dry_run, allow_failure=not args.strict_scrape)
            if rc != 0 and args.strict_scrape:
                return rc

        if not args.skip_compose:
            timeline_dir.mkdir(parents=True, exist_ok=True)
            if args.mode == "carousel":
                carousels_dir.mkdir(parents=True, exist_ok=True)
                carousel_cmd = [
                    str(python_bin),
                    "carousel_generator.py",
                    "--project-dir",
                    str(project_dir),
                    "--output-dir",
                    str(carousels_dir),
                    "--source-info-dir",
                    str(timeline_dir),
                    "--profile-name",
                    args.profile_name,
                    "--profile-keywords",
                    args.profile_keywords,
                    "--profile-focus",
                    args.profile_focus,
                    "--fallback-theme",
                    args.fallback_theme,
                    "--slides",
                    str(args.carousel_slides),
                    "--count",
                    str(args.max_carousels),
                ]
                run_command(carousel_cmd, cwd=project_dir, dry_run=args.dry_run)
            else:
                outputs_dir.mkdir(parents=True, exist_ok=True)

                source_videos = list_source_videos(timeline_dir)
                new_sources = [p for p in source_videos if p.name not in processed_sources]
                if args.max_compose > 0:
                    new_sources = new_sources[: args.max_compose]

                print(f"Novos videos para compor: {len(new_sources)}")

                for source_video in new_sources:
                    final_name = f"{source_video.stem}_final.mp4"
                    final_output = outputs_dir / final_name

                    compose_cmd = [
                        str(python_bin),
                        "compose_test_video.py",
                        "--video",
                        str(source_video),
                        "--avatar",
                        str(avatar_path),
                        "--intro-seconds",
                        str(args.intro_seconds),
                        "--outro-seconds",
                        str(args.outro_seconds),
                        "--text-box-opacity",
                        str(args.text_box_opacity),
                        "--output",
                        str(final_output),
                    ]
                    # Sem --bg-music o video sai com o audio original, sem
                    # nenhuma trilha por cima.
                    if bg_music_path is not None:
                        compose_cmd.extend(
                            ["--bg-music", str(bg_music_path), "--bg-volume", str(args.bg_volume)]
                        )
                        if args.music_only:
                            compose_cmd.append("--music-only")
                    run_command(compose_cmd, cwd=project_dir, dry_run=args.dry_run)
                    composed_outputs.append(final_output)
                    composed_source_names[final_output.name] = source_video.name
                    if args.dry_run:
                        continue
                    else:
                        if not final_output.exists():
                            raise RuntimeError(f"Video final nao foi criado: {final_output}")
                        if args.skip_publish:
                            processed_sources.add(source_video.name)

        if not args.skip_publish:
            if args.mode == "carousel":
                packages = list_carousel_packages(carousels_dir)
                unpublished_packages = [p for p in packages if p.name not in published_by_target[INSTAGRAM_CAROUSEL_TARGET]]

                if not unpublished_packages:
                    print("Nenhum carrossel novo para publicar nesta execucao.")
                else:
                    next_package = unpublished_packages[0]
                    print(f"Proximo carrossel para publicar: {next_package.name}")

                    publish_cmd = [
                        str(python_bin),
                        "instagram_graph_publisher.py",
                        "--carousel-dir",
                        str(next_package),
                        "--publish-now",
                        "--caption-style",
                        args.caption_style,
                    ]
                    run_command(publish_cmd, cwd=project_dir, dry_run=args.dry_run)
                    if not args.dry_run:
                        published_by_target[INSTAGRAM_CAROUSEL_TARGET].add(next_package.name)
                        cleanup_carousel_package(
                            package_dir=next_package,
                            keep_published_files=args.keep_published_files,
                        )
            else:
                publishable = [
                    path
                    for path in composed_outputs
                    if path.suffix.lower() in VIDEO_EXTENSIONS
                ]
                if publishable:
                    print(f"Videos gerados nesta execucao para publicacao: {len(publishable)}")
                else:
                    print(
                        "Nenhum video foi gerado nesta execucao. "
                        "Nao ha arquivos para publicar."
                    )

                for media in publishable:
                    if all_targets_published(
                        media.name,
                        published_by_target,
                        requested_targets,
                    ):
                        source_name = composed_source_names.get(media.name)
                        if source_name:
                            processed_sources.add(source_name)
                        if not args.dry_run:
                            cleanup_after_publish(
                                published_media=media,
                                timeline_dir=timeline_dir,
                                keep_published_files=args.keep_published_files,
                            )

                unpublished = [p for p in publishable if any(p.name not in published_by_target[target] for target in requested_targets)]

                if not unpublished:
                    print("Nenhuma midia nova para publicar nesta execucao.")
                else:
                    selected_batch = unpublished[: args.publish_count]
                    print(f"Midias selecionadas para publicar: {len(selected_batch)}")

                    for index, next_media in enumerate(selected_batch, start=1):
                        print(f"\n[{index}/{len(selected_batch)}] Proxima midia para publicar: {next_media.name}")

                        targets_for_media = [
                            target
                            for target in requested_targets
                            if next_media.name not in published_by_target[target]
                        ]

                        for target in targets_for_media:
                            if target == INSTAGRAM_REELS_TARGET:
                                publish_cmd = [
                                    str(python_bin),
                                    "instagram_graph_publisher.py",
                                    "--from-dir",
                                    str(outputs_dir),
                                    "--file-name",
                                    next_media.name,
                                    "--publish-now",
                                    "--auto-caption",
                                    "--caption-style",
                                    args.caption_style,
                                ]
                            elif target == INSTAGRAM_STORIES_TARGET:
                                publish_cmd = [
                                    str(python_bin),
                                    "instagram_graph_publisher.py",
                                    "--from-dir",
                                    str(outputs_dir),
                                    "--file-name",
                                    next_media.name,
                                    "--story",
                                    "--publish-now",
                                ]
                            elif target == YOUTUBE_SHORTS_TARGET:
                                publish_cmd = [
                                    str(python_bin),
                                    "youtube_shorts_publisher.py",
                                    "--video",
                                    str(next_media),
                                    "--privacy-status",
                                    args.youtube_privacy_status,
                                    "--category-id",
                                    args.youtube_category_id,
                                    "--tags",
                                    args.youtube_tags,
                                ]
                                if args.youtube_title_prefix:
                                    publish_cmd.extend(["--title-prefix", args.youtube_title_prefix])
                                if args.youtube_description:
                                    publish_cmd.extend(["--description", args.youtube_description])
                                if args.youtube_made_for_kids:
                                    publish_cmd.append("--made-for-kids")
                                if args.youtube_notify_subscribers:
                                    publish_cmd.append("--notify-subscribers")
                            elif target == TIKTOK_TARGET:
                                if args.tiktok_method == "cookie":
                                    publish_cmd = [
                                        str(python_bin),
                                        "tiktok_cookie_publisher.py",
                                        "--video",
                                        str(next_media),
                                        "--cookies",
                                        args.tiktok_cookies,
                                    ]
                                else:
                                    publish_cmd = [
                                        str(python_bin),
                                        "tiktok_sandbox_uploader.py",
                                        "--token-only",
                                        "--video",
                                        str(next_media),
                                    ]
                            else:
                                raise ValueError(f"Destino de publicacao desconhecido: {target}")

                            rc = run_command(publish_cmd, cwd=project_dir, dry_run=args.dry_run)
                            if not args.dry_run:
                                published_by_target[target].add(next_media.name)

                        if not args.dry_run and all_targets_published(
                            next_media.name,
                            published_by_target,
                            requested_targets,
                        ):
                            source_name = composed_source_names.get(next_media.name)
                            if source_name:
                                processed_sources.add(source_name)
                            cleanup_after_publish(
                                published_media=next_media,
                                timeline_dir=timeline_dir,
                                keep_published_files=args.keep_published_files,
                            )

        if args.dry_run:
            print("\nDry-run finalizado. Nenhuma alteracao de estado foi salva.")
            return 0

        state["processed_sources"] = sorted(processed_sources)
        state["published_outputs_by_target"] = {
            key: sorted(published_by_target[key])
            for key in TARGET_STATE_KEYS
        }
        state["published_outputs"] = sorted(
            set().union(*(set(state["published_outputs_by_target"][key]) for key in TARGET_STATE_KEYS))
        )
        state["last_run_utc"] = utc_now_iso()
        state["last_success_utc"] = state["last_run_utc"]
        save_state(state_path, state)

        print("\nPipeline finalizada com sucesso.")
        return 0
    except Exception as exc:
        if not args.dry_run:
            state["processed_sources"] = sorted(processed_sources)
            state["published_outputs_by_target"] = {
                key: sorted(published_by_target[key])
                for key in TARGET_STATE_KEYS
            }
            state["published_outputs"] = sorted(
                set().union(*(set(state["published_outputs_by_target"][key]) for key in TARGET_STATE_KEYS))
            )
            state["last_run_utc"] = utc_now_iso()
            save_state(state_path, state)
        print(f"\nErro na pipeline: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
