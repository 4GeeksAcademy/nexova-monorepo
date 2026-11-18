"""
Hace importable `incidents_analyzer` sin necesidad de `pip install`.

Mismo patrón que `services/api/app/main.py`: si el paquete no está instalado,
se añade `packages/incidents-analyzer` al `sys.path` (en Docker se monta en
`/packages/incidents-analyzer`).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ANALYZER_DIRNAME = "incidents-analyzer"


def ensure_analyzer_importable() -> None:
    try:
        import incidents_analyzer  # noqa: F401

        return
    except ImportError:
        pass

    # packages/shared/nexova_shared/_bootstrap.py → packages/
    packages_dir = Path(__file__).resolve().parents[2]
    candidates = [packages_dir / _ANALYZER_DIRNAME, Path("/packages") / _ANALYZER_DIRNAME]
    for candidate in candidates:
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
            return
