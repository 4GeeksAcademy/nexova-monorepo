"""
Consolidación y limpieza de la memoria: funciones puras, sin base de datos.

Política (ver docs/agent/memory-design.md):

1. **Deduplicación al escribir.** Se compara el hecho nuevo con los activos
   del mismo `business_line` + `category`:
   - huella idéntica, o similitud ≥ `DUPLICATE_SIMILARITY` con las mismas
     cifras → es el mismo hecho: no se inserta, se refresca el existente;
   - similitud ≥ `SUPERSEDE_SIMILARITY` → es una corrección del mismo hecho
     (p. ej. el SLA pasó de 48 h a 24 h): el nuevo reemplaza al anterior, que
     queda `superseded` con el rastro intacto.
2. **Expiración por inactividad.** Un hecho que nadie ha usado en
   `TTL_DAYS` días pasa a `expired`.
3. **Topes.** Como máximo `MAX_PER_BUCKET` activos por línea + categoría y
   `MAX_TOTAL` en total; si se superan, se expulsan (`expired`, motivo
   `evicted`) los de menor uso y actividad más antigua.
4. **Propuestas pendientes.** Caducan a las `PENDING_TTL_HOURS` horas.

La similitud es léxica (Jaccard sobre raíces de 5 letras sin palabras vacías):
suficiente para frases cortas en español y explicable, sin embeddings.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from agent.memory.schemas import MemoryFact

TTL_DAYS = 90
MAX_PER_BUCKET = 25
MAX_TOTAL = 150
PENDING_TTL_HOURS = 24
DUPLICATE_SIMILARITY = 0.85
SUPERSEDE_SIMILARITY = 0.5
_STEM = 5

_STOPWORDS = frozenset(
    """a al algo algun alguna algunas alguno algunos ante antes aqui asi aun cada como con contra
    cual cuales cuando de del desde donde dos e el ella ellas ello ellos en entre era es esa esas
    ese eso esos esta estan estas este esto estos fue fueron ha han hasta hay la las le les lo los
    mas me mi mis muy ni no nos o os otra otras otro otros para pero poco por porque que quien se
    ser si sin sobre son su sus tambien te tiene tienen todo todos tu tus un una unas uno unos y ya
    ahora siempre nunca solo debe deben suele suelen""".split()
)


def normalize(text: str) -> str:
    """Minúsculas, sin tildes ni puntuación y con espacios colapsados."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    ascii_text = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", ascii_text)).strip()


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def tokens(text: str) -> frozenset[str]:
    """Raíces (prefijo de 5 letras) de las palabras con contenido; los números se conservan."""
    return frozenset(
        word if word.isdigit() else word[:_STEM]
        for word in normalize(text).split()
        if word not in _STOPWORDS and (word.isdigit() or len(word) > 2)
    )


def _numbers(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"\d+", text))


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass(frozen=True)
class Related:
    duplicate: MemoryFact | None = None
    superseded: MemoryFact | None = None


def find_related(
    fact: str, business_line: str, category: str, active: list[MemoryFact]
) -> Related:
    """Duplicado exacto/casi exacto, o hecho anterior al que este corrige."""
    print_ = fingerprint(fact)
    best: tuple[float, MemoryFact] | None = None
    for existing in active:
        if existing.fingerprint == print_:
            return Related(duplicate=existing)
        if existing.business_line != business_line or existing.category != category:
            continue
        score = similarity(fact, existing.fact)
        if best is None or score > best[0]:
            best = (score, existing)
    if best is None:
        return Related()
    score, existing = best
    # Si cambian las cifras (SLA de 48 h → 24 h) no es un duplicado, es una corrección.
    if score >= DUPLICATE_SIMILARITY and _numbers(fact) == _numbers(existing.fact):
        return Related(duplicate=existing)
    if score >= SUPERSEDE_SIMILARITY:
        return Related(superseded=existing)
    return Related()


@dataclass(frozen=True)
class StatusChange:
    fact_id: str
    status: str  # "expired"
    reason: str  # "ttl" | "evicted"


def plan_consolidation(
    active: list[MemoryFact],
    now: datetime,
    *,
    ttl_days: int = TTL_DAYS,
    max_per_bucket: int = MAX_PER_BUCKET,
    max_total: int = MAX_TOTAL,
) -> list[StatusChange]:
    """Qué hechos activos deben dejar de leerse (TTL y topes), sin aplicarlo."""
    cutoff = now - timedelta(days=ttl_days)
    changes = [StatusChange(f.id, "expired", "ttl") for f in active if f.last_activity() < cutoff]
    gone = {c.fact_id for c in changes}
    survivors = [f for f in active if f.id not in gone]

    def weakest_first(facts: list[MemoryFact]) -> list[MemoryFact]:
        return sorted(facts, key=lambda f: (f.use_count, f.last_activity()))

    buckets: dict[tuple[str, str], list[MemoryFact]] = {}
    for fact in survivors:
        buckets.setdefault((fact.business_line, fact.category), []).append(fact)
    for facts in buckets.values():
        for fact in weakest_first(facts)[: max(0, len(facts) - max_per_bucket)]:
            changes.append(StatusChange(fact.id, "expired", "evicted"))
            gone.add(fact.id)

    survivors = [f for f in survivors if f.id not in gone]
    for fact in weakest_first(survivors)[: max(0, len(survivors) - max_total)]:
        changes.append(StatusChange(fact.id, "expired", "evicted"))
    return changes


def is_pending_stale(created_at: datetime, now: datetime, *, ttl_hours: int = PENDING_TTL_HOURS) -> bool:
    return now - created_at > timedelta(hours=ttl_hours)


def select_relevant(question: str, active: list[MemoryFact], *, k: int = 6) -> list[MemoryFact]:
    """Hechos que comparten vocabulario con la pregunta, los más afines primero."""
    question_tokens = tokens(question)
    if not question_tokens:
        return []
    scored = []
    for fact in active:
        overlap = len(question_tokens & tokens(fact.fact))
        if overlap >= 2 or (overlap == 1 and len(question_tokens) <= 3):
            scored.append((overlap, fact.use_count, fact.created_at, fact))
    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [item[3] for item in scored[:k]]
