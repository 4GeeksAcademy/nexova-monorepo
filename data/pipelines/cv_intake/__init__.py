"""
Frontera de ingesta de CVs (informe NIST · Protect; BRIEFING §4, caso 1).

El pipeline de scoring de candidatos todavía no existe. Este paquete es la única
puerta por la que un CV debe llegar a un modelo cuando se construya:

1. `extract`: PDF → texto **visible** con pdfplumber (ya viene con
   `markitdown[pdf]`). El texto que un humano no vería (blanco, ≤ 2 pt, fuera de
   la página) se separa y nunca llega al modelo.
2. `guard`: neutraliza las frases con forma de instrucción (los detectores del
   agente de soporte + una familia propia de manipulación del scoring), quita
   caracteres de ancho cero y envuelve el CV en `<cv_candidato>` con `<`/`>`
   escapados.
3. `prompts`: el modelo solo **extrae** datos con un esquema cerrado
   (`CvExtraction`, `extra="forbid"`): no existe campo de puntuación, ranking ni
   descarte. Puntuar y descartar son decisiones humanas (RGPD art. 22; FCRA si
   interviene un informe de antecedentes).
"""

from cv_intake.extract import CvDocument, UnreadableCv, extract_cv
from cv_intake.guard import CvGuardResult, detect_scoring_manipulation, prepare_cv_for_llm
from cv_intake.prompts import CV_EXTRACTION_SYSTEM_PROMPT, CvExtraction, CvExtractionOutcome, run_extraction

__all__ = [
    "CV_EXTRACTION_SYSTEM_PROMPT",
    "CvDocument",
    "CvExtraction",
    "CvExtractionOutcome",
    "CvGuardResult",
    "UnreadableCv",
    "detect_scoring_manipulation",
    "extract_cv",
    "prepare_cv_for_llm",
    "run_extraction",
]
