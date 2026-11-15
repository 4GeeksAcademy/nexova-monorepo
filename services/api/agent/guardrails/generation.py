"""
Generación segura: mismo LLM de generación del RAG, pero con el system prompt
de soporte (`prompt.py`) y separación estricta de roles:

    system: SUPPORT_SYSTEM_PROMPT                     (solo instrucciones)
    user:   <datos_externos>…</datos_externos>        (dato: RAG, tools, memoria)
            <mensaje_usuario>…</mensaje_usuario>      (dato: lo que escribió la persona)

`build_messages` es pura (sin red) para que los tests comprueben que ningún
contenido externo llega al mensaje `system`.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from agent.guardrails.isolation import format_isolated_context, format_user_message
from agent.guardrails.prompt import SMALL_TALK_PROMPT, SUPPORT_SYSTEM_PROMPT

_SMALL_TALK_MAX_CHARS = 280


def build_messages(
    question: str, context: list[dict[str, Any]], *, system_prompt: str = SUPPORT_SYSTEM_PROMPT
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{format_isolated_context(context)}\n\n{format_user_message(question)}"},
    ]


def secure_generate(question: str, context: list[dict[str, Any]], *, llm: Any | None = None) -> str:
    """Respuesta de soporte (sin memoria) con el prompt seguro."""
    from knowledge.pipeline_bridge import generation_model, get_llm_client

    client = llm or get_llm_client()
    completion = client.chat.completions.create(
        model=generation_model(), temperature=0.2, messages=build_messages(question, context)
    )
    return (completion.choices[0].message.content or "").strip()


def secure_structured_generate():
    """`generate_with_memory` con el prompt de soporte, el contexto aislado y respaldo seguro."""
    from agent.memory.generation import generate_with_memory

    return partial(
        generate_with_memory,
        system_prompt=SUPPORT_SYSTEM_PROMPT,
        build_messages=build_messages,
        fallback=secure_generate,
    )


def llm_small_talk(question: str, *, llm: Any | None = None) -> str:
    """Respuesta breve a small talk (sin contexto, sin tools, sin memoria)."""
    from knowledge.pipeline_bridge import generation_model, get_llm_client

    from agent.routing import strip_reasoning

    client = llm or get_llm_client()
    completion = client.chat.completions.create(
        model=generation_model(),
        temperature=0.3,
        timeout=15,
        messages=[
            {"role": "system", "content": SMALL_TALK_PROMPT},
            {"role": "user", "content": format_user_message(question)},
        ],
    )
    text = strip_reasoning(completion.choices[0].message.content or "")
    return text[:_SMALL_TALK_MAX_CHARS].strip()
