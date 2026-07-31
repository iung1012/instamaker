"""Geracao de carrossel no estilo blueprint (9 slides, 1080x1350)."""

from .build import attach_images, build_deck
from .frames import extract as extract_frames
from .render import RenderError, render_deck

__all__ = ["build_deck", "attach_images", "extract_frames", "render_deck", "RenderError"]
