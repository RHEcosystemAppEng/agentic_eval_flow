"""Validate a submission directory against the submission contract.

Checks vary by eval-engine mode:

Harbor (default):
  1. instruction.md exists and is non-empty
  2. skills/ contains at least one SKILL.md (flat or nested layout)
  3. tests/test_outputs.py compiles
  4. tests/llm_judge.py compiles if present
  5. metadata.yaml passes Pydantic schema validation
  6. supportive/ total size < 50 MB

ASE (agent-skills-eval):
  1. SKILL.md exists with valid YAML frontmatter containing 'name'
  2. evals/evals.json exists and is valid
  3. metadata.yaml passes Pydantic schema validation

Both: all checks from both engines apply.

Exit codes: 0 = pass, 1 = validation failure (structured JSON on stdout).
"""

import argparse
import ast
import json
import logging
import re
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from abevalflow.schemas import AEH_FAMILY, EVAL_ENGINE_CLI_CHOICES, EvalEngine, SubmissionMetadata

logger = logging.getLogger(__name__)

MAX_SUPPORTIVE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

_YAML_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _check_instruction_md(submission_dir: Path) -> list[str]:
    instruction = submission_dir / "instruction.md"
    if not instruction.is_file():
        return ["instruction.md is missing"]
    if not instruction.read_text().strip():
        return ["instruction.md is empty"]
    return []


def _check_skills_dir(submission_dir: Path) -> list[str]:
    """Validate skills/ contains at least one SKILL.md.

    Supports two layouts:
      - Flat:   skills/SKILL.md  (single skill at top level)
      - Nested: skills/<name>/SKILL.md  (Claude discovers each subdirectory as a skill)

    Both are valid — Claude Code scans the skills_dir for subdirectories
    containing SKILL.md and registers each as a /skill-name command.
    """
    skills_dir = submission_dir / "skills"
    if not skills_dir.is_dir():
        return ["skills/ directory is missing"]

    skill_files: list[Path] = []
    # Flat layout: skills/SKILL.md
    top_level = skills_dir / "SKILL.md"
    if top_level.is_file():
        skill_files.append(top_level)
    # Nested layout: skills/<name>/SKILL.md
    for child in sorted(skills_dir.iterdir()):
        if child.is_dir():
            nested = child / "SKILL.md"
            if nested.is_file():
                skill_files.append(nested)

    if not skill_files:
        return ["skills/ must contain at least one SKILL.md (either skills/SKILL.md or skills/<name>/SKILL.md)"]

    errors: list[str] = []
    for sf in skill_files:
        rel = sf.relative_to(submission_dir)
        if not sf.read_text().strip():
            errors.append(f"{rel} is empty")

    return errors


def _check_py_compiles(file_path: Path) -> list[str]:
    if not file_path.is_file():
        return [f"{file_path.name} is missing"]
    try:
        source = file_path.read_text()
        ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        return [f"{file_path.name} does not compile: {exc}"]
    return []


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


def _check_supportive_size(submission_dir: Path) -> list[str]:
    supportive_dir = submission_dir / "supportive"
    if not supportive_dir.is_dir():
        return []
    total_size = sum(f.stat().st_size for f in supportive_dir.rglob("*") if f.is_file())
    if total_size > MAX_SUPPORTIVE_SIZE_BYTES:
        size_mb = total_size / (1024 * 1024)
        return [f"supportive/ exceeds 50 MB limit ({size_mb:.1f} MB)"]
    return []


def _check_edge_cases(submission_dir: Path) -> list[str]:
    """Validate optional edge_cases/ directory structure.

    Each file must be a non-empty .md file with a descriptive name.
    The directory is optional — submissions without it pass validation.
    """
    edge_cases_dir = submission_dir / "edge_cases"
    if not edge_cases_dir.is_dir():
        return []

    errors: list[str] = []
    md_files = list(edge_cases_dir.glob("*.md"))

    if not md_files:
        errors.append("edge_cases/ directory exists but contains no .md files")
        return errors

    for md_file in sorted(md_files):
        if not md_file.read_text().strip():
            errors.append(f"edge_cases/{md_file.name} is empty")

    non_md = [f.name for f in edge_cases_dir.iterdir() if f.is_file() and f.suffix != ".md"]
    if non_md:
        errors.append(f"edge_cases/ contains non-.md files: {', '.join(non_md)}")

    return errors


# ---------------------------------------------------------------------------
# ASE-specific checks
# ---------------------------------------------------------------------------


