"""Biblioteca de personagens usados no painel de baixo do video.

Um personagem e um arquivo de video (ou imagem) dentro de `characters/`,
ou uma subpasta com varias variacoes do mesmo personagem:

    characters/
      mascarado/          <- personagem "mascarado" com 3 variacoes
        mascarado_1.mov
        mascarado_2.mp4
        mascarado_3.mp4
      outro.mp4           <- personagem "outro" com um unico clipe

Quando o personagem e uma pasta, um dos arquivos e sorteado a cada render,
para os posts nao repetirem sempre a mesma imagem.

Sem pasta `characters/`, cai no `avatar_video.mp4` da raiz, entao o projeto
continua funcionando exatamente como antes de existir biblioteca.
"""

import json
import os
import random
from pathlib import Path

CHARACTERS_DIRNAME = "characters"
LEGACY_AVATAR = "avatar_video.mp4"
MEDIA_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".png", ".jpg", ".jpeg"}
STATE_FILENAME = ".character_state.json"


def characters_dir(project_dir: Path) -> Path:
    return Path(project_dir) / CHARACTERS_DIRNAME


def _media_files(folder: Path) -> list[Path]:
    return sorted(
        (
            item
            for item in folder.iterdir()
            if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS
        ),
        key=lambda p: p.name.lower(),
    )


def list_characters(project_dir: Path) -> list[Path]:
    """Entradas da biblioteca (arquivo solto ou pasta com variacoes), em ordem alfabetica."""
    base = characters_dir(project_dir)
    if not base.is_dir():
        legacy = Path(project_dir) / LEGACY_AVATAR
        return [legacy] if legacy.is_file() else []

    found = []
    for item in base.iterdir():
        if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS:
            found.append(item)
        elif item.is_dir() and _media_files(item):
            found.append(item)
    if not found:
        legacy = Path(project_dir) / LEGACY_AVATAR
        return [legacy] if legacy.is_file() else []
    return sorted(found, key=lambda p: p.name.lower())


def variants(entry: Path) -> list[Path]:
    """Arquivos concretos de um personagem (o proprio, ou o conteudo da pasta)."""
    entry = Path(entry)
    if entry.is_dir():
        return _media_files(entry)
    return [entry]


def entry_for(project_dir: Path, path: Path) -> Path:
    """Entrada da biblioteca dona de `path` (a subpasta, se for uma variacao)."""
    p = Path(path)
    if p.parent.parent == characters_dir(project_dir):
        return p.parent
    return p


def character_label(path: Path) -> str:
    p = Path(path)
    if p.suffix.lower() in MEDIA_EXTENSIONS:
        raw = p.parent.name if p.parent.parent.name == CHARACTERS_DIRNAME else p.stem
    else:
        raw = p.name
    return raw.replace("_", " ").replace("-", " ").strip() or raw


def resolve_character(project_dir: Path, name: str | None) -> Path | None:
    """Encontra um personagem pelo nome (sem extensao, sem diferenciar caixa)."""
    available = list_characters(project_dir)
    if not available:
        return None
    if not name:
        return None

    wanted = str(name).strip().lower()
    for item in available:
        if item.stem.lower() == wanted or character_label(item).lower() == wanted:
            return item
    for item in available:
        if wanted and wanted in item.stem.lower():
            return item
    return None


def _state_path(project_dir: Path) -> Path:
    return Path(project_dir) / STATE_FILENAME


RECENT_MEMORY = 3  # quantos usos recentes evitamos repetir


def _load_state(project_dir: Path) -> dict:
    path = _state_path(project_dir)
    if not path.is_file():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(project_dir: Path, state: dict) -> None:
    _state_path(project_dir).write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def recent_uses(project_dir: Path) -> list[str]:
    """Nomes dos ultimos arquivos usados, do mais antigo para o mais novo."""
    raw = _load_state(project_dir).get("recent")
    return [str(x) for x in raw] if isinstance(raw, list) else []


