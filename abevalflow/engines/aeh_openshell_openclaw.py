"""AEH OpenShell + OpenClaw evaluation engine adapter.

Same scoring as AEH (summary.yaml / report.json). Execution goes through
``python -m agent_eval.openshell.run`` against a forge-saw OpenShell gateway,
not Harbor trial pods.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from abevalflow.engines import register_engine
from abevalflow.engines.aeh import AEHEngine


@register_engine("aeh_openshell_openclaw")
class AEHOpenShellOpenClawEngine(AEHEngine):
    """AEH results with OpenShell/OpenClaw provenance."""

    name = "aeh_openshell_openclaw"

    def read_result(self, reports_dir: Path) -> dict[str, Any] | None:
        result = super().read_result(reports_dir)
        return self._stamp(result)

    def _merge_aeh_output(
        self,
        summary: dict[str, Any],
        run_result: dict[str, Any],
        source_dir: Path,
    ) -> dict[str, Any]:
        merged = super()._merge_aeh_output(summary, run_result, source_dir)
        return self._stamp(merged) or merged

    def _stamp(self, result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is None:
            return None
        result["eval_engine"] = self.name
        provenance = result.get("provenance")
        if isinstance(provenance, dict):
            provenance["eval_engine"] = self.name
        return result