def _check_skill_md_frontmatter(submission_dir: Path) -> list[str]:
    """Validate that at least one SKILL.md has YAML frontmatter with 'name'."""
    skill_files: list[Path] = []

    skills_dir = submission_dir / "skills"
    if skills_dir.is_dir():
        top_level = skills_dir / "SKILL.md"
        if top_level.is_file():
            skill_files.append(top_level)
        for child in sorted(skills_dir.iterdir()):
            if child.is_dir():
                nested = child / "SKILL.md"
                if nested.is_file():
                    skill_files.append(nested)

    if not skill_files:
        return ["No SKILL.md found under skills/ for ASE validation"]

    errors: list[str] = []
    for sf in skill_files:
        rel = sf.relative_to(submission_dir)
        content = sf.read_text()
        match = _YAML_FRONTMATTER_RE.match(content)
        if not match:
            errors.append(f"{rel} is missing YAML frontmatter (---...--- block)")
            continue
        try:
            fm = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            errors.append(f"{rel} has invalid YAML frontmatter: {exc}")
            continue
        if not isinstance(fm, dict):
            errors.append(f"{rel} frontmatter must be a YAML mapping")
            continue
        if not fm.get("name"):
            errors.append(f"{rel} frontmatter is missing required 'name' field")

    return errors


def _check_evals_json(submission_dir: Path) -> list[str]:
    """Validate evals/evals.json for agent-skills-eval format.

    Note: Missing evals.json is NOT an error - the pipeline will generate it
    from SKILL.md. This function only validates format when the file exists.
    """
    evals_path = submission_dir / "evals" / "evals.json"
    if not evals_path.is_file():
        logger.info("evals/evals.json not present - will be generated from SKILL.md")
        return []  # Not an error - pipeline will generate it

    try:
        data = json.loads(evals_path.read_text())
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"evals/evals.json is not valid JSON: {exc}"]

    if not isinstance(data, dict):
        return ["evals/evals.json must be a JSON object"]

    evals = data.get("evals")
    if not isinstance(evals, list) or len(evals) == 0:
        return ["evals/evals.json must contain a non-empty 'evals' array"]

    errors: list[str] = []
    for i, ev in enumerate(evals):
        prefix = f"evals/evals.json evals[{i}]"
        if not isinstance(ev, dict):
            errors.append(f"{prefix}: must be a JSON object")
            continue
        if not ev.get("prompt"):
            errors.append(f"{prefix}: missing required 'prompt' field")
        if not ev.get("assertions") and not ev.get("expected_output"):
            errors.append(f"{prefix}: must have 'assertions' or 'expected_output'")

    return errors


# ---------------------------------------------------------------------------
# MCPChecker-specific checks
# ---------------------------------------------------------------------------


def _check_mcpchecker_eval_yaml(submission_dir: Path) -> list[str]:
    """Validate eval.yaml exists and has valid MCPChecker structure."""
    eval_path = submission_dir / "eval.yaml"
    if not eval_path.is_file():
        return ["eval.yaml is required for MCPChecker evaluation"]

    try:
        data = yaml.safe_load(eval_path.read_text())
    except yaml.YAMLError as exc:
        return [f"eval.yaml is not valid YAML: {exc}"]

    if not isinstance(data, dict):
        return ["eval.yaml must be a YAML mapping"]

    errors: list[str] = []

    kind = data.get("kind")
    if kind != "Eval":
        errors.append(f"eval.yaml: 'kind' must be 'Eval', got '{kind}'")

    api_version = data.get("apiVersion", "")
    if not api_version.startswith("mcpchecker/"):
        errors.append(f"eval.yaml: 'apiVersion' must start with 'mcpchecker/', got '{api_version}'")

    metadata = data.get("metadata", {})
    if not metadata.get("name"):
        errors.append("eval.yaml: 'metadata.name' is required")

    return errors


def _check_mcpchecker_mcp_config(submission_dir: Path) -> list[str]:
    """Validate mcp-config.yaml exists for MCPChecker."""
    config_path = submission_dir / "mcp-config.yaml"
    if not config_path.is_file():
        return ["mcp-config.yaml is required for MCPChecker evaluation"]

    try:
        data = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        return [f"mcp-config.yaml is not valid YAML: {exc}"]

    if not isinstance(data, dict):
        return ["mcp-config.yaml must be a YAML mapping"]

    if not data.get("mcpServers"):
        return ["mcp-config.yaml: 'mcpServers' section is required"]

    return []


