"""Discovery of supported upstream scoring and reporting resources."""

from __future__ import annotations

import os
from pathlib import Path


def find_aeh_script(name: str, explicit: Path | None = None) -> Path | None:
    """Locate an upstream AEH resource from an explicit path, installation, or image."""
    import importlib.util

    candidates = []
    if explicit:
        candidates.append(explicit if name == "score.py" else explicit.parent / name)
    configured = os.environ.get("AEH_SCORE_PY")
    if configured:
        candidates.append(Path(configured).parent / name)
    if explicit and candidates[0].is_file():
        return candidates[0]
    try:
        spec = importlib.util.find_spec("agent_eval")
    except ValueError:
        spec = None
    if spec and spec.origin:
        root = Path(spec.origin).parent.parent
        candidates += [root / "skills/eval-run/scripts" / name, root / "agent_eval/resources" / name]
    candidates += [
        Path(__file__).resolve().parent.parent / "_vendor/aeh" / name,
        Path("/opt/agent-eval-harness/skills/eval-run/scripts") / name,
        Path("skills/eval-run/scripts") / name,
    ]
    return next((path for path in candidates if path.is_file()), None)
