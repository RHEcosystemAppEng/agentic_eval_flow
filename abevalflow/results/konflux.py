"""Project versioned single-skill evidence into bounded Konflux batch results."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from abevalflow.results.run import RunResult, write_json
from abevalflow.workflow.batch import BatchResult, validate_skills


def image_repository(value: str) -> str:
    """Ignore an optional tag when identifying immutable image subjects."""
    prefix, separator, name = value.rpartition("/")
    return prefix + separator + name.split(":", 1)[0]


def outcome(state: str, note: str, successes=0, failures=0, warnings=0) -> dict:
    return dict(
        result=state,
        timestamp=datetime.now(UTC).isoformat(),
        note=note[:500],
        successes=successes,
        failures=failures,
        warnings=warnings,
    )


def project(steps, required: set[str], label: str) -> dict:
    by_name = {s.name: s for s in steps}
    if len(by_name) != len(steps):
        return outcome("ERROR", label + ": duplicate evidence", failures=1)
    missing = required - by_name.keys()
    selected = [by_name[name] for name in required if name in by_name]
    errors = [s.name for s in selected if s.blocking and (s.status != "completed" or s.verdict == "unavailable")]
    failed = [s.name for s in selected if s.blocking and s.verdict == "fail"]
    problems = sorted(missing | set(errors + failed))
    state = "ERROR" if missing or errors else "FAILURE" if failed else "SUCCESS"
    warnings = sum(
        s.verdict == "warn" or (not s.blocking and (s.status != "completed" or s.verdict == "fail")) for s in selected
    )
    return outcome(
        state,
        label + (": " + ", ".join(problems) if problems else ": completed"),
        sum(s.status == "completed" and s.verdict in {"pass", "warn"} for s in selected),
        len(problems),
        warnings,
    )


def project_run(run: RunResult, skill: str, tiers: set[int], persist: set[str]) -> dict:
    values = {}
    for tier, key, required in [
        (1, "tier1", {"prepare", "structure", "security", "quality", "report"}),
        (2, "tier2", {"prepare", "submission", "evaluate", "report"}),
    ]:
        review = "skill-review" if tier == 1 else "submission-review"
        if review in run.requested_steps:
            required.add(review)
        values[key] = (
            project(run.steps, required, f"{skill} Tier {tier}")
            if tier in tiers
            else outcome("SKIPPED", f"{skill}: Tier {tier} not requested")
        )
    values["publication"] = project(run.publication, persist, f"{skill} publication")
    return values


def aggregate(values: list[dict], label: str) -> dict:
    selected = [v for v in values if v["result"] != "SKIPPED"]
    if values and not selected:
        return outcome("SKIPPED", f"{label}: not requested")
    errors = sum(v["result"] == "ERROR" for v in selected)
    failed = sum(v["result"] == "FAILURE" for v in selected)
    state = "ERROR" if errors else "FAILURE" if failed else "SUCCESS"
    return outcome(
        state,
        f"{label}: {len(selected)} skill(s), {errors} error(s), {failed} failure(s)",
        len(selected) - errors - failed,
        errors + failed,
        sum(v["warnings"] for v in selected),
    )


def summarize(directory: Path, skills: list[str], revision: str, tiers: set[int], persist: set[str]) -> dict:
    """Validate identity and completeness against caller inputs; never infer success from existence."""
    validate_skills(skills)
    batch = BatchResult.model_validate_json((directory / "batch.json").read_text())
    if (
        batch.source_revision != revision
        or [i.skill_id for i in batch.items] != skills
        or not batch.completed_at
        or set(batch.tiers) != tiers
        or set(batch.persist) != persist
    ):
        raise ValueError("Batch identity or completion does not match request")
    items = []
    for item in batch.items:
        try:
            if item.status != "completed":
                raise ValueError("Skill did not complete")
            run_dir = (directory / item.run_dir).resolve()
            if not run_dir.is_relative_to(directory.resolve() / "runs"):
                raise ValueError("Run directory leaves batch")
            run = RunResult.model_validate_json((run_dir / "run.json").read_text())
            if (
                run.schema_version not in {"1.0", "1.1"}
                or run.run_id != item.run_id
                or run.source_revision != revision
                or not run.completed_at
                or set(run.config["tiers"]) != tiers
                or set(run.config["persist"]) != persist
            ):
                raise ValueError("Run identity does not match batch")
            config = run.config
            pack, name = item.skill_id.split("/")
            if Path(config["skill"]) != Path(config["source"]) / pack / "skills" / name:
                raise ValueError("Skill identity does not match batch")
            values = project_run(run, item.skill_id, tiers, persist)
        except (OSError, ValueError, KeyError, TypeError):
            values = {
                f"tier{t}": outcome("ERROR", f"{item.skill_id}: missing or invalid evidence", failures=1)
                if t in tiers
                else outcome("SKIPPED", f"Tier {t} not requested")
                for t in (1, 2)
            }
            values["publication"] = outcome("ERROR", f"{item.skill_id}: missing or invalid evidence", failures=1)
        items.append({"skill_id": item.skill_id, "run_dir": item.run_dir, **values})
    return dict(
        schema_version="1.0",
        source_revision=revision,
        tiers=sorted(tiers),
        changed_skills=skills,
        items=items,
        results={
            key: aggregate([i[key] for i in items], label)
            for key, label in [("tier1", "Tier 1"), ("tier2", "Tier 2"), ("publication", "Publication")]
        },
    )


def package_batch(directory: Path) -> None:
    """Package all skill report evidence, including timeout histories, without source or credentials."""
    batch = BatchResult.model_validate_json((directory / "batch.json").read_text())
    with tarfile.open(directory / "reports.tar.gz", "w:gz") as archive:
        archive.add(directory / "batch.json", arcname="batch.json")
        if (directory / "component-evaluation.json").is_file():
            archive.add(directory / "component-evaluation.json", arcname="component-evaluation.json")
        for item in batch.items:
            run = (directory / item.run_dir).resolve()
            if not run.is_relative_to(directory.resolve() / "runs"):
                raise ValueError("Run directory leaves batch")
            for name in ("run.json", "reports"):
                if (run / name).exists():
                    archive.add(run / name, arcname=f"{item.run_dir}/{name}")


def component_evidence(directory: Path, manifest_file: Path, image_url: str, image_digest: str) -> dict:
    """Bind a complete plugin evaluation to a built image before archiving it.

    This records observations, including failures. Managed release policy decides
    which tiers and individual checks are mandatory; authored blocking flags do
    not turn failed observations into successful release evidence.
    """
    manifest = json.loads(manifest_file.read_text())
    plugin = manifest["plugin_id"]
    skills = manifest["skills"]
    revision = manifest["source_revision"]
    if (
        manifest.get("schema_version") != "1.0"
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", plugin)
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision)
        or not skills
        or any(not skill.startswith(plugin + "/") for skill in skills)
        or not image_url
        or "@" in image_url
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest)
    ):
        raise ValueError("Invalid component identity")
    validate_skills(skills)
    batch = BatchResult.model_validate_json((directory / "batch.json").read_text())
    summary = summarize(directory, skills, revision, set(batch.tiers), set(batch.persist))
    evaluations = []
    image_ref = f"{image_url}@{image_digest}"
    for item in batch.items:
        run_dir = (directory / item.run_dir).resolve()
        if not run_dir.is_relative_to(directory.resolve() / "runs") or item.status != "completed":
            raise ValueError("Incomplete component evaluation")
        run = RunResult.model_validate_json((run_dir / "run.json").read_text())
        if run.config.get("built_image_ref") != image_ref or run.source_revision != revision:
            raise ValueError("Evaluation does not match the built component")
        if len({step.name for step in run.steps}) != len(run.steps):
            raise ValueError("Duplicate evaluation steps")
        report_file = run_dir / "reports/report.json"
        report = json.loads(report_file.read_text()) if report_file.is_file() else {}
        treatment = report.get("summary", {}).get("treatment", {})
        evaluations.append(
            {
                "skill_id": item.skill_id,
                "run_id": run.run_id,
                "evaluator_version": run.evaluator_version,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "metrics": {
                    "mean_reward": report.get("mean_reward", treatment.get("mean_reward")),
                    "n_trials": treatment.get("n_trials", report.get("n_trials")),
                    "n_errors": treatment.get("n_errors", report.get("n_errors")),
                },
                "checks": [
                    {"name": step.name, "tier": step.tier, "status": step.status, "verdict": step.verdict}
                    for step in run.steps
                ],
            }
        )
    evidence = {
        "schema_version": "1.0",
        "plugin_id": plugin,
        "source_revision": revision,
        "image_ref": f"{image_repository(image_url)}@{image_digest}",
        "payload_digest": manifest["payload_digest"],
        "skills": skills,
        "tiers": list(batch.tiers),
        "completed_at": batch.completed_at.isoformat(),
        "evaluations": evaluations,
        "results": summary["results"],
    }
    write_json(directory / "component-evaluation.json", evidence)
    return evidence


def emit_results(
    directory: Path,
    skills: list[str],
    revision: str,
    tiers: set[int],
    persist: set[str],
    results_dir: Path,
    image_url="",
    image_digest="",
    artifact_file: Path | None = None,
    fail_on_non_success: bool = True,
) -> int:
    """Emit evidence before gating. Tier 2 remains informational; OCI attachment is a separate gate."""
    try:
        summary = summarize(directory, skills, revision, tiers, persist)
    except (OSError, ValueError, KeyError, TypeError):
        error = outcome("ERROR", "Missing, incomplete, or mismatched batch evidence", failures=1)
        summary = dict(
            schema_version="1.0",
            source_revision=revision,
            tiers=sorted(tiers),
            changed_skills=skills,
            items=[],
            results=dict.fromkeys(("tier1", "tier2", "publication"), error),
        )
    artifact = dict(uri="", digest="")
    if image_url or image_digest:
        try:
            if not image_url or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
                raise ValueError("Invalid OCI subject")
            attached = json.loads(artifact_file.read_text()) if artifact_file else {}
            if attached.get("uri") != image_url or not re.fullmatch(r"sha256:[0-9a-f]{64}", attached.get("digest", "")):
                raise ValueError("Missing OCI attachment")
            artifact = attached
            summary["results"]["publication"]["successes"] += 1
        except (OSError, ValueError, TypeError):
            summary["results"]["publication"] = outcome("ERROR", "OCI evidence attachment failed", failures=1)
    write_json(directory / "summary.json", summary)
    for name, key in [
        ("TEST_OUTPUT", "tier1"),
        ("TEST_OUTPUT_TIER_A", "tier1"),
        ("TEST_OUTPUT_TIER_B", "tier2"),
        ("TEST_OUTPUT_PUBLICATION", "publication"),
    ]:
        write_json(results_dir / name, summary["results"][key])
    write_json(
        results_dir / "IMAGES_PROCESSED",
        {
            "image": {
                "pullspec": image_url,
                "digests": [image_digest] if image_url and image_digest else [],
            }
        },
    )
    evidence_file = directory / "component-evaluation.json"
    attested_summary = {}
    if evidence_file.is_file():
        evidence = json.loads(evidence_file.read_text())
        if (
            evidence["image_ref"] != f"{image_repository(image_url)}@{image_digest}"
            or evidence["source_revision"] != revision
        ):
            raise ValueError("Component evidence identity does not match results")
        attested_summary = {
            key: evidence[key]
            for key in (
                "schema_version",
                "plugin_id",
                "source_revision",
                "image_ref",
                "payload_digest",
                "tiers",
                "completed_at",
            )
        }
        attested_summary.update(
            skill_count=len(evidence["skills"]),
            evidence_digest="sha256:" + hashlib.sha256(evidence_file.read_bytes()).hexdigest(),
        )
    write_json(results_dir / "PLUGIN_EVALUATION", attested_summary)
    write_json(results_dir / "skill-reports-ARTIFACT_OUTPUTS", artifact)
    return int(
        fail_on_non_success
        and any(summary["results"][key]["result"] not in {"SUCCESS", "SKIPPED"} for key in ("tier1", "publication"))
    )
