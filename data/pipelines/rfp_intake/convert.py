"""
PDF → Markdown con MarkItDown, antes de que ningún agente lea el documento.

Las RFP pesan mucho en tokens como PDF; el Markdown es el único formato que
reciben los agentes. Se limpian artefactos de extracción (`(cid:127)` en las
viñetas, espacios repetidos) que solo añaden ruido y tokens.
"""

from __future__ import annotations

import re
from pathlib import Path

_CID_BULLET = re.compile(r"\(cid:\d+\)\s*")
_TRAILING_SPACES = re.compile(r"[ \t]+\n")
_EXTRA_BLANK_LINES = re.compile(r"\n{3,}")

# Por debajo de esto el PDF es una imagen escaneada o está vacío.
MIN_TEXT_CHARS = 40


class UnreadablePdf(ValueError):
    """El PDF no tiene texto extraíble."""


def clean_markdown(text: str) -> str:
    text = _CID_BULLET.sub("- ", text or "")
    text = _TRAILING_SPACES.sub("\n", text)
    text = _EXTRA_BLANK_LINES.sub("\n\n", text)
    return text.strip()


def pdf_to_markdown(path: str | Path) -> str:
    """Convierte el PDF a Markdown limpio. Lanza `UnreadablePdf` si no hay texto."""
    from markitdown import MarkItDown

    result = MarkItDown(enable_plugins=False).convert(str(path))
    markdown = clean_markdown(result.text_content)
    if len(markdown) < MIN_TEXT_CHARS:
        raise UnreadablePdf("El PDF no contiene texto extraíble (¿escaneado o vacío?).")
    return markdown
