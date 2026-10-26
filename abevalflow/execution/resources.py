"""Discovery of supported upstream scoring and reporting resources."""

from __future__ import annotations

import os
from pathlib import Path


def find_aeh_script(name: str, explicit: Path | None = None) -> Path | None:
    """Locate an upstream AEH resource from an explicit path, installation, or image."""
    from abevalflow.execution.runtime import RuntimeUnavailable, resolve_runtime

    configured = explicit or (Path(os.environ["AEH_SCORE_PY"]) if os.environ.get("AEH_SCORE_PY") else None)
    if configured:
        candidate = configured if name == "score.py" else configured.parent / name
        return candidate if candidate.is_file() else None
    try:
        return resolve_runtime().script(name)
    except RuntimeUnavailable:
        return None
