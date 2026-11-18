"""
Router del analizador de incidencias (CSV): /api/incidents

- POST /api/incidents/analyze          -> analiza un CSV subido y devuelve JSON
- GET  /api/incidents/results/export   -> devuelve el último análisis en CSV

Solo personal interno. Es una herramienta de análisis: no escribe en la base de
datos. El gestor de incidencias (alta, listado, detalle, estado y resumen) vive
en `incident_manager/`, en el mismo prefijo, sin solapamiento de rutas.
"""

from __future__ import annotations

import csv

import incidents_analyzer as ia
from authz import require_staff
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from models import User

from app.schemas import IncidentAnalysisSummary
from app.state import get_last_summary, set_last_summary

router = APIRouter(prefix="/api/incidents", tags=["incidents"])

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB, margen para producción


@router.post("/analyze", response_model=IncidentAnalysisSummary)
async def analyze_incidents(
    file: UploadFile = File(...), current_user: User = Depends(require_staff)
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

    # Lee como mucho el límite + 1 byte: un fichero enorme no llega entero a memoria.
    raw_bytes = await file.read(MAX_FILE_SIZE_BYTES + 1)

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
    except (csv.Error, ValueError):  # CSV mal formado (comillas sin cerrar, nulos…)
        raise HTTPException(
            status_code=400,
            detail="El fichero no tiene un formato CSV válido.",
        )

    result = ia.analyze(records, source_name=file.filename)
    summary = ia.build_summary(result)

    # Se guarda para que GET /api/incidents/results/export pueda descargarlo.
    set_last_summary(summary, owner=current_user.id)

    return IncidentAnalysisSummary.model_validate(summary)


@router.get("/results/export")
async def export_results(current_user: User = Depends(require_staff)) -> Response:
    """
    Devuelve el último análisis ejecutado como un CSV descargable
    (una fila por métrica), igual que el script de la Fase 1.
    """
    summary = get_last_summary(owner=current_user.id)

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
