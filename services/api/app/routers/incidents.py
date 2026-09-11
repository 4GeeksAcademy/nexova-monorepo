"""
Router: /api/incidents

- POST /api/incidents/analyze          -> analiza un CSV subido, devuelve JSON
- GET  /api/incidents/results/export   -> devuelve el último análisis en CSV
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

import incidents_analyzer as ia

from app.schemas import IncidentAnalysisSummary
from app.state import get_last_summary, set_last_summary
from models import User
from security import get_current_user

router = APIRouter(prefix="/api/incidents", tags=["incidents"])

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB, margen para producción


@router.post("/analyze", response_model=IncidentAnalysisSummary)
async def analyze_incidents(
    file: UploadFile = File(...), current_user: User = Depends(get_current_user)
) -> IncidentAnalysisSummary:
    """
    Recibe un CSV como multipart/form-data, ejecuta la misma lógica de
    validación y análisis que el script de la Fase 1, y devuelve el
    resumen en JSON. Nunca incluye datos personales (emails) en la
    respuesta.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No se recibió ningún fichero.")

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail=f"Formato no soportado: '{file.filename}'. Se espera un fichero .csv.",
        )

    raw_bytes = await file.read()

    if not raw_bytes:
        raise HTTPException(status_code=400, detail="El fichero está vacío.")

    if len(raw_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="El fichero supera el tamaño máximo permitido (50 MB).",
        )

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="No se pudo leer el fichero: la codificación debe ser UTF-8.",
        )

    try:
        records = ia.parse_csv_text(text)
    except (ia.EmptyFileError, ia.MissingHeaderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="El fichero no tiene un formato CSV válido.",
        )

    result = ia.analyze(records, source_name=file.filename)
    summary = ia.build_summary(result)

    # Se guarda para que GET /api/incidents/results/export pueda descargarlo.
    set_last_summary(summary)

    return IncidentAnalysisSummary.model_validate(summary)


@router.get("/results/export")
async def export_results(current_user: User = Depends(get_current_user)) -> Response:
    """
    Devuelve el último análisis ejecutado como un CSV descargable
    (una fila por métrica), igual que el script de la Fase 1.
    """
    summary = get_last_summary()

    if summary is None:
        raise HTTPException(
            status_code=404,
            detail="No hay ningún análisis previo. Ejecuta primero POST /api/incidents/analyze.",
        )

    csv_text = ia.summary_to_csv_text(summary)

    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="results.csv"'},
    )
