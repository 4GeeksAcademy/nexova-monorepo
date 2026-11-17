"""
Clasificación explícita de la decisión del usuario frente a la propuesta pendiente.

Cuando hay una propuesta de memoria pendiente, el siguiente mensaje se evalúa
PRIMERO contra ella con una etiqueta estructurada (nunca con `"sí" in mensaje`):

- `approve`: acepta guardar el hecho tal cual.
- `reject`: no quiere que se guarde.
- `edit`: quiere guardarlo con cambios (`edited_fact` = versión corregida).
- `unrelated`: no responde a la propuesta (cambia de tema).
- `ambiguous`: parece referirse a ella, pero la decisión no es clara.

El LLM de generación devuelve un JSON validado con Pydantic (`PendingDecision`).
`effective_label()` aplica la regla de seguridad: solo `approve`/`edit` con
confianza ≥ `MIN_CONFIDENCE` pueden escribir en memoria; cualquier otra cosa
(incluido un fallo del modelo) descarta la propuesta. El silencio o la
ambigüedad nunca se interpretan como aprobación.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from pydantic import ValidationError

from agent.memory.schemas import MemoryProposal, PendingDecision

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.7

DECISION_PROMPT = """\
Eres un clasificador de decisiones. El asistente de soporte de Nexova propuso al usuario \
guardar un hecho en la memoria compartida del equipo y le preguntó si quería que lo recordara. \
Tu única tarea es clasificar el NUEVO mensaje del usuario respecto a esa propuesta. \
No respondas al usuario.

Etiquetas (campo "label"):
- "approve": acepta que se guarde tal cual (p. ej. "sí", "vale, guárdalo", "correcto, recuérdalo").
- "reject": no quiere que se guarde (p. ej. "no", "mejor no", "no hace falta", "olvídalo").
- "edit": quiere que se guarde pero cambiando algo. Pon en "edited_fact" el hecho COMPLETO \
corregido, redactado como regla general en una frase.
- "unrelated": el mensaje no responde a la propuesta (cambia de tema o hace otra pregunta \
sin decir nada sobre guardar).
- "ambiguous": parece referirse a la propuesta pero no queda clara la decisión \
(p. ej. "quizás", "no sé", "luego lo vemos", "puede ser").

"remaining_question": si el mensaje, además de decidir, plantea otra pregunta o petición, \
cópiala literalmente; si no, null. Con "unrelated", copia el mensaje completo.
"confidence": número entre 0 y 1 con tu seguridad en la etiqueta.

Responde SOLO un objeto JSON, sin texto adicional:
{"label": "approve"|"reject"|"edit"|"unrelated"|"ambiguous", "edited_fact": string|null, \
"remaining_question": string|null, "confidence": number}
"""

DecisionClassifier = Callable[[MemoryProposal, str], PendingDecision]


def classify_decision_with_llm(proposal: MemoryProposal, message: str) -> PendingDecision:
    from agent.memory.generation import FORMAT_RETRIES, create_json_completion
    from agent.routing import _extract_json, _rag_llm

    client, model = _rag_llm()
    messages = [
        {"role": "system", "content": DECISION_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {"propuesta_pendiente": proposal.fact, "mensaje_del_usuario": message},
                ensure_ascii=False,
            ),
        },
    ]
    # Un reintento solo si el FORMATO falla (p. ej. el modelo devuelve `{}`); si
    # vuelve a fallar, la excepción llega a `classify_decision` y se descarta.
    for attempt in range(1 + FORMAT_RETRIES):
        completion = create_json_completion(
            client, model=model, temperature=0, timeout=15, messages=messages
        )
        try:
            data = _extract_json(completion.choices[0].message.content or "")
            return PendingDecision.model_validate({**data, "method": "llm"})
        except (ValidationError, ValueError) as exc:
            if attempt == FORMAT_RETRIES:
                raise
            logger.warning("Clasificador con formato inválido (%s); se repite una vez", type(exc).__name__)
    raise AssertionError("inalcanzable")


def _fallback(message: str) -> PendingDecision:
    """Sin clasificador fiable no se decide nada: se descarta la propuesta.

    Si el mensaje trae una pregunta explícita, se atiende como tal para no perderla.
    """
    looks_like_question = "?" in message
    return PendingDecision(
        label="ambiguous",
        remaining_question=message if looks_like_question else None,
        confidence=0.0,
        method="fallback",
    )


def classify_decision(
    proposal: MemoryProposal,
    message: str,
    *,
    llm_classifier: DecisionClassifier = classify_decision_with_llm,
) -> PendingDecision:
    try:
        decision = llm_classifier(proposal, message)
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Clasificador de decisión inválido (%s); se descarta la propuesta", type(exc).__name__)
        return _fallback(message)
    except Exception as exc:  # LLM caído, timeout…
        logger.warning("Clasificador de decisión no disponible (%s); se descarta la propuesta", type(exc).__name__)
        return _fallback(message)

    if decision.label == "unrelated" and not decision.remaining_question:
        decision = decision.model_copy(update={"remaining_question": message})
    if decision.remaining_question is not None and not decision.remaining_question.strip():
        decision = decision.model_copy(update={"remaining_question": None})
    return decision


def effective_label(decision: PendingDecision) -> str:
    """Etiqueta que se aplica: la aprobación exige confianza; en la duda, descarte."""
    if decision.label in ("approve", "edit") and decision.confidence < MIN_CONFIDENCE:
        return "ambiguous"
    if decision.label == "edit" and not (decision.edited_fact or "").strip():
        return "ambiguous"
    return decision.label
