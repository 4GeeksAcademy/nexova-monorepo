"""
Métricas de legibilidad para anticipar el costo de procesar una RFP.

No es una nota literaria: Flesch-Kincaid y Gunning Fog (vía
`py-readability-metrics`) más el tamaño del documento en palabras y tokens
estimados se combinan en un `cost_tier` (bajo / medio / alto).

`py-readability-metrics` necesita el tokenizador `punkt_tab` de NLTK y al
menos 100 palabras; si falta cualquiera de las dos cosas se devuelven solo
las cifras de tamaño y el motivo en `note` (nunca se falla el pipeline).
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

logger = logging.getLogger("rfp_intake")

MIN_WORDS = 100
# Aproximación habitual para modelos tipo GPT: ~4 caracteres por token.
CHARS_PER_TOKEN = 4


def _ensure_punkt() -> bool:
    import nltk

    try:
        nltk.data.find("tokenizers/punkt_tab")
        return True
    except LookupError:
        try:
            return bool(nltk.download("punkt_tab", quiet=True))
        except Exception:  # sin red o sin permisos de escritura
            logger.warning("No se pudo descargar punkt_tab de NLTK", exc_info=True)
            return False


def cost_tier(estimated_tokens: int, grade_level: float | None) -> str:
    """Clasifica el costo de procesamiento por tamaño y complejidad del texto."""
    if estimated_tokens > 6000 or (grade_level is not None and grade_level >= 16):
        return "alto"
    if estimated_tokens > 1500 or (grade_level is not None and grade_level >= 12):
        return "medio"
    return "bajo"


def compute_readability(markdown: str) -> dict[str, Any]:
    words = len(re.findall(r"\w+", markdown))
    estimated_tokens = math.ceil(len(markdown) / CHARS_PER_TOKEN)
    result: dict[str, Any] = {
        "word_count": words,
        "char_count": len(markdown),
        "estimated_tokens": estimated_tokens,
        "flesch_reading_ease": None,
        "flesch_kincaid_grade": None,
        "gunning_fog": None,
        "sentence_count": None,
        "note": None,
    }

    if words < MIN_WORDS:
        result["note"] = f"Texto de {words} palabras: py-readability-metrics exige al menos {MIN_WORDS}."
    elif not _ensure_punkt():
        result["note"] = "Tokenizador punkt_tab de NLTK no disponible."
    else:
        try:
            from readability import Readability

            readability = Readability(markdown)
            # `flesch_kincaid().score` ya es un grado escolar; `flesch()` es la facilidad de lectura.
            fk = readability.flesch_kincaid()
            fog = readability.gunning_fog()
            result.update(
                flesch_reading_ease=round(float(readability.flesch().score), 2),
                flesch_kincaid_grade=round(float(fk.score), 1),
                gunning_fog=round(float(fog.score), 2),
                sentence_count=int(readability.statistics()["num_sentences"]),
            )
        except Exception as exc:  # métricas auxiliares: nunca rompen el pipeline
            result["note"] = f"No se pudieron calcular las métricas: {type(exc).__name__}"

    result["cost_tier"] = cost_tier(estimated_tokens, result["flesch_kincaid_grade"])
    return result
