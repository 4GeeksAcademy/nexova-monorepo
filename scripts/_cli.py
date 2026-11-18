"""Salida de error común de los scripts: mensaje legible en stderr y código ≠ 0."""

from __future__ import annotations

import sys
from typing import NoReturn


def fail(message: str, code: int = 1) -> NoReturn:
    """Imprime `Error: …` en stderr y termina con `code` (nunca 0)."""
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


def reason(exc: BaseException) -> str:
    """Causa para el usuario: el tipo de la excepción, sin su texto (puede incluir URLs o credenciales)."""
    return type(exc).__name__
