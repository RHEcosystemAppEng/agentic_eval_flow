"""Prepare Harbor task packages for skill evaluation on OpenShift.

Preparation stages skill trees, verifier annotations, tool handlers, and per-case MCP
fixtures through Harbor task inputs and native MCP server declarations. Generated interception hooks and
agent settings are preserved. Model overrides and reward configuration are
applied to the bundled verifier inputs.

Pack-level mcps.json files use stable workspace-relative fixture paths. Each
task receives its own fixture at those paths. Pairwise control configurations
can reference generated MCP-only packs to retain tool access without skills.
"""

from __future__ import annotations

import json
import logging
import shutil
import tomllib
from pathlib import Path

import tomli_w
import yaml

from abevalflow.execution.mcp import fixture_server_names

logger = logging.getLogger(__name__)

# Artifacts produced by agent_eval.harbor.tasks._generate_tool_interception
_INTERCEPTION_MARKERS = (
    "hooks/tools.py",
    "tool_handlers.yaml",
    ".claude/settings.json",
)

# Shared, plugin-level schema: submissions/<plugin>/mcp-servers/<server>/
# schema.json -- a sibling of every skill's own submission dir (NOT inside
# any one skill's submission_root, and NOT the real <plugin>/ source tree
# that runner.plugin_dirs points at). One schema.json per real mcps.json
# server name, reused by every case of every skill submitted under that
# plugin.
_MCP_SERVERS_DIRNAME = "mcp-servers"
# Per-case fixtures: cases/<case_id>/mock-mcps/<server>/fixture.json.
_MOCK_MCPS_DIRNAME = "mock-mcps"
# Written into a task's environment/mock-mcps/<server>/fixture.json when
# that specific case has no fixture for a server some *other* case in this
# skill mocks -- keeps the mock server starting cleanly (every tool call
# falls through to the schema's outputExample, upstream's documented
# fallback-when-exhausted behavior) instead of failing to launch over a
# missing --fixtures file.
_EMPTY_FIXTURE_STUB = '{\n  "sequence": []\n}\n'


def enrich_harbor_tasks(
    tasks_dir: Path,
    *,
    config_path: Path,
    skill_model: str | None = None,
    judge_model: str | None = None,
) -> int:
    """Enrich every Harbor task package under *tasks_dir*.

    Optional *skill_model* / *judge_model* rewrite ``models`` in each task's
    bundled ``tests/eval.yaml`` (in-pod judges read this file). When the
    submission has no ``reward:`` section, inject a weighted formula over
    declared judges so all-null judge values yield 0.0 instead of AEH's
    default 1.0.

    Returns the number of task packages updated.
    """
    config_path = Path(config_path)
    tasks_dir = Path(tasks_dir)
    if not tasks_dir.is_dir():
        return 0

    raw = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(raw, dict):
        raw = {}
    submission_root = config_path.parent
    dataset_rel = ""
    dataset = raw.get("dataset") or {}
    if isinstance(dataset, dict):
        dataset_rel = str(dataset.get("path") or "").strip()

    plugin_dirs: list[str] = []
    runner = raw.get("runner") or {}
    if isinstance(runner, dict):
        plugin_dirs = list(runner.get("plugin_dirs") or [])

    wants_interception = _config_wants_tool_interception(raw)
    reward_inject = _reward_to_inject(raw)

    # submissions/<plugin>/mcp-servers/ -- a sibling of submission_root
    # itself (submissions/<plugin>/<skill>/), shared across every skill
    # submitted under that plugin. See module docstring + _MCP_SERVERS_DIRNAME.
    mcp_schema_root = submission_root.parent / _MCP_SERVERS_DIRNAME
    cases_root = submission_root / (dataset_rel or "cases")
    mock_server_names = sorted(fixture_server_names(cases_root))

    updated = 0
    for task_dir in sorted(p for p in tasks_dir.iterdir() if p.is_dir()):
        if not (task_dir / "task.toml").is_file():
            continue
        _enrich_one_task(
            task_dir,
            case_id=task_dir.name,
            submission_root=submission_root,
            dataset_rel=dataset_rel,
            plugin_dirs=plugin_dirs,
            config_path=config_path,
            wants_interception=wants_interception,
            skill_model=skill_model,
            judge_model=judge_model,
            reward_inject=reward_inject,
            mcp_schema_root=mcp_schema_root,
            mock_server_names=mock_server_names,
        )
        updated += 1
    return updated


