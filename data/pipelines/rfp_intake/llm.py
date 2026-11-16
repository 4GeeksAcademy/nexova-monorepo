"""
Llamadas JSON al modelo de generación (endpoint de 4Geeks, compatible con OpenAI).

Los nodos no conocen el SDK: reciben un `LlmFn` = `(task, system, user) -> dict`.
En producción es `OpenAIJsonLLM`; en los tests, un fake que responde según
`task` ("classify", "metadata", "orchestrate", "worker:<department_id>",
"synthesize").

Mismas variables que el RAG (`LLM_BASE_URL`, `LLM_API_KEY`,
`GENERATION_MODEL`), pero sin importar `data/process/rag_indexing.py`, para
que el worker no dependa de Qdrant. Patrón de JSON estricto tomado de
`services/api/agent/memory/generation.py::create_json_completion`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Protocol

logger = logging.getLogger("rfp_intake")

FORMAT_RETRIES = 2
LLM_TIMEOUT_SECONDS = 60


class LlmFn(Protocol):
    def __call__(self, task: str, system: str, user: str) -> dict[str, Any]: ...


class LlmConfigError(RuntimeError):
    """Falta configuración del modelo."""


class LlmOutputError(ValueError):
    """El modelo no devolvió un JSON legible."""


def strip_reasoning(text: str) -> str:
    """Quita los bloques `<think>…</think>` que algunos modelos anteponen."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def extract_json(text: str) -> dict[str, Any]:
    text = strip_reasoning(text)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise LlmOutputError("La respuesta del modelo no contiene un objeto JSON.")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LlmOutputError(f"JSON inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise LlmOutputError("El JSON del modelo no es un objeto.")
    if not data:
        # Con `json_object`, el modelo de 4Geeks a veces devuelve `{}`: no es una respuesta.
        raise LlmOutputError("El modelo devolvió un objeto JSON vacío.")
    return data


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise LlmConfigError(f"Falta la variable de entorno {name}.")
    return value


class OpenAIJsonLLM:
    """Cliente real: `response_format=json_object` y un reintento si el JSON no se lee."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=_require_env("LLM_BASE_URL"), api_key=_require_env("LLM_API_KEY"))
        self.client = client
        self.model = model or _require_env("GENERATION_MODEL")

    def _complete(self, messages: list[dict[str, str]], json_mode: bool = True) -> str:
        from openai import BadRequestError

        kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "timeout": LLM_TIMEOUT_SECONDS,
            "messages": messages,
        }
        if not json_mode:
            completion = self.client.chat.completions.create(**kwargs)
            return completion.choices[0].message.content or ""
        try:
            completion = self.client.chat.completions.create(
                response_format={"type": "json_object"}, **kwargs
            )
        except BadRequestError:
            logger.info("El proveedor no admite response_format=json_object; se pide sin él")
            completion = self.client.chat.completions.create(**kwargs)
        return completion.choices[0].message.content or ""

    def __call__(self, task: str, system: str, user: str) -> dict[str, Any]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error: Exception | None = None
        for attempt in range(FORMAT_RETRIES + 1):
            # El último intento va sin `json_object`: ese modo es el que a veces da `{}`.
            raw = self._complete(messages, json_mode=attempt < FORMAT_RETRIES)
            try:
                return extract_json(raw)
            except LlmOutputError as exc:
                last_error = exc
                logger.warning("task=%s intento=%d JSON ilegible: %s", task, attempt + 1, exc)
                messages = [
                    *messages[:2],
                    {"role": "assistant", "content": raw[:2000]},
                    {"role": "user", "content": "Responde SOLO con el objeto JSON pedido, sin texto adicional."},
                ]
        raise LlmOutputError(f"{task}: {last_error}")
