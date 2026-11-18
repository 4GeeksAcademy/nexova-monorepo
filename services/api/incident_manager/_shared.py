"""
Hace importable `nexova_shared` (`packages/shared`) sin necesidad de `pip install`.

Mismo patrón que `app/main.py` con `incidents_analyzer`: si el paquete no está
instalado se añade su carpeta al `sys.path` (en Docker, bind mount en
`/packages/shared`).
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_shared_importable() -> None:
    try:
        import nexova_shared  # noqa: F401

        return
    except ImportError:
        pass

    # services/api/incident_manager/_shared.py → raíz del monorepo. En Docker el
    # archivo vive en /app/incident_manager/ (menos niveles): sin raíz, solo /packages/shared.
    candidates = [Path("/packages/shared")]
    parents = Path(__file__).resolve().parents
    if len(parents) > 3:
        candidates.insert(0, parents[3] / "packages" / "shared")
    for candidate in candidates:
        if (candidate / "nexova_shared").is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
            return


ensure_shared_importable()