def _reward_to_inject(raw: dict) -> dict | None:
    """Build a weighted reward block when the submission omits ``reward:``."""
    if isinstance(raw.get("reward"), dict) and raw["reward"]:
        return None
    judges = raw.get("judges") or []
    if not isinstance(judges, list):
        return None
    names = [str(j["name"]) for j in judges if isinstance(j, dict) and j.get("name")]
    if not names:
        return None
    return {
        "formula": "weighted",
        # AEH RewardConfig defaults to [1, 5]; LLM judges in this pipeline
        # emit [0, 1], so keep the range aligned or scores of 1.0 become 0.0.
        "score_range": [0, 1],
        "weights": {name: 1.0 for name in names},
    }


def _config_wants_tool_interception(raw: dict) -> bool:
    inputs = raw.get("inputs") or {}
    if isinstance(inputs, dict):
        tools = inputs.get("tools") or []
        if isinstance(tools, list) and tools:
            return True
    permissions = raw.get("permissions") or {}
    if isinstance(permissions, dict) and (permissions.get("deny") or permissions.get("allow")):
        return True
    return False


def _enrich_one_task(
    task_dir: Path,
    *,
    case_id: str,
    submission_root: Path,
    dataset_rel: str,
    plugin_dirs: list[str],
    config_path: Path,
    wants_interception: bool,
    skill_model: str | None = None,
    judge_model: str | None = None,
    reward_inject: dict | None = None,
    mcp_schema_root: Path | None = None,
    mock_server_names: list[str] | None = None,
) -> None:
    env_dir = task_dir / "environment"
    env_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    # Snapshot interception artifacts before any destructive copies
    preserved = _snapshot_interception(env_dir)

    # Keep judge ground truth in the verifier's inputs, outside the agent workspace.
    annotations_src = _find_annotations(submission_root, dataset_rel, case_id)
    task_config = tomllib.loads((task_dir / "task.toml").read_text())
    workdir = task_config.get("environment", {}).get("workdir", "/workspace")
    if annotations_src is not None:
        annotation_dir = tests_dir / "cases" / Path(workdir).name
        annotation_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(annotations_src, annotation_dir / "annotations.yaml")
        annotations = yaml.safe_load(annotations_src.read_text()) or {}
        for value in annotations.values() if isinstance(annotations, dict) else ():
            if (
                isinstance(value, str)
                and len(value) <= 255
                and value == value.strip()
                and not any(character in value for character in ("\n", "\r", "\x00"))
            ):
                try:
                    reference = (annotations_src.parent / value).resolve()
                    is_reference = reference.is_relative_to(annotations_src.parent.resolve()) and reference.is_file()
                except (ValueError, OSError):
                    continue
                if is_reference:
                    destination = annotation_dir / value
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(reference, destination)

    # Resolve verifier input configuration.
    bundled = tests_dir / "eval.yaml"
    if bundled.is_file():
        cfg = yaml.safe_load(bundled.read_text()) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        if isinstance(cfg.get("dataset"), dict):
            cfg["dataset"]["path"] = "cases"
        elif "dataset" not in cfg:
            cfg["dataset"] = {"path": "cases"}
        if skill_model or judge_model:
            models = cfg.get("models")
            if not isinstance(models, dict):
                models = {}
                cfg["models"] = models
            if skill_model:
                models["skill"] = skill_model
            if judge_model:
                models["judge"] = judge_model
        if reward_inject is not None and not (isinstance(cfg.get("reward"), dict) and cfg["reward"]):
            # Only Agentic Eval Flow-injected rewards get score_range [0, 1]. Do not
            # rewrite submission-authored weighted rewards that omit score_range
            # (AEH default [1, 5] may be intentional for Likert judges).
            cfg["reward"] = reward_inject
        bundled.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))

    # 4) Copy plugin skill trees + point Harbor at them
    #    Only replace environment/skills/ — never wipe hooks/ or .claude/
    copied_skills = False
    for rel in plugin_dirs:
        root = (submission_root / rel).resolve()
        if not root.is_dir():
            continue
        src = _resolve_skill_source(root)
        if src is None:
            continue
        dest = env_dir / "skills"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        copied_skills = True
        break  # Harbor expects a single skills_dir root

    if copied_skills:
        task_config.setdefault("environment", {})["skills_dir"] = f"{workdir}/skills"

    # 5) Restore / ensure tool interception + MCP hook artifacts
    _restore_interception(env_dir, preserved)
    _ensure_submission_tool_handlers(env_dir, config_path)
    if wants_interception and not _has_interception(env_dir):
        logger.warning(
            "Task %s: eval.yaml declares inputs.tools/permissions but environment/ "
            "has no hooks/tools.py or tool_handlers.yaml — Harbor will not intercept tools",
            task_dir.name,
        )

    # 6) Mock MCP: shared schema (plugin-level) + this case's own fixture.
    #    See module docstring -- mcps.json itself is swapped once for the
    #    whole job (aeh-skill-eval.yaml's swap-mock-mcp), to a FIXED stdio
    #    command referencing these same workspace-relative paths; placing
    #    different fixture content per task here is what makes each case's
    #    mock behave differently despite that one shared command.
    if mock_server_names:
        _stage_mock_mcp_files(
            env_dir,
            case_dir=submission_root / (dataset_rel or "cases") / case_id,
            mcp_schema_root=mcp_schema_root,
            server_names=mock_server_names,
        )
    servers = _native_mcp_servers(submission_root, plugin_dirs, mock_server_names)
    task_config.setdefault("environment", {})["mcp_servers"] = servers
    (task_dir / "task.toml").write_text(tomli_w.dumps(task_config))


