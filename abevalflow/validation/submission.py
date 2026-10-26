"""Validation submission services."""

import json
import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from abevalflow.schemas import EvalEngine, SubmissionMetadata

logger = logging.getLogger(__name__)


def _check_metadata_yaml(submission_dir: Path) -> tuple[list[str], SubmissionMetadata | None]:
    metadata_path = submission_dir / "metadata.yaml"
    if not metadata_path.is_file():
        return ["metadata.yaml is missing"], None
    try:
        raw = yaml.safe_load(metadata_path.read_text())
    except yaml.YAMLError as exc:
        return [f"metadata.yaml is not valid YAML: {exc}"], None
    if not isinstance(raw, dict):
        return ["metadata.yaml must contain a YAML mapping"], None
    try:
        model = SubmissionMetadata(**raw)
    except ValidationError as exc:
        errors = [
            f"metadata.yaml validation: {e['msg']} ({'.'.join(str(loc) for loc in e['loc'])})" for e in exc.errors()
        ]
        return errors, None
    return [], model


def _check_aeh_eval_yaml_file(eval_path: Path) -> list[str]:
    """Validate a single AEH eval.yaml file exists and has valid structure.

    AEH eval.yaml requires:
    - models.skill (or models block)
    - judges section (dict or list)
    - Optional: thresholds, mlflow, etc.
    """
    if not eval_path.is_file():
        return [f"{eval_path.name} is required for AEH evaluation"]

    try:
        data = yaml.safe_load(eval_path.read_text())
    except yaml.YAMLError as exc:
        return [f"{eval_path.name} is not valid YAML: {exc}"]

    if not isinstance(data, dict):
        return [f"{eval_path.name} must be a YAML mapping"]

    errors: list[str] = []
    filename = eval_path.name

    models = data.get("models")
    if not models:
        errors.append(f"{filename}: 'models' section is required")
    elif isinstance(models, dict):
        if not models.get("skill"):
            errors.append(f"{filename}: 'models.skill' is required (e.g., 'claude-sonnet-4-5')")
    elif isinstance(models, str):
        pass
    else:
        errors.append(f"{filename}: 'models' must be a mapping or string")

    if not data.get("judges"):
        errors.append(f"{filename}: 'judges' section is required")

    return errors


def _check_aeh_eval_yaml(submission_dir: Path) -> list[str]:
    """Validate eval.yaml exists and has valid AEH structure (single mode)."""
    return _check_aeh_eval_yaml_file(submission_dir / "eval.yaml")


def _check_aeh_plugin_dirs(submission_dir: Path, eval_path: Path) -> list[str]:
    """Validate skill roots and intentional MCP-only plugin directories."""
    if not eval_path.is_file():
        return []
    try:
        data = yaml.safe_load(eval_path.read_text()) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []

    runner = data.get("runner") or {}
    if not isinstance(runner, dict):
        return []
    plugin_dirs = runner.get("plugin_dirs") or []
    if not plugin_dirs:
        return []

    errors: list[str] = []
    skill_name = data.get("skill")
    execution = data.get("execution") or {}
    if isinstance(execution, dict) and execution.get("skill"):
        skill_name = skill_name or execution.get("skill")
    for rel in plugin_dirs:
        root = submission_dir / str(rel)
        if not root.is_dir():
            if not skill_name and str(rel).rstrip("/").endswith("-mcp-only"):
                continue
            errors.append(
                f"{eval_path.name}: plugin_dirs entry '{rel}/' is missing "
                f"(required for Claude slash-skill /{skill_name or '…'})"
            )
            continue
        has_top = (root / "SKILL.md").is_file()
        has_nested = any((child / "SKILL.md").is_file() for child in root.iterdir() if child.is_dir())

        skills_sub = root / "skills"
        has_skills_sub = skills_sub.is_dir() and any(
            (child / "SKILL.md").is_file() for child in skills_sub.iterdir() if child.is_dir()
        )
        has_any_skill = has_top or has_nested or has_skills_sub
        is_mcp_only = not skill_name and not has_any_skill and (root / "mcps.json").is_file()
        if not has_any_skill and not is_mcp_only:
            errors.append(
                f"{eval_path.name}: plugin_dirs '{rel}/' must contain SKILL.md (flat or nested "
                "skills/<name>/SKILL.md), or (with no execution.skill set) an mcps.json for an "
                "intentional MCP-only entry"
            )
        elif skill_name and not has_top:
            named = root / str(skill_name) / "SKILL.md"
            named_in_skills = root / "skills" / str(skill_name) / "SKILL.md"
            if not named.is_file() and not named_in_skills.is_file():
                errors.append(
                    f"{eval_path.name}: skill '{skill_name}' requires "
                    f"{rel}/{skill_name}/SKILL.md or {rel}/skills/{skill_name}/SKILL.md"
                )
    return errors