def record_use(project_dir: Path, path: Path | None) -> None:
    """Anota o arquivo usado para que os proximos renders nao o repitam."""
    if path is None:
        return
    state = _load_state(project_dir)
    history = [str(x) for x in state.get("recent", []) if isinstance(x, str)]
    name = Path(path).name
    history = [item for item in history if item != name]
    history.append(name)
    state["recent"] = history[-(RECENT_MEMORY * 2):]  # guarda folga, usa os 3 ultimos
    _save_state(project_dir, state)


def _drop_recent(options: list[Path], project_dir: Path) -> list[Path]:
    """Tira os ultimos usados -- mas nunca devolve lista vazia."""
    recent = set(recent_uses(project_dir)[-RECENT_MEMORY:])
    if not recent:
        return options
    fresh = [p for p in options if Path(p).name not in recent]
    return fresh or options


def load_default_character(project_dir: Path) -> Path | None:
    """Personagem fixado pelo usuario, se ainda existir."""
    path = _state_path(project_dir)
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return resolve_character(project_dir, state.get("default_character"))


def save_default_character(project_dir: Path, character: Path | None) -> None:
    path = _state_path(project_dir)
    payload = {"default_character": Path(character).stem if character else ""}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def pick_character(
    project_dir: Path,
    name: str | None = None,
    exclude: Path | None = None,
    rng: random.Random | None = None,
) -> Path | None:
    """Escolhe o arquivo concreto de um personagem.

    Ordem: nome pedido -> padrao fixado -> sorteio entre as entradas. Se a
    entrada escolhida tem varias variacoes, uma delas e sorteada. `exclude`
    serve ao botao "trocar personagem": evita repetir o arquivo ja mostrado —
    num personagem com variacoes isso troca a variacao; entre personagens de
    clipe unico, troca de personagem.
    """
    chooser = rng or random
    exclude = Path(exclude) if exclude is not None else None
    exclude_entry = entry_for(project_dir, exclude) if exclude is not None else None

    entry = None
    if name:
        entry = resolve_character(project_dir, name)

    if entry is None:
        default = load_default_character(project_dir)
        if default is not None:
            same = exclude_entry is not None and Path(default) == exclude_entry
            if not same or len(variants(default)) > 1:
                entry = default

    if entry is None:
        available = list_characters(project_dir)
        if not available:
            return None
        if exclude_entry is not None:
            remaining = [p for p in available if Path(p) != exclude_entry]
            if remaining:
                available = remaining
        available = _drop_recent(available, project_dir)
        entry = chooser.choice(available)

    options = variants(entry)
    if not options:
        return None
    if exclude is not None and len(options) > 1:
        remaining = [p for p in options if Path(p) != exclude]
        if remaining:
            options = remaining
    options = _drop_recent(options, project_dir)
    return chooser.choice(options)


def segment_start(video: Path, clip_seconds: float,
                  rng: random.Random | None = None) -> float:
    """Offset aleatorio para comecar o clipe do personagem.

    Com um unico arquivo em characters/ nao ha o que sortear, e o painel de baixo
    sai identico em todo post. Variar o trecho resolve isso sem depender de acervo:
    o mesmo mascarado aparece fazendo coisas diferentes.
    """
    video = Path(video)
    if video.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        return 0.0
    try:
        import subprocess

        from compose_test_video import resolve_executable

        out = subprocess.run(
            [resolve_executable("ffprobe"), "-v", "error", "-show_entries",
             "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=60,
        )
        total = float((out.stdout or "0").strip() or 0)
    except Exception:
        return 0.0

    folga = total - clip_seconds
    if folga <= 1.0:
        return 0.0
    return round((rng or random).uniform(0, folga), 2)


def describe_library(project_dir: Path) -> str:
    available = list_characters(project_dir)
    if not available:
        return "Nenhum personagem encontrado. Coloque videos em characters/."
    default = load_default_character(project_dir)
    lines = []
    for item in available:
        count = len(variants(item))
        extra = f" ({count} variacoes)" if count > 1 else ""
        mark = " (padrao)" if default and Path(item) == Path(default) else ""
        lines.append(f"- {character_label(item)}{extra}{mark}")
    return "\n".join(lines)
