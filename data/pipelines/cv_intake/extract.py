"""
CV en PDF → texto visible + texto oculto, por separado.

Un candidato puede esconder instrucciones para el modelo que ningún revisor humano
vería (BRIEFING §4: "en texto blanco o muy pequeño"). Por eso el texto se separa
por carácter según cómo se pinta:

- color de relleno blanco (gris ≥ 0,95, RGB ≥ 0,95 o CMYK ≤ 0,05);
- tamaño de letra ≤ `HIDDEN_MAX_FONT_SIZE` (2 pt);
- fuera del área visible de la página.

Solo el texto visible se manda al modelo; el oculto se devuelve para la revisión
humana (y su mera presencia ya marca el CV para revisión).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_CV_BYTES = 10 * 1024 * 1024
HIDDEN_MAX_FONT_SIZE = 2.0
_WHITE = 0.95
MIN_TEXT_CHARS = 40


class UnreadableCv(ValueError):
    """El archivo no es un PDF válido o no tiene texto extraíble."""


@dataclass(frozen=True)
class CvDocument:
    visible_text: str
    hidden_text: str

    @property
    def has_hidden_text(self) -> bool:
        return bool(self.hidden_text.strip())


def _is_white(color: Any) -> bool:
    if color is None:
        return False
    values = color if isinstance(color, (list, tuple)) else (color,)
    try:
        numbers = [float(v) for v in values]
    except (TypeError, ValueError):
        return False
    if len(numbers) in (1, 3):  # escala de grises o RGB
        return all(v >= _WHITE for v in numbers)
    if len(numbers) == 4:  # CMYK: blanco = sin tinta
        return all(v <= 1 - _WHITE for v in numbers)
    return False


def is_hidden_char(char: dict[str, Any], page_width: float, page_height: float) -> bool:
    if char.get("object_type") != "char":
        return False
    if float(char.get("size") or 0) <= HIDDEN_MAX_FONT_SIZE:
        return True
    if _is_white(char.get("non_stroking_color")):
        return True
    x0, x1 = float(char.get("x0", 0)), float(char.get("x1", 0))
    top, bottom = float(char.get("top", 0)), float(char.get("bottom", 0))
    return x1 <= 0 or x0 >= page_width or bottom <= 0 or top >= page_height


def extract_cv(source: bytes | str | Path) -> CvDocument:
    """Separa el texto visible del oculto. Lanza `UnreadableCv` si no es un PDF con texto."""
    import pdfplumber

    data = Path(source).read_bytes() if isinstance(source, (str, Path)) else source
    if len(data) > MAX_CV_BYTES:
        raise UnreadableCv("El CV supera el tamaño máximo (10 MB).")
    if not data.startswith(b"%PDF-"):
        raise UnreadableCv("El CV no es un PDF.")

    visible: list[str] = []
    hidden: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                w, h = page.width, page.height
                shown = page.filter(lambda obj, w=w, h=h: not is_hidden_char(obj, w, h))
                concealed = page.filter(
                    lambda obj, w=w, h=h: obj.get("object_type") != "char" or is_hidden_char(obj, w, h)
                )
                visible.append(shown.extract_text() or "")
                hidden.append(concealed.extract_text() or "")
    except Exception as exc:  # pdfminer lanza tipos variados ante PDFs corruptos
        raise UnreadableCv("No se pudo leer el PDF del CV.") from exc

    document = CvDocument("\n".join(t for t in visible if t).strip(), "\n".join(t for t in hidden if t).strip())
    if len(document.visible_text) < MIN_TEXT_CHARS:
        raise UnreadableCv("El CV no contiene texto visible extraíble (¿escaneado o vacío?).")
    return document