def _check_mock_mcp_servers(submission_dir: Path, eval_path: Path) -> list[str]:
    """Validate the per-case stdio mock MCP convention.

    For every ``cases/<case>/mock-mcps/<server>/fixture.json``:
    - fixture.json must be valid JSON shaped ``{"sequence": [...]}`` (the
      shape generic-mock-mcp-server's ``--strategy fixtures`` expects).
    - a matching shared ``submissions/<plugin>/mcp-servers/<server>/
      schema.json`` must exist (sibling of this submission_dir's parent --
      shared across every skill submitted under that plugin, NOT the real
      ``<plugin>/`` source tree) and be valid JSON.

    See abevalflow.harbor_extensions.aeh_task_enrichment's docstring for
    why schema is plugin-shared while fixtures are per-case.
    """
    if not eval_path.is_file():
        return []
    try:
        data = yaml.safe_load(eval_path.read_text()) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []

    dataset = data.get("dataset") or {}
    dataset_rel = "cases"
    if isinstance(dataset, dict):
        dataset_rel = str(dataset.get("path") or "cases").strip() or "cases"

    cases_root = submission_dir / dataset_rel
    if not cases_root.is_dir():
        return []

    mcp_schema_root = submission_dir.parent / "mcp-servers"
    errors: list[str] = []
    schema_cache: dict[str, bool] = {}

    for case_dir in sorted(p for p in cases_root.iterdir() if p.is_dir()):
        mock_mcps_dir = case_dir / "mock-mcps"
        if not mock_mcps_dir.is_dir():
            continue
        for server_dir in sorted(p for p in mock_mcps_dir.iterdir() if p.is_dir()):
            server = server_dir.name
            fixture_path = server_dir / "fixture.json"
            rel = fixture_path.relative_to(submission_dir)
            if not fixture_path.is_file():
                errors.append(f"{rel}: mock-mcps/{server}/ must contain fixture.json")
                continue
            try:
                fixture_data = json.loads(fixture_path.read_text())
            except json.JSONDecodeError as exc:
                errors.append(f"{rel}: invalid JSON: {exc}")
                continue
            if not isinstance(fixture_data, dict) or not isinstance(fixture_data.get("sequence"), list):
                errors.append(f'{rel}: must be a JSON object with a "sequence" list')

            if server not in schema_cache:
                schema_path = mcp_schema_root / server / "schema.json"
                if not schema_path.is_file():
                    errors.append(
                        f"cases/*/mock-mcps/{server}/ requires a shared schema.json at "
                        f"{schema_path} (plugin-shared, sibling of this submission's own directory)"
                    )
                    schema_cache[server] = False
                else:
                    try:
                        json.loads(schema_path.read_text())
                        schema_cache[server] = True
                    except json.JSONDecodeError as exc:
                        errors.append(f"{schema_path}: invalid JSON: {exc}")
                        schema_cache[server] = False

    return errors