def _check_mcpchecker_tasks(submission_dir: Path) -> list[str]:
    """Validate at least one task file exists in tasks/ directory."""
    tasks_dir = submission_dir / "tasks"
    if not tasks_dir.is_dir():
        return ["tasks/ directory is required for MCPChecker evaluation"]

    task_files = list(tasks_dir.rglob("*.yaml")) + list(tasks_dir.rglob("*.yml"))
    if not task_files:
        return ["tasks/ must contain at least one .yaml task file"]

    errors: list[str] = []
    for task_file in task_files:
        try:
            data = yaml.safe_load(task_file.read_text())
        except yaml.YAMLError as exc:
            errors.append(f"{task_file.relative_to(submission_dir)}: invalid YAML: {exc}")
            continue

        if not isinstance(data, dict):
            errors.append(f"{task_file.relative_to(submission_dir)}: must be a YAML mapping")
            continue

        kind = data.get("kind")
        if kind != "Task":
            errors.append(f"{task_file.relative_to(submission_dir)}: 'kind' must be 'Task'")

        spec = data.get("spec", {})
        if not spec.get("prompt"):
            errors.append(f"{task_file.relative_to(submission_dir)}: 'spec.prompt' is required")

    return errors


# ---------------------------------------------------------------------------
# AEH-specific checks
# ---------------------------------------------------------------------------


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


def _check_openclaw_runner(eval_path: Path) -> list[str]:
    """Require runner.type=openclaw for the OpenShell OpenClaw engine."""
    if not eval_path.is_file():
        return []
    try:
        data = yaml.safe_load(eval_path.read_text()) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    runner = data.get("runner") or {}
    rtype = runner.get("type") if isinstance(runner, dict) else None
    if rtype != "openclaw":
        return [
            f"{eval_path.name}: runner.type must be 'openclaw' for "
            f"eval_engine=aeh_openshell_openclaw (got {rtype!r})"
        ]
    return []


def _check_aeh_eval_yaml(submission_dir: Path) -> list[str]:
    """Validate eval.yaml exists and has valid AEH structure (single mode)."""
    return _check_aeh_eval_yaml_file(submission_dir / "eval.yaml")


def _check_aeh_plugin_dirs(submission_dir: Path, eval_path: Path) -> list[str]:
    """If eval.yaml lists plugin_dirs, each must exist and contain a SKILL.md."""
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
    for rel in plugin_dirs:
        root = submission_dir / str(rel)
        if not root.is_dir():
            errors.append(
                f"{eval_path.name}: plugin_dirs entry '{rel}/' is missing "
                f"(required for Claude slash-skill /{skill_name or '…'})"
            )
            continue
        has_top = (root / "SKILL.md").is_file()
        has_nested = any((child / "SKILL.md").is_file() for child in root.iterdir() if child.is_dir())
        if not has_top and not has_nested:
            errors.append(
                f"{eval_path.name}: plugin_dirs '{rel}/' must contain SKILL.md (flat or nested skills/<name>/SKILL.md)"
            )
        elif skill_name and not has_top:
            named = root / str(skill_name) / "SKILL.md"
            if not named.is_file():
                errors.append(
                    f"{eval_path.name}: skill '{skill_name}' requires "
                    f"{rel}/{skill_name}/SKILL.md for /{skill_name} slash command"
                )
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


