"""Extrai frames do video do post para ilustrar os slides.

Deteccao de cena (`select='gt(scene,...)'`) devolve ZERO frames em screen recording,
que e o formato da maioria dos posts de demo -- entao amostramos a intervalo fixo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from compose_test_video import resolve_executable

SLOT_W = 924   # largura do slot de imagem no template
SLOT_H = 520


def duration(video: Path) -> float:
    try:
        out = subprocess.run(
            [resolve_executable("ffprobe"), "-v", "error", "-show_entries",
             "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=60,
        )
        return float((out.stdout or "0").strip() or 0)
    except (ValueError, OSError, subprocess.SubprocessError):
        return 0.0


def extract(video: Path, out_dir: Path, count: int = 4) -> list[Path]:
    """Pega `count` frames espalhados pelo video, ja no tamanho do slot.

    Video 16:9 cabe exato em 924x520; qualquer outra proporcao entra por
    `increase` + `crop`, que preenche o slot sem distorcer.
    """
    video = Path(video)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = duration(video)
    if total <= 0:
        return []

    # evita o primeiro e o ultimo instante: costumam ser fade ou tela vazia
    span = total * 0.86
    start = total * 0.07
    marks = [start + span * i / max(count - 1, 1) for i in range(count)]

    ffmpeg = resolve_executable("ffmpeg")
    written: list[Path] = []
    for index, at in enumerate(marks, start=1):
        dest = out_dir / f"frame{index}.png"
        cmd = [
            ffmpeg, "-v", "error", "-ss", f"{at:.2f}", "-i", str(video),
            "-frames:v", "1", "-vf",
            f"scale={SLOT_W}:{SLOT_H}:force_original_aspect_ratio=increase,"
            f"crop={SLOT_W}:{SLOT_H}",
            "-y", str(dest),
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        except (OSError, subprocess.SubprocessError):
            continue
        if dest.is_file():
            written.append(dest)
    return written
