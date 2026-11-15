"""
Normalización para comparar texto contra los patrones de los guards.

Un atacante reformula para esquivar un filtro literal: mayúsculas, acentos,
`ign0ra`, `i g n o r a`, caracteres invisibles, markdown. Antes de comparar,
todo se lleva a una forma canónica. El texto original nunca se modifica: la
normalización solo sirve para decidir.
"""

from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_MARKDOWN_RE = re.compile(r"[*_~`#>|]+")
# "i g n o r a" / "i.g.n.o.r.a" / "i-g-n-o-r-a": 4+ letras sueltas separadas por un mismo separador.
_SPACED_RE = re.compile(r"\b(?:[a-z][ .\-_]){3,}[a-z]\b")
_WS_RE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _join_spaced(match: re.Match[str]) -> str:
    return re.sub(r"[ .\-_]", "", match.group(0))


def normalize(text: str | None) -> str:
    """Minúsculas, sin acentos ni invisibles ni markdown, letras sueltas unidas, espacios colapsados."""
    value = (text or "").translate(_ZERO_WIDTH)
    value = strip_accents(unicodedata.normalize("NFKC", value)).casefold()
    value = _MARKDOWN_RE.sub(" ", value)
    value = _SPACED_RE.sub(_join_spaced, value)
    return _WS_RE.sub(" ", value).strip()


def deleet(normalized: str) -> str:
    """Variante con sustituciones leetspeak (`ign0r4` → `ignora`), solo para patrones de inyección."""
    return normalized.translate(_LEET)


def compact(normalized: str) -> str:
    """Solo letras: `ignora-tus_instrucciones` → `ignoratusinstrucciones`."""
    return re.sub(r"[^a-z]", "", deleet(normalized))