def _check_aeh_pairwise_contract(eval_path: Path) -> list[str]:
    """Pairwise mode requires outputs: and a pairwise LLM judge for score.py."""
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
    if isinstance(judges, list):
        for judge in judges:
            if not isinstance(judge, dict):
                continue
            name = str(judge.get("name") or "").lower()
            jtype = str(judge.get("type") or "").lower()
            if name == "pairwise" or (jtype == "llm" and "pairwise" in name):
                has_pairwise = True
                break
            if jtype == "llm" and (judge.get("prompt") or judge.get("prompt_file")):
                # Accept any LLM judge as fallback (score.py does too)
                has_pairwise = True
                break
    elif isinstance(judges, dict):
        has_pairwise = "pairwise" in judges or any(
            isinstance(v, dict) and (v.get("type") == "llm" or v.get("prompt")) for v in judges.values()
        )
    if not has_pairwise:
        errors.append(
            f"{filename}: pairwise mode requires a 'pairwise' LLM judge "
            "(or another LLM judge with prompt) for score.py pairwise"
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

        if not treatment_path.is_file():
            errors.append(f"AEH pairwise: missing {treatment_config}")
        else:
            errors.extend(_check_aeh_eval_yaml_file(treatment_path))
            errors.extend(_check_aeh_plugin_dirs(submission_dir, treatment_path))
            errors.extend(_check_aeh_tool_interception(submission_dir, treatment_path))
            # Treatment config drives score.py pairwise — enforce contract there
            errors.extend(_check_aeh_pairwise_contract(treatment_path))
    else:
        errors.extend(_check_aeh_eval_yaml(submission_dir))
        errors.extend(_check_aeh_plugin_dirs(submission_dir, submission_dir / "eval.yaml"))
        errors.extend(_check_aeh_tool_interception(submission_dir, submission_dir / "eval.yaml"))

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


# ---------------------------------------------------------------------------
# Main validation
# ---------------------------------------------------------------------------


def validate_submission(
    submission_dir: Path,
    eval_engine: EvalEngine = EvalEngine.HARBOR,
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

    run_harbor = eval_engine in (EvalEngine.HARBOR, EvalEngine.BOTH)
    run_ase = eval_engine in (EvalEngine.ASE, EvalEngine.BOTH)
    run_mcpchecker = eval_engine == EvalEngine.MCPCHECKER
    run_a2a = eval_engine == EvalEngine.A2A
    run_aeh = eval_engine in AEH_FAMILY

    # Common: metadata.yaml is always required
    metadata_errors, metadata = _check_metadata_yaml(submission_dir)
    errors.extend(metadata_errors)

    # MCPChecker, A2A, and AEH have their own structure - skip skills/ check
    if not run_mcpchecker and not run_a2a and not run_aeh:
        errors.extend(_check_skills_dir(submission_dir))

    if run_harbor:
        errors.extend(_check_instruction_md(submission_dir))
        errors.extend(_check_py_compiles(submission_dir / "tests" / "test_outputs.py"))
        llm_judge = submission_dir / "tests" / "llm_judge.py"
        if llm_judge.is_file():
            errors.extend(_check_py_compiles(llm_judge))
        errors.extend(_check_edge_cases(submission_dir))

    if run_ase:
        errors.extend(_check_skill_md_frontmatter(submission_dir))
        errors.extend(_check_evals_json(submission_dir))

    if run_mcpchecker:
        errors.extend(_check_mcpchecker_eval_yaml(submission_dir))
        errors.extend(_check_mcpchecker_mcp_config(submission_dir))
        errors.extend(_check_mcpchecker_tasks(submission_dir))
        # Validate mcp.credentials_secret is provided
        if metadata and not metadata.mcp:
            errors.append("mcp.credentials_secret is required for MCPChecker evaluation")
        elif metadata and metadata.mcp and not metadata.mcp.credentials_secret:
            errors.append("mcp.credentials_secret must not be empty")

    if run_a2a:
        # A2A requires either instruction.md or task.toml
        has_instruction = (submission_dir / "instruction.md").is_file()
        has_task_toml = (submission_dir / "task.toml").is_file()
        has_tasks_dir = (submission_dir / "tasks").is_dir()
        if not has_instruction and not has_task_toml and not has_tasks_dir:
            errors.append("A2A evaluation requires instruction.md, task.toml, or tasks/ directory")

    if run_aeh:
        errors.extend(
            _check_aeh_structure(
                submission_dir,
                aeh_mode=aeh_mode,
                control_config=aeh_control_config,
                treatment_config=aeh_treatment_config,
            )
        )
        if eval_engine == EvalEngine.AEH_OPENSHELL_OPENCLAW:
            if aeh_mode == "pairwise":
                errors.extend(_check_openclaw_runner(submission_dir / aeh_treatment_config))
            else:
                errors.extend(_check_openclaw_runner(submission_dir / "eval.yaml"))

    # Common: supportive/ size check (skip for mcpchecker, a2a, and aeh)
    if not run_mcpchecker and not run_a2a and not run_aeh:
        errors.extend(_check_supportive_size(submission_dir))

    if errors:
        logger.warning("Validation failed with %d error(s)", len(errors))
    else:
        logger.info("Validation passed")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a submission directory")
    parser.add_argument("submission_dir", type=Path, help="Path to the submission directory")
    parser.add_argument(
        "--eval-engine",
        type=str,
        choices=list(EVAL_ENGINE_CLI_CHOICES),
        default="harbor",
        help="Evaluation engine (controls which checks run)",
    )
    parser.add_argument(
        "--aeh-mode",
        type=str,
        choices=["single", "pairwise"],
        default="single",
        help="AEH evaluation mode (single or pairwise)",
    )
    parser.add_argument(
        "--aeh-control-config",
        type=str,
        default="eval-control.yaml",
        help="Control variant eval.yaml filename for AEH pairwise mode",
    )
    parser.add_argument(
        "--aeh-treatment-config",
        type=str,
        default="eval-treatment.yaml",
        help="Treatment variant eval.yaml filename for AEH pairwise mode",
    )
    args = parser.parse_args(argv)

    submission_dir: Path = args.submission_dir
    if not submission_dir.is_dir():
        result = {"valid": False, "errors": [f"Not a directory: {submission_dir}"]}
        print(json.dumps(result, indent=2))
        return 1

    engine = EvalEngine(args.eval_engine)
    errors = validate_submission(
        submission_dir,
        eval_engine=engine,
        aeh_mode=args.aeh_mode,
        aeh_control_config=args.aeh_control_config,
        aeh_treatment_config=args.aeh_treatment_config,
    )
    result = {"valid": len(errors) == 0, "errors": errors}
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
