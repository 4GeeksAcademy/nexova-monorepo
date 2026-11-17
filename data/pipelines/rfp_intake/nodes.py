"""
Nodos del grafo `rfp_intake`: cada agente es un nodo separado.

    convert_to_markdown → classify ─(no RFP)→ discard
                                   └(RFP)→ extract_metadata → orchestrate ─Send×N→ department_worker
                                                                            → synthesize → finalize

Los agentes LLM proponen; el código decide lo que tiene consecuencias:
- el umbral de descarte del clasificador,
- la sede → moneda (España → EUR, Miami/EE. UU. → USD),
- qué departamentos existen (catálogo cerrado; lo demás va a `unmapped_services`),
- qué cifras se aceptan (solo las que tienen cita literal en el documento),
- los conflictos entre workers (se señalan, no se resuelven).

Cada nodo persiste su resultado en el store de forma idempotente, para que el
ticket refleje en todo momento hasta dónde llegó el análisis.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any

from langgraph.types import Send

from rfp_intake.convert import UnreadablePdf, pdf_to_markdown
from rfp_intake.departments import (
    CURRENCY_BY_HQ,
    DEPARTMENT_IDS,
    DEPARTMENTS,
    HQ_MIAMI,
    HQ_SPAIN,
    HQ_UNKNOWN,
    department_card,
)
from rfp_intake.events import TicketCreatedPublisher, build_ticket_created_payload
from rfp_intake.legibility import compute_readability
from rfp_intake.llm import LlmFn
from rfp_intake.prompts import (
    CLASSIFIER_SYSTEM,
    METADATA_SYSTEM,
    ORCHESTRATOR_SYSTEM,
    SYNTHESIZER_SYSTEM,
    worker_system,
    wrap_document,
)
from rfp_intake.schemas import (
    Classification,
    ExtractedMetadata,
    Figure,
    OrchestratorPlan,
    SynthesisOutput,
    WorkerOutput,
)
from rfp_intake.state import IntakeState
from rfp_intake.store import RfpStore

logger = logging.getLogger("rfp_intake")

# Rechazar solo con alta confianza: un falso negativo (RFP real descartada) pierde una venta;
# un falso positivo solo cuesta un análisis que Ventas descarta a mano.
DISCARD_CONFIDENCE_THRESHOLD = 0.7
MAX_DOCUMENT_CHARS = 24_000
MAX_EXCERPTS_PER_DEPARTMENT = 8
HANDOFF_CONTRACT_VERSION = "rfp_intake.v1"

_SPAIN_HINTS = (
    "españa", "espana", "spain", "madrid", "barcelona", "valencia", "sevilla", "bilbao",
    "malaga", "málaga", "zaragoza",
)
_MIAMI_HINTS = (
    "miami", "florida", "fl", "usa", "u.s", "ee. uu", "ee.uu", "estados unidos", "united states",
)


# --------------------------------------------------------------------------- #
# Utilidades deterministas
# --------------------------------------------------------------------------- #
def _plain(text: str) -> str:
    """Minúsculas, sin acentos, sin viñetas y con espacios colapsados (para comparar citas)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[•\-\*·]+\s", " ", text)
    text = re.sub(r"[\"'“”‘’`]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_document(quote: str, document: str) -> bool:
    quote_plain = _plain(quote)
    return len(quote_plain) >= 3 and quote_plain in _plain(document)


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", (text or "").replace(".", "").replace(",", "")))


def value_supported_by_quote(value: str, quote: str) -> bool:
    """Las cifras del valor deben salir de la cita ("4" no se sostiene con "5 posiciones")."""
    numbers = _numbers(value)
    return not numbers or numbers <= _numbers(quote)


def split_verified_figures(figures: list[Figure], document: str) -> tuple[list[dict], list[Figure]]:
    """Separa las cifras con cita literal (y valor respaldado por ella) de las que no la tienen."""
    verified, rejected = [], []
    for figure in figures:
        if (
            figure.value
            and quote_in_document(figure.quote, document)
            and value_supported_by_quote(figure.value, figure.quote)
        ):
            verified.append(figure.model_dump())
        else:
            rejected.append(figure)
    return verified, rejected


def resolve_client_hq(*candidates: str | None) -> str:
    """Sede del cliente → "España" | "Miami" | "desconocido" (define la moneda, BRIEFING §5)."""
    for candidate in candidates:
        if not candidate:
            continue
        text = candidate.lower()
        if _has_hint(text, _MIAMI_HINTS):
            return HQ_MIAMI
        if _has_hint(text, _SPAIN_HINTS):
            return HQ_SPAIN
    return HQ_UNKNOWN


def _has_hint(text: str, hints: tuple[str, ...]) -> bool:
    # Límites de palabra: "usa" no debe coincidir dentro de "causa".
    return any(re.search(rf"(?<![a-z]){re.escape(hint)}(?![a-z])", text) for hint in hints)


def currency_for_hq(client_hq: str) -> str | None:
    return CURRENCY_BY_HQ.get(client_hq)


def parse_deadline(value: str | None, document: str | None = None) -> date | None:
    """Fecha ISO del extractor. Si el documento no trae el año, no se acepta (el modelo lo inventa)."""
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
    if document is not None and str(parsed.year) not in document:
        return None
    return parsed


def keyword_excerpts(document: str, department_id: str, limit: int = 4) -> list[str]:
    """Párrafos con palabras clave del departamento (respaldo si el orquestador no cita bien)."""
    keywords = DEPARTMENTS[department_id].keywords
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", document) if p.strip()]
    hits = [p for p in paragraphs if any(k in p.lower() for k in keywords)]
    return hits[:limit]


def _concept_key(concept: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", _plain(concept)).strip()


def _value_key(value: str) -> str:
    digits = re.findall(r"\d+(?:[.,]\d+)?", value or "")
    return " ".join(digits) if digits else _plain(value)


def detect_conflicts(results: list[dict[str, Any]], metadata_volumes: list[dict[str, Any]]) -> list[dict]:
    """Misma cifra con valores distintos entre workers (o frente a los metadatos)."""
    by_concept: dict[str, list[dict[str, Any]]] = {}
    for volume in metadata_volumes:
        by_concept.setdefault(_concept_key(volume["concept"]), []).append(
            {"source": "metadata", "value": volume["value"], "quote": volume["quote"]}
        )
    for result in results:
        for figure in result.get("figures_cited") or []:
            by_concept.setdefault(_concept_key(figure["concept"]), []).append(
                {"source": result["department_id"], "value": figure["value"], "quote": figure["quote"]}
            )

    conflicts = []
    for concept, entries in by_concept.items():
        if not concept or len({e["source"] for e in entries}) < 2:
            continue
        if len({_value_key(e["value"]) for e in entries}) > 1:
            conflicts.append({"concept": concept, "values": entries, "resolution": "pendiente_ventas"})
    return conflicts


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc(markdown: str) -> str:
    return wrap_document(markdown[:MAX_DOCUMENT_CHARS])


# --------------------------------------------------------------------------- #
# Nodos
# --------------------------------------------------------------------------- #
class IntakeNodes:
    def __init__(
        self, llm: LlmFn, store: RfpStore, publisher: TicketCreatedPublisher | None = None
    ) -> None:
        self.llm = llm
        self.store = store
        # Notificación en tiempo real (SSE); sin publicador no se emite nada.
        self.publisher = publisher

    # -- Ingesta --------------------------------------------------------------
    def convert_to_markdown(self, state: IntakeState) -> dict[str, Any]:
        if state.get("markdown"):
            markdown = state["markdown"]
        else:
            try:
                markdown = pdf_to_markdown(state["pdf_path"])
            except UnreadablePdf as exc:
                classification = {
                    "is_rfp": False, "rfp_type": "no_rfp", "confidence": 1.0,
                    "document_kind": "PDF sin texto", "reason": str(exc),
                    "decision": "descartado", "decided_by": "conversion",
                }
                return {"markdown": "", "classification": classification, "discarded": True}
        self.store.save_markdown(state["ticket_id"], markdown)
        return {"markdown": markdown}

    # -- Agente clasificador --------------------------------------------------
    def classify(self, state: IntakeState) -> dict[str, Any]:
        if state.get("discarded"):
            return {}
        raw = self.llm("classify", CLASSIFIER_SYSTEM, _doc(state["markdown"]))
        result = Classification.model_validate(raw)
        discard = (not result.is_rfp) and result.confidence >= DISCARD_CONFIDENCE_THRESHOLD
        classification = result.model_dump()
        if not result.is_rfp and not discard:
            # Duda: se procesa igual y Ventas lo ve marcado.
            classification["rfp_type"] = "informal"
        classification.update(
            decision="descartado" if discard else "aceptado",
            low_confidence=result.confidence < DISCARD_CONFIDENCE_THRESHOLD,
            decided_by="classifier",
            threshold=DISCARD_CONFIDENCE_THRESHOLD,
        )
        if not discard:
            self.store.save_classification(state["ticket_id"], classification)
        return {"classification": classification, "discarded": discard}

    # -- Agente extractor de metadatos ----------------------------------------
    def extract_metadata(self, state: IntakeState) -> dict[str, Any]:
        markdown = state["markdown"]
        raw = self.llm("metadata", METADATA_SYSTEM, _doc(markdown))
        extracted = ExtractedMetadata.model_validate(raw)

        volumes, rejected = split_verified_figures(extracted.volumes, markdown)
        if rejected:
            logger.info("ticket=%s cifras sin cita literal descartadas: %d", state["ticket_id"], len(rejected))

        # La sede se decide por código: primero lo que dice el extractor, luego la cabecera.
        client_hq = resolve_client_hq(extracted.client_location, markdown[:800])
        deadline = parse_deadline(extracted.deadline, markdown)
        deadline_text = extracted.deadline_text or extracted.deadline
        if extracted.deadline and deadline is None:
            deadline_text = f"{deadline_text} (fecha sin año explícito en la RFP: confirmar)"
        metadata = {
            "client_name": extracted.client_name,
            "client_hq": client_hq,
            "client_location": extracted.client_location,
            "currency": currency_for_hq(client_hq),
            "rfp_type": (state.get("classification") or {}).get("rfp_type"),
            "services_requested": extracted.services_requested,
            "scope": extracted.scope,
            "volumes": volumes,
            "deadline": deadline,
            "deadline_text": deadline_text,
            "budget_range": extracted.budget_range,
            "client_contact": extracted.client_contact,
            "departments_needed": [],
            "unmapped_services": [],
            "readability": compute_readability(markdown),
        }
        self.store.save_metadata(state["ticket_id"], metadata, extracted.rfp_reference)
        self._notify_ticket_created(state["ticket_id"], metadata, extracted.rfp_reference)
        return {"metadata": metadata, "rfp_id": extracted.rfp_reference}

    def _notify_ticket_created(
        self, ticket_id: str, metadata: dict[str, Any], rfp_id: str | None
    ) -> None:
        """RFP válida con metadatos guardados: avisa al dashboard (nunca interrumpe el análisis)."""
        if self.publisher is None:
            return
        try:
            payload = build_ticket_created_payload(
                self.store.get_ticket(ticket_id), metadata, rfp_id, ticket_id
            )
            self.publisher(payload)
        except Exception:  # noqa: BLE001 - la notificación es accesoria
            logger.warning("ticket=%s no se pudo notificar rfp_ticket_created", ticket_id, exc_info=True)

    # -- Agente orquestador ---------------------------------------------------
    def orchestrate(self, state: IntakeState) -> dict[str, Any]:
        markdown = state["markdown"]
        metadata = state["metadata"]
        user = (
            "Metadatos ya extraídos:\n"
            + json.dumps(_shared_metadata(metadata), ensure_ascii=False, default=str)
            + "\n\n"
            + _doc(markdown)
        )
        plan = OrchestratorPlan.model_validate(self.llm("orchestrate", ORCHESTRATOR_SYSTEM, user))

        unmapped = list(dict.fromkeys(plan.unmapped_services))
        assignments: dict[str, dict[str, Any]] = {}
        for item in plan.departments:
            if item.department_id not in DEPARTMENTS:
                # Departamento que no existe en Nexova: se registra, nunca se inventa.
                label = ", ".join(item.services) or item.reason or item.department_id
                unmapped.append(f"{label} (departamento propuesto: {item.department_id})")
                continue
            current = assignments.setdefault(
                item.department_id, {"department_id": item.department_id, "reason": item.reason,
                                     "services": [], "excerpts": []}
            )
            current["services"] += [s for s in item.services if s not in current["services"]]
            verified = [e for e in item.excerpts if quote_in_document(e, markdown)]
            current["excerpts"] += [e for e in verified if e not in current["excerpts"]]

        ordered = [assignments[d] for d in DEPARTMENT_IDS if d in assignments]
        for assignment in ordered:
            if not assignment["excerpts"]:
                assignment["excerpts"] = keyword_excerpts(markdown, assignment["department_id"])
            assignment["excerpts"] = assignment["excerpts"][:MAX_EXCERPTS_PER_DEPARTMENT]

        metadata = {
            **metadata,
            "departments_needed": [a["department_id"] for a in ordered],
            "unmapped_services": list(dict.fromkeys(unmapped)),
        }
        self.store.save_metadata(state["ticket_id"], metadata, state.get("rfp_id"))
        self.store.reset_sections(state["ticket_id"])

        update: dict[str, Any] = {"metadata": metadata, "assignments": ordered,
                                  "unmapped_services": metadata["unmapped_services"]}
        if not ordered:
            classification = {
                **(state.get("classification") or {}),
                "decision": "descartado",
                "decided_by": "orchestrator",
                "reason": "La solicitud no pide ningún servicio que cubran los departamentos de "
                "Nexova (seleccion, capacitacion, soporte).",
            }
            update.update(classification=classification, discarded=True)
        return update

    # -- Workers (uno por departamento, en paralelo) --------------------------
    def department_worker(self, payload: dict[str, Any]) -> dict[str, Any]:
        department_id = payload["department_id"]
        excerpts: list[str] = payload["excerpts"]
        user = (
            "Metadatos compartidos de la RFP:\n"
            + json.dumps(payload["shared_metadata"], ensure_ascii=False, default=str)
            + f"\n\nMotivo de activación: {payload.get('reason') or 's/d'}\n\n"
            + wrap_document("\n\n".join(excerpts) or "(sin extractos)")
        )
        output = WorkerOutput.model_validate(
            self.llm(f"worker:{department_id}", worker_system(department_id), user)
        )

        evidence = "\n\n".join(excerpts) + "\n\n" + json.dumps(payload["shared_metadata"], ensure_ascii=False)
        figures, rejected = split_verified_figures(output.figures_cited, evidence)
        open_questions = list(output.open_questions)
        for figure in rejected:
            open_questions.append(
                f"Confirmar con el cliente: {figure.concept} ({figure.value}) no aparece literalmente en la RFP."
            )

        section = {
            "department_id": department_id,
            "owner_name": DEPARTMENTS[department_id].owner,
            "key_aspects": output.key_aspects,
            "requests_to_owner": output.requests_to_owner,
            "open_questions": list(dict.fromkeys(open_questions)),
            "figures_cited": figures,
            "excerpts": excerpts,
        }
        self.store.upsert_section(payload["ticket_id"], section)
        return {"department_results": [section]}

    # -- Synthesizer ----------------------------------------------------------
    def synthesize(self, state: IntakeState) -> dict[str, Any]:
        metadata = state["metadata"]
        order = {d: i for i, d in enumerate(DEPARTMENT_IDS)}
        results = sorted(state.get("department_results") or [], key=lambda r: order[r["department_id"]])
        conflicts = detect_conflicts(results, metadata.get("volumes") or [])

        who_to_ask = [
            {
                **department_card(r["department_id"]),
                "requests": r["requests_to_owner"],
                "open_questions": r["open_questions"],
            }
            for r in results
        ]
        payload = {
            "metadata": _shared_metadata(metadata),
            "departments": [
                {"department_id": r["department_id"], "owner": r["owner_name"],
                 "key_aspects": r["key_aspects"], "requests_to_owner": r["requests_to_owner"],
                 "open_questions": r["open_questions"]}
                for r in results
            ],
            "conflicts": conflicts,
            "unmapped_services": metadata.get("unmapped_services") or [],
        }
        try:
            output = SynthesisOutput.model_validate(
                self.llm("synthesize", SYNTHESIZER_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str))
            )
            if not output.summary_for_sales:
                raise ValueError("resumen vacío")
            summary, next_steps, source = output.summary_for_sales, output.next_steps, "llm"
        except Exception:
            # El resumen es redacción: si el modelo falla, se arma con los datos ya validados.
            logger.warning("ticket=%s synthesizer sin LLM, resumen determinista", state["ticket_id"], exc_info=True)
            summary, next_steps, source = _fallback_summary(metadata, who_to_ask), [], "fallback"

        if not next_steps:
            next_steps = [
                f"Pedir a {w['owner']} ({w['name']}): " + "; ".join(w["requests"][:3])
                for w in who_to_ask if w["requests"]
            ]

        synthesis = {
            "summary_for_sales": summary,
            "next_steps": next_steps,
            "who_to_ask": who_to_ask,
            "conflicts": conflicts,
            "unmapped_services": metadata.get("unmapped_services") or [],
            "currency": metadata.get("currency"),
            "source": source,
            "generated_at": _now_iso(),
        }
        handoff = {
            "contract_version": HANDOFF_CONTRACT_VERSION,
            "ticket_id": state["ticket_id"],
            "rfp_id": state.get("rfp_id"),
            "client_name": metadata.get("client_name"),
            "client_hq": metadata.get("client_hq"),
            "currency": metadata.get("currency"),
            "deadline": _iso_or_none(metadata.get("deadline")),
            "services_requested": metadata.get("services_requested") or [],
            "workstreams": [
                {
                    "department_id": r["department_id"],
                    "owner": r["owner_name"],
                    "key_aspects": r["key_aspects"],
                    "requests_to_owner": r["requests_to_owner"],
                    "open_questions": r["open_questions"],
                    "figures_cited": r["figures_cited"],
                    "excerpts": r["excerpts"],
                }
                for r in results
            ],
            "conflicts": conflicts,
            "unmapped_services": metadata.get("unmapped_services") or [],
            "cost_tier": (metadata.get("readability") or {}).get("cost_tier"),
        }
        return {"synthesis": synthesis, "handoff": handoff}

    # -- Cierre ---------------------------------------------------------------
    def finalize(self, state: IntakeState) -> dict[str, Any]:
        self.store.complete(state["ticket_id"], state["synthesis"], state["handoff"])
        return {"status": "analisis_completo"}

    def discard(self, state: IntakeState) -> dict[str, Any]:
        self.store.discard(state["ticket_id"], state["classification"])
        return {"status": "descartado"}


# --------------------------------------------------------------------------- #
# Aristas
# --------------------------------------------------------------------------- #
def after_classify(state: IntakeState) -> str:
    return "discard" if state.get("discarded") else "extract_metadata"


def fan_out(state: IntakeState) -> list[Send] | str:
    """Una subtarea por departamento activo; cada worker recibe solo lo suyo."""
    if state.get("discarded") or not state.get("assignments"):
        return "discard"
    shared = _shared_metadata(state["metadata"])
    return [
        Send(
            "department_worker",
            {
                "ticket_id": state["ticket_id"],
                "department_id": a["department_id"],
                "reason": a["reason"],
                "excerpts": a["excerpts"],
                "shared_metadata": shared,
            },
        )
        for a in state["assignments"]
    ]


# --------------------------------------------------------------------------- #
def _iso_or_none(value: Any) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value or None


def _shared_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Lo que comparten todos los workers (sin el documento completo)."""
    return {
        "client_name": metadata.get("client_name"),
        "client_hq": metadata.get("client_hq"),
        "currency": metadata.get("currency"),
        "rfp_type": metadata.get("rfp_type"),
        "services_requested": metadata.get("services_requested") or [],
        "scope": metadata.get("scope"),
        "volumes": metadata.get("volumes") or [],
        "deadline": _iso_or_none(metadata.get("deadline")),
        "deadline_text": metadata.get("deadline_text"),
        "budget_range": metadata.get("budget_range"),
    }


def _fallback_summary(metadata: dict[str, Any], who_to_ask: list[dict[str, Any]]) -> str:
    client = metadata.get("client_name") or "Cliente sin identificar"
    hq = metadata.get("client_hq") or HQ_UNKNOWN
    currency = metadata.get("currency") or "moneda por confirmar"
    owners = ", ".join(f"{w['owner']} ({w['department_id']})" for w in who_to_ask) or "ningún departamento"
    services = "; ".join(metadata.get("services_requested") or []) or "servicios por confirmar"
    return (
        f"{client} (sede: {hq}) solicita: {services}. Propuesta en {currency}. "
        f"Departamentos a involucrar: {owners}."
    )
