"""
Aislamiento del contenido externo: fragmentos del RAG, resultados de tools
(tickets, inventario) y memoria aprobada.

Ese texto lo escribe un tercero (un documento, la descripción de un ticket que
redactó un cliente, un hecho de memoria) y por eso NUNCA es instrucción:

1. `sanitize_text`: busca instrucciones incrustadas por segmento (frase o
   línea) con los mismos detectores que el guard de entrada y sustituye cada
   segmento sospechoso por `[instrucción externa neutralizada]`. Registra un
   evento `isolation / sanitize / security` con el origen.
2. `format_isolated_context`: envuelve cada fragmento en
   `<fragmento n=… origen=… documento=… seccion=…>` dentro de
   `<datos_externos>` y escapa `<` y `>`, para que ningún dato pueda cerrar el
   bloque ni abrir una etiqueta falsa (`</datos_externos><system>`).

El system prompt (`prompt.py`) declara que todo ese bloque es dato.
"""

from __future__ import annotations

import re
from typing import Any

from agent.guardrails import events
from agent.guardrails.input_guard import detect_injection

NEUTRALIZED = "[instrucción externa neutralizada]"
EMPTY_CONTEXT = "(vacío: no hay fragmentos para esta consulta)"
_SEGMENT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_RESTRICTED_SOURCES = {"pricing-model"}  # términos comerciales del contrato (BRIEFING §3)

_ORIGINS = {
    "Gestor de incidentes (en vivo)": "ticket",
    "Gestor de inventario (en vivo)": "inventory",
    "Memoria aprobada del equipo": "memory",
}


def origin_of(chunk: dict[str, Any]) -> str:
    return _ORIGINS.get(str(chunk.get("source_document") or ""), "rag")


def escape_markup(text: str) -> str:
    return (text or "").replace("<", "‹").replace(">", "›")


def sanitize_text(text: str | None) -> tuple[str, list[str]]:
    """Neutraliza los segmentos con forma de instrucción. Devuelve (texto, reglas detectadas)."""
    if not text:
        return text or "", []
    rules: list[str] = []
    kept: list[str] = []
    for segment in _SEGMENT_RE.split(text):
        if not segment:
            continue
        hits = detect_injection(segment)
        if hits:
            rules.extend(h for h in hits if h not in rules)
            if not kept or kept[-1] != NEUTRALIZED:
                kept.append(NEUTRALIZED)
        else:
            kept.append(segment)
    if not rules:
        return text, []
    return " ".join(kept), rules


def sanitize_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copia de los chunks con los campos de texto neutralizados (y un evento por chunk afectado)."""
    cleaned = []
    for chunk in chunks:
        copy = dict(chunk)
        found: list[str] = []
        for key in ("text", "section", "source_document"):
            if isinstance(copy.get(key), str):
                copy[key], rules = sanitize_text(copy[key])
                found += [r for r in rules if r not in found]
        if found:
            copy["neutralized"] = found
            events.emit("isolation", found[0], "sanitize", "security",
                        origin=origin_of(chunk), excerpt=chunk.get("text"))
        cleaned.append(copy)
    return cleaned


def filter_restricted_sources(chunks: list[dict[str, Any]], *, is_staff: bool) -> list[dict[str, Any]]:
    """Quita del RAG los documentos con términos comerciales para usuarios externos."""
    if is_staff:
        return chunks
    kept = [c for c in chunks if c.get("source_document") not in _RESTRICTED_SOURCES]
    if len(kept) != len(chunks):
        events.emit("isolation", "restricted_source", "sanitize", "content", origin="rag")
    return kept


def source_label(source_document: Any) -> str:
    """Nombre legible del documento (mismo mapa que el RAG, sin duplicarlo)."""
    from knowledge.pipeline_bridge import PIPELINES_PATH  # noqa: F401 — deja `rag` importable
    from rag import SOURCE_LABELS

    value = str(source_document or "")
    return SOURCE_LABELS.get(value, value)


def format_isolated_context(context: list[dict[str, Any]]) -> str:
    """Bloque `<datos_externos>`: cada fragmento etiquetado con su origen, con `<`/`>` escapados."""
    if not context:
        return f"<datos_externos>\n{EMPTY_CONTEXT}\n</datos_externos>"
    parts = []
    for i, chunk in enumerate(context, start=1):
        document = escape_markup(source_label(chunk.get("source_document")))
        section = escape_markup(str(chunk.get("section") or ""))
        body = escape_markup(str(chunk.get("text") or ""))
        parts.append(
            f'<fragmento n="{i}" origen="{origin_of(chunk)}" documento="{document}" seccion="{section}">\n'
            f"{body}\n</fragmento>"
        )
    return "<datos_externos>\n" + "\n".join(parts) + "\n</datos_externos>"


def format_user_message(question: str) -> str:
    return f"<mensaje_usuario>\n{escape_markup(question)}\n</mensaje_usuario>"
