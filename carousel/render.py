"""Renderiza um deck de carrossel em PNGs 1080x1350 usando o template HTML.

O template e HTML/CSS porque a identidade visual depende de tipografia condensada
(Anton), grid milimetrado e marcas de corte -- reproduzir isso em Pillow custa caro
e fica pior. O playwright ja e dependencia do projeto.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

TEMPLATE = Path(__file__).with_name("template.html")
SLIDE_W = 1080
SLIDE_H = 1350


class RenderError(RuntimeError):
    pass


def _handle() -> str:
    """@ do perfil, estampado ao lado da paginacao em todos os slides."""
    raw = (os.getenv("CAROUSEL_HANDLE") or "").strip()
    if not raw:
        return ""
    return raw if raw.startswith("@") else f"@{raw}"


def render_deck(deck: dict, out_dir: Path) -> list[Path]:
    """Escreve out_dir/01.png ... conforme os slides do deck. Devolve os caminhos."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise RenderError(
            "playwright nao instalado. Rode: python -m pip install playwright "
            "&& python -m playwright install chromium"
        ) from exc

    slides = deck.get("slides") or []
    if not slides:
        raise RenderError("deck sem slides")

    deck = dict(deck)
    deck.setdefault("handle", _handle())

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page(
                viewport={"width": SLIDE_W, "height": SLIDE_H}, device_scale_factor=1
            )
            page.goto(TEMPLATE.resolve().as_uri())
            count = page.evaluate("d => window.mount(d)", deck)
            if not count:
                raise RenderError("o template nao montou nenhum slide")

            # As webfonts vem da rede: sem esperar, Anton cai para Arial e o layout
            # inteiro desmonta. Se a fonte nao chegar, seguimos mesmo assim.
            try:
                page.wait_for_function("document.fonts.status === 'loaded'", timeout=20000)
            except Exception:
                pass
            # Imagem que falha se remove sozinha no onerror; por isso esperamos
            # apenas "complete", que resolve tanto no sucesso quanto na falha.
            try:
                page.wait_for_function(
                    "[...document.images].every(i => i.complete)", timeout=20000
                )
            except Exception:
                pass
            page.wait_for_timeout(400)

            for index, node in enumerate(page.query_selector_all(".slide"), start=1):
                dest = out_dir / f"{index:02d}.png"
                node.screenshot(path=str(dest))
                written.append(dest)
        finally:
            browser.close()

    if not written:
        raise RenderError("nenhum slide renderizado")
    return written


def main() -> None:  # pragma: no cover - utilitario de linha de comando
    import argparse

    parser = argparse.ArgumentParser(description="Renderiza um deck.json em PNGs")
    parser.add_argument("deck", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    deck = json.loads(args.deck.read_text(encoding="utf-8"))
    for path in render_deck(deck, args.out):
        print(path)


if __name__ == "__main__":  # pragma: no cover
    main()