def _check_aeh_tool_interception(submission_dir: Path, eval_path: Path) -> list[str]:
    """Validate inputs.tools / permissions / tool_handlers.yaml for Harbor MCP hooks.

    When ``inputs.tools`` is declared, each entry needs ``match``, and a
    submission-level ``tool_handlers.yaml`` is required so OpenShift Harbor
    trials get reliable PreToolUse patterns (heuristic fallback is weak).
    """
    if not eval_path.is_file():
        return []
    try:
        data = yaml.safe_load(eval_path.read_text()) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []

    errors: list[str] = []
    filename = eval_path.name

    permissions = data.get("permissions")
    if permissions is not None:
        if not isinstance(permissions, dict):
            errors.append(f"{filename}: 'permissions' must be a mapping with allow/deny lists")
        else:
            for key in ("allow", "deny"):
                if key in permissions and not isinstance(permissions[key], list):
                    errors.append(f"{filename}: permissions.{key} must be a list")

    inputs = data.get("inputs")
    if inputs is None:
        return errors
    if not isinstance(inputs, dict):
        errors.append(f"{filename}: 'inputs' must be a mapping")
        return errors

    tools = inputs.get("tools")
    if tools is None:
        return errors
    if not isinstance(tools, list):
        errors.append(f"{filename}: inputs.tools must be a list")
        return errors

    for i, entry in enumerate(tools):
        if not isinstance(entry, dict):
            errors.append(f"{filename}: inputs.tools[{i}] must be a mapping")
            continue
        if not str(entry.get("match") or "").strip():
            errors.append(f"{filename}: inputs.tools[{i}] requires a non-empty 'match' field")

    handlers = submission_dir / "tool_handlers.yaml"
    if tools and not handlers.is_file():
        errors.append(
            f"{filename}: inputs.tools is set but tool_handlers.yaml is missing "
            f"next to {filename} (required for reliable Harbor tool/MCP interception)"
        )
    elif handlers.is_file():
        try:
            handlers_data = yaml.safe_load(handlers.read_text())
        except yaml.YAMLError as exc:
            errors.append(f"tool_handlers.yaml is not valid YAML: {exc}")
        else:
            if handlers_data is not None and not isinstance(handlers_data, (dict, list)):
                errors.append("tool_handlers.yaml must be a YAML mapping or list")

    return errors


def _check_aeh_cases(submission_dir: Path) -> list[str]:
    """Validate cases/ directory exists with at least one case.

    Each case directory must contain:
    - input.yaml (required)
    - annotations.yaml (optional)
    """
    cases_dir = submission_dir / "cases"
    if not cases_dir.is_dir():
        return ["cases/ directory is required for AEH evaluation"]

    case_dirs = [d for d in cases_dir.iterdir() if d.is_dir()]
    if not case_dirs:
        return ["cases/ must contain at least one case directory (e.g., cases/case-001/)"]

    errors: list[str] = []
    for case_dir in sorted(case_dirs):
        rel = case_dir.relative_to(submission_dir)
        input_path = case_dir / "input.yaml"
        if not input_path.is_file():
            errors.append(f"{rel}: missing required input.yaml")
            continue

        try:
            data = yaml.safe_load(input_path.read_text())
        except yaml.YAMLError as exc:
            errors.append(f"{rel}/input.yaml: invalid YAML: {exc}")
            continue

        if not isinstance(data, dict):
            errors.append(f"{rel}/input.yaml: must be a YAML mapping")

    return errors


def _case_has_annotations(submission_dir: Path) -> bool:
    """True if at least one cases/<id>/annotations.yaml exists and is non-empty.

    Used to decide whether the pairwise judge is required to be grounded in
    ground truth (see ``_check_aeh_pairwise_contract``) -- a submission with
    no annotated ground truth has nothing to ground against.
    """
    cases_dir = submission_dir / "cases"
    if not cases_dir.is_dir():
        return False
    for case_dir in cases_dir.iterdir():
        if not case_dir.is_dir():
            continue
        annotations_path = case_dir / "annotations.yaml"
        if annotations_path.is_file() and annotations_path.read_text().strip():
            return True
    return False


