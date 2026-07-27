"""Biblioteca de personagens usados no painel de baixo do video.

Um personagem e um arquivo de video (ou imagem) dentro de `characters/`.
O nome exibido e o nome do arquivo sem extensao, com `_` virando espaco.

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


def list_characters(project_dir: Path) -> list[Path]:
    """Personagens disponiveis, em ordem alfabetica."""
    base = characters_dir(project_dir)
    if not base.is_dir():
        legacy = Path(project_dir) / LEGACY_AVATAR
        return [legacy] if legacy.is_file() else []

    found = [
        item
        for item in base.iterdir()
        if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS
    ]
    if not found:
        legacy = Path(project_dir) / LEGACY_AVATAR
        return [legacy] if legacy.is_file() else []
    return sorted(found, key=lambda p: p.name.lower())


def character_label(path: Path) -> str:
    return Path(path).stem.replace("_", " ").replace("-", " ").strip() or Path(path).stem


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
    """Escolhe um personagem.

    Ordem: nome pedido -> padrao fixado -> sorteio. `exclude` serve ao botao
    "trocar personagem": evita repetir o que ja foi mostrado quando existe
    mais de uma opcao.
    """
    if name:
        chosen = resolve_character(project_dir, name)
        if chosen:
            return chosen

    default = load_default_character(project_dir)
    if default and (exclude is None or Path(default) != Path(exclude)):
        return default

    available = list_characters(project_dir)
    if not available:
        return None
    if exclude is not None:
        remaining = [p for p in available if Path(p) != Path(exclude)]
        if remaining:
            available = remaining
    return (rng or random).choice(available)


def describe_library(project_dir: Path) -> str:
    available = list_characters(project_dir)
    if not available:
        return "Nenhum personagem encontrado. Coloque videos em characters/."
    default = load_default_character(project_dir)
    lines = []
    for item in available:
        mark = " (padrao)" if default and Path(item) == Path(default) else ""
        lines.append(f"- {character_label(item)}{mark}")
    return "\n".join(lines)