def _stage_mock_mcp_files(
    env_dir: Path,
    *,
    case_dir: Path,
    mcp_schema_root: Path | None,
    server_names: list[str],
) -> None:
    """Copy shared schema.json + this case's fixture.json per mocked server.

    Raises if a server this skill mocks has no plugin-level schema.json --
    a case that specifies a fixture needs its contract, and a silently
    missing schema would otherwise surface only as an opaque mock-server
    startup failure inside a trial pod much later.
    """
    for server in server_names:
        if mcp_schema_root is None:
            raise FileNotFoundError(
                f"mock-mcps references server '{server}' but no mcp-servers/ root could be resolved for this submission"
            )
        schema_src = mcp_schema_root / server / "schema.json"
        if not schema_src.is_file():
            raise FileNotFoundError(
                f"Case mocks server '{server}' but {schema_src} is missing — "
                "every mocked server needs a shared schema.json alongside the "
                "plugin's other skill submissions (submissions/<plugin>/"
                f"{_MCP_SERVERS_DIRNAME}/{server}/schema.json)"
            )
        schema_dest = env_dir / _MCP_SERVERS_DIRNAME / server / "schema.json"
        schema_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(schema_src, schema_dest)

        fixture_dest = env_dir / _MOCK_MCPS_DIRNAME / server / "fixture.json"
        fixture_dest.parent.mkdir(parents=True, exist_ok=True)
        fixture_src = case_dir / _MOCK_MCPS_DIRNAME / server / "fixture.json"
        if fixture_src.is_file():
            shutil.copy2(fixture_src, fixture_dest)
        else:
            # This case doesn't exercise this server -- stub it so the mock
            # still starts; every call falls through to outputExample.
            fixture_dest.write_text(_EMPTY_FIXTURE_STUB)