def _check_aeh_pairwise_contract(eval_path: Path, submission_dir: Path | None = None) -> list[str]:
    """Require pairwise output paths, a comparison judge, and annotation grounding when available."""
    if not eval_path.is_file():
        return []
    try:
        data = yaml.safe_load(eval_path.read_text()) or {}
    except yaml.YAMLError:
        return []

    errors: list[str] = []
    filename = eval_path.name

    outputs = data.get("outputs") or []
    has_path = False
    if isinstance(outputs, list):
        for out in outputs:
            if isinstance(out, dict) and out.get("path"):
                has_path = True
                break
            if isinstance(out, str) and out.strip():
                has_path = True
                break
    if not has_path:
        errors.append(
            f"{filename}: pairwise mode requires outputs: with at least one path "
            "(e.g. path: output) so score.py can compare case artifacts"
        )

    judges = data.get("judges") or []
    has_pairwise = False
    pairwise_prompt: str | None = None
    if isinstance(judges, list):
        ordered_judges = sorted(
            (judge for judge in judges if isinstance(judge, dict)),
            key=lambda judge: "pairwise" not in str(judge.get("name") or "").lower(),
        )
        for judge in ordered_judges:
            if not isinstance(judge, dict):
                continue
            name = str(judge.get("name") or "").lower()
            jtype = str(judge.get("type") or "").lower()
            prompt = judge.get("prompt") or judge.get("prompt_file")
            if name == "pairwise" or (jtype == "llm" and "pairwise" in name):
                has_pairwise = True
                pairwise_prompt = judge.get("prompt")
                break
            if jtype == "llm" and prompt:
                has_pairwise = True
                pairwise_prompt = judge.get("prompt")
                break
    elif isinstance(judges, dict):
        pairwise_entry = judges.get("pairwise")
        if isinstance(pairwise_entry, dict):
            has_pairwise = True
            pairwise_prompt = pairwise_entry.get("prompt")
        else:
            for v in judges.values():
                if isinstance(v, dict) and (v.get("type") == "llm" or v.get("prompt")):
                    has_pairwise = True
                    pairwise_prompt = v.get("prompt")
                    break
    if not has_pairwise:
        errors.append(
            f"{filename}: pairwise mode requires a 'pairwise' LLM judge "
            "(or another LLM judge with prompt) for score.py pairwise"
        )
    elif submission_dir is not None and _case_has_annotations(submission_dir):
        prompt_text = pairwise_prompt or ""
        if "{{ annotations" not in prompt_text and "{{annotations" not in prompt_text:
            errors.append(
                f"{filename}: this submission has case annotations.yaml (ground truth), but the "
                "pairwise judge's prompt does not reference '{{ annotations }}'. An ungrounded "
                "pairwise judge compares only the two output artifacts and can side with a "
                "plausible-sounding wrong answer over the documented ground truth, overriding a "
                "correctly-grounded per-case judge. Add '{{ annotations }}' to the pairwise prompt."
            )

    return errors


