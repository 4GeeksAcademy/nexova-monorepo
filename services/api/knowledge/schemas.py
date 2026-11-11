"""Contratos HTTP de /knowledge. La respuesta solo lleva el texto generado."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KnowledgeQueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class KnowledgeQueryResponse(BaseModel):
    answer: str