def _snapshot_interception(env_dir: Path) -> dict[str, bytes]:
    """Read interception files so enrichment cannot accidentally drop them."""
    snapped: dict[str, bytes] = {}
    for rel in _INTERCEPTION_MARKERS:
        path = env_dir / rel
        if path.is_file():
            snapped[rel] = path.read_bytes()
    return snapped


def _restore_interception(env_dir: Path, preserved: dict[str, bytes]) -> None:
    for rel, data in preserved.items():
        dest = env_dir / rel
        if dest.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def _ensure_submission_tool_handlers(env_dir: Path, config_path: Path) -> None:
    """Copy submission-level tool_handlers.yaml into environment/ when missing."""
    dest = env_dir / "tool_handlers.yaml"
    if dest.is_file():
        return
    src = config_path.parent / "tool_handlers.yaml"
    if src.is_file():
        shutil.copy2(src, dest)


def _has_interception(env_dir: Path) -> bool:
    return (env_dir / "hooks" / "tools.py").is_file() or (env_dir / "tool_handlers.yaml").is_file()


def _find_annotations(submission_root: Path, dataset_rel: str, case_id: str) -> Path | None:
    candidates: list[Path] = []
    if dataset_rel:
        candidates.append(submission_root / dataset_rel / case_id / "annotations.yaml")
    candidates.append(submission_root / "cases" / case_id / "annotations.yaml")
    for path in candidates:
        if path.is_file():
            return path
    return None


def _resolve_skill_source(root: Path) -> Path | None:
    """Resolve the directory that should be copied to ``environment/skills``.

    Mirrors the submission validator's ``_check_aeh_plugin_dirs`` three
    accepted ``runner.plugin_dirs`` conventions so a submission that passes
    validation also gets its skill(s) actually staged for Harbor:

    - ``root/SKILL.md``               (flat: plugin_dirs entry IS one skill)
    - ``root/<name>/SKILL.md``        (nested: entry's children are skills)
    - ``root/skills/<name>/SKILL.md`` (pack root: e.g. ``plugin_dirs:
      ["../../../rh-sre"]`` — the real plugin source tree, kept at the pack
      root rather than ``root/skills`` so this SAME entry also resolves
      ``mcps.json`` for the mock-MCP swap step. Returns ``root/skills``
      here, not ``root`` itself, so Harbor's ``skills_dir`` scan — which
      expects immediate children to be skill directories — doesn't see an
      extra ``skills/`` level once copied.)

    Returns ``None`` when none of the three match.
    """
    if (root / "SKILL.md").is_file():
        return root
    if any(child.is_dir() and (child / "SKILL.md").is_file() for child in root.iterdir()):
        return root
    skills_sub = root / "skills"
    if skills_sub.is_dir() and any(child.is_dir() and (child / "SKILL.md").is_file() for child in skills_sub.iterdir()):
        return skills_sub
    return None


def _native_mcp_servers(submission_root: Path, plugin_dirs: list[str], mock_server_names: list[str]) -> list[dict]:
    """Register only fixture-backed servers from the staged pack declarations."""
    servers: dict[str, dict] = {}
    for relative in plugin_dirs:
        path = (submission_root / relative).resolve() / "mcps.json"
        if not path.is_file():
            continue
        for name, entry in json.loads(path.read_text()).get("mcpServers", {}).items():
            if name not in mock_server_names:
                continue
            if entry.get("type") != "stdio" or not isinstance(entry.get("command"), str):
                raise ValueError(f"MCP mock {name} must be prepared as a stdio command")
            native = {"name": name, "transport": "stdio", "command": entry["command"], "args": entry.get("args", [])}
            if name in servers and servers[name] != native:
                raise ValueError(f"Conflicting MCP declarations for {name}")
            servers[name] = native
    missing = set(mock_server_names) - servers.keys()
    if missing:
        raise ValueError(f"MCP mocks have no pack declaration: {', '.join(sorted(missing))}")
    return list(servers.values())
