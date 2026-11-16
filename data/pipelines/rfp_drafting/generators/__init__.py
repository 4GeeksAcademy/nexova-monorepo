"""Un generador por departamento, claramente separados (BRIEFING §2.1)."""

from rfp_drafting.generators.base import DepartmentGenerator
from rfp_drafting.generators.capacitacion import GENERATOR as CAPACITACION
from rfp_drafting.generators.seleccion import GENERATOR as SELECCION
from rfp_drafting.generators.soporte import GENERATOR as SOPORTE

GENERATORS: dict[str, DepartmentGenerator] = {
    g.department_id: g for g in (SELECCION, CAPACITACION, SOPORTE)
}

__all__ = ["GENERATORS", "DepartmentGenerator"]
