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
        entry = chooser.choice(available)

    options = variants(entry)
    if not options:
        return None
    if exclude is not None and len(options) > 1:
        remaining = [p for p in options if Path(p) != exclude]
        if remaining:
            options = remaining
    return chooser.choice(options)


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