def _check_aeh_structure(
    submission_dir: Path,
    aeh_mode: str = "single",
    control_config: str = "eval-control.yaml",
    treatment_config: str = "eval-treatment.yaml",
) -> list[str]:
    """Run all AEH-specific validation checks.

    Args:
        submission_dir: Path to the submission directory
        aeh_mode: Either "single" or "pairwise"
        control_config: Control variant config filename for pairwise mode
        treatment_config: Treatment variant config filename for pairwise mode
    """
    errors: list[str] = []

    if aeh_mode == "pairwise":
        control_path = submission_dir / control_config
        treatment_path = submission_dir / treatment_config

        if not control_path.is_file():
            errors.append(f"AEH pairwise: missing {control_config}")
        else:
            errors.extend(_check_aeh_eval_yaml_file(control_path))
            errors.extend(_check_aeh_plugin_dirs(submission_dir, control_path))
            errors.extend(_check_aeh_tool_interception(submission_dir, control_path))
            errors.extend(_check_mock_mcp_servers(submission_dir, control_path))

        if not treatment_path.is_file():
            errors.append(f"AEH pairwise: missing {treatment_config}")
        else:
            errors.extend(_check_aeh_eval_yaml_file(treatment_path))
            errors.extend(_check_aeh_plugin_dirs(submission_dir, treatment_path))
            errors.extend(_check_aeh_tool_interception(submission_dir, treatment_path))
            errors.extend(_check_mock_mcp_servers(submission_dir, treatment_path))

            errors.extend(_check_aeh_pairwise_contract(treatment_path, submission_dir))
    else:
        errors.extend(_check_aeh_eval_yaml(submission_dir))
        errors.extend(_check_aeh_plugin_dirs(submission_dir, submission_dir / "eval.yaml"))
        errors.extend(_check_aeh_tool_interception(submission_dir, submission_dir / "eval.yaml"))
        errors.extend(_check_mock_mcp_servers(submission_dir, submission_dir / "eval.yaml"))

    errors.extend(_check_aeh_cases(submission_dir))
    errors.extend(_check_aeh_skill_matches_metadata(submission_dir, aeh_mode, control_config, treatment_config))
    return errors


def _check_aeh_skill_matches_metadata(
    submission_dir: Path,
    aeh_mode: str,
    control_config: str,
    treatment_config: str,
) -> list[str]:
    """Require eval.yaml skill to match metadata.name (analyze uses submission-name).

    Pipeline report.json is keyed by submission-name (usually metadata.name). AEH
    run dirs use the skill field for score.py. Mismatches break analyze after a
    successful eval.
    """
    meta_path = submission_dir / "metadata.yaml"
    if not meta_path.is_file():
        return []
    try:
        meta = yaml.safe_load(meta_path.read_text()) or {}
    except yaml.YAMLError:
        return []
    meta_name = meta.get("name")
    if not meta_name:
        return []

    config_paths: list[Path]
    if aeh_mode == "pairwise":
        config_paths = [submission_dir / control_config, submission_dir / treatment_config]
    else:
        config_paths = [submission_dir / "eval.yaml"]

    errors: list[str] = []
    for path in config_paths:
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            continue
        skill = data.get("skill")
        if skill and skill != meta_name:
            errors.append(
                f"{path.name}: skill '{skill}' must match metadata.name '{meta_name}' "
                "(analyze reads reports/<submission-name>/report.json)"
            )
    return errors


def validate_submission(
    submission_dir: Path,
    eval_engine: EvalEngine = EvalEngine.AEH,
    aeh_mode: str = "single",
    aeh_control_config: str = "eval-control.yaml",
    aeh_treatment_config: str = "eval-treatment.yaml",
) -> list[str]:
    """Run validation checks based on the eval engine and return error strings.

    Args:
        submission_dir: Path to the submission directory
        eval_engine: Which evaluation engine to validate for
        aeh_mode: AEH mode - "single" or "pairwise"
        aeh_control_config: Control config filename for AEH pairwise mode
        aeh_treatment_config: Treatment config filename for AEH pairwise mode
    """
    logger.info("Validating submission: %s (eval_engine=%s)", submission_dir, eval_engine)
    errors: list[str] = []

    if eval_engine != EvalEngine.AEH:
        return [f"Unsupported evaluation engine: {eval_engine}; expected aeh"]
    metadata_errors, _ = _check_metadata_yaml(submission_dir)
    errors.extend(metadata_errors)
    errors.extend(
        _check_aeh_structure(
            submission_dir,
            aeh_mode=aeh_mode,
            control_config=aeh_control_config,
            treatment_config=aeh_treatment_config,
        )
    )

    if errors:
        logger.warning("Validation failed with %d error(s)", len(errors))
    else:
        logger.info("Validation passed")
    return errors
