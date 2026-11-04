"""Installed command-line interface for skill validation and AEH evaluation."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from contextlib import redirect_stdout
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pydantic import ValidationError

from abevalflow.config.runtime import ConfigurationError, resolve_config
from abevalflow.execution.runtime import RuntimeUnavailable


def workflow_options(parser: argparse.ArgumentParser) -> None:
    workload = parser.add_argument_group("Kubernetes workload")
    scanners = parser.add_argument_group("Tier 1 scanners")
    models = parser.add_argument_group("Model endpoints and review")
    storage = parser.add_argument_group("Persistence and publication")
    credentials = parser.add_argument_group("Credentials")
    parser.add_argument("skill", nargs="?", help="Directory containing SKILL.md; defaults to ABEVAL_SKILL or cwd")
    parser.add_argument("--skill-id", help="Catalog identifier <pack>/<skill>; alternative to the skill path")
    parser.add_argument("--submission", type=Path, help="AEH submission directory; otherwise discovered in the catalog")
    parser.add_argument("--tiers", help="1, 2, or 1,2 (default: 1)")
    parser.add_argument("--source", type=Path, help="Source root containing the skill and submission")
    parser.add_argument("--output", type=Path, help="New run directory (default: .abeval/runs/<run-id>)")
    parser.add_argument("--run-id", help="Stable filesystem-safe run identifier")
    parser.add_argument("--revision", help="Evaluated source revision")
    parser.add_argument("--plugin-name", help="Owning plugin; otherwise inferred from the catalog path or metadata")
    parser.add_argument("--built-image-ref", help="Evaluated content image as repository@sha256:digest")
    models.add_argument(
        "--submission-review",
        choices=("auto", "on", "off"),
        help="Honor metadata, enable, or disable advisory submission review (default: auto)",
    )
    models.add_argument(
        "--skill-review",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable advisory LLM review of SKILL.md",
    )
    parser.add_argument(
        "--continue-on-tier1-failure",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow Tier 2 after blocking Tier 1 findings; preserve the failing verdict",
    )
    storage.add_argument("--persist", help="Comma-separated destinations: postgres,s3,compass; disabled by default")
    storage.add_argument(
        "--persistence-required",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Fail the command when selected publication fails (default: enabled)",
    )
    workload.add_argument("--backend", choices=("kubernetes",), help="Remote workload backend")
    workload.add_argument("--kubeconfig", type=Path, help="Remote cluster kubeconfig (also KUBECONFIG)")
    workload.add_argument("--kube-context", help="Explicit kubeconfig context")
    workload.add_argument("--namespace", help="Namespace for trial pods (default: default)")
    workload.add_argument("--credentials-secret", help="Workload model credential Secret (default: llm-credentials)")
    workload.add_argument("--trial-image", help="Trial runtime image, preferably pinned by digest")
    workload.add_argument("--model", help="Override the submission's execution model")
    workload.add_argument("--judge-model", help="Override the submission's judge model")
    workload.add_argument("--score-script", type=Path, help="Upstream AEH score.py for pairwise aggregation")
    workload.add_argument("--aeh-root", type=Path, help="Complete upstream AEH installation (ABEVAL_AEH_ROOT)")
    workload.add_argument("--aeh-python", type=Path, help="AEH Python interpreter (ABEVAL_AEH_PYTHON)")
    scanners.add_argument(
        "--scanner-runtime",
        choices=("local", "podman", "docker"),
        help="Run installed scanners locally or in containers (default: local)",
    )
    scanners.add_argument("--security-image", help="Cisco scanner image for container execution")
    scanners.add_argument("--quality-image", help="Quality scanner image for container execution")
    scanners.add_argument("--security-command", help="Override the included skill-scanner executable")
    scanners.add_argument("--quality-command", help="Override the included harness-eval executable")
    parser.add_argument("--command-timeout", type=int, help="External command timeout in seconds (default: 7200)")
    parser.add_argument("--timeout-retries", type=int, help="Retry a timed-out AEH arm 0-5 times (default: 1)")
    workload.add_argument(
        "--n-concurrent",
        type=int,
        help="Maximum concurrent AEH trial cases (default: 1; also ABEVAL_N_CONCURRENT)",
    )
    models.add_argument("--llm-base-url", help="Submission/skill review API endpoint")
    models.add_argument("--llm-model", help="Submission/skill review model")
    models.add_argument("--agent-base-url", help="Agent API endpoint passed to AEH")
    models.add_argument("--judge-base-url", help="Suite-level judge API endpoint")
    models.add_argument(
        "--insecure-ssl",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Skip certificate verification for evaluation HTTP clients, Compass, "
            "and the database TLS route (default: false)"
        ),
    )
    for secret in ("llm-api-key", "agent-api-key", "judge-api-key", "s3-access-key", "s3-secret-key", "compass-token"):
        credentials.add_argument(f"--{secret}", help=f"Credential; prefer ABEVAL_{secret.upper().replace('-', '_')}")
    storage.add_argument("--database-url", help="SQLAlchemy database URL; prefer ABEVAL_DATABASE_URL for credentials")
    storage.add_argument("--database-tls-route-host", help="TLS passthrough route for a loopback PostgreSQL URL")
    storage.add_argument("--s3-endpoint", help="S3-compatible endpoint URL")
    storage.add_argument("--s3-bucket", help="S3 bucket (default: ab-eval-reports)")
    storage.add_argument("--s3-verify-tls", action=argparse.BooleanOptionalAction, default=None)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="abeval", description="Validate skills and run AEH trials on Kubernetes.")
    command.add_argument("--version", action="version", version=package_version())
    command.add_argument("--verbose", action="store_true", help="Enable diagnostic logging")
    subcommands = command.add_subparsers(dest="command", required=True)
    for name, help_text in {
        "run": "Execute selected tiers, report results, and publish when requested",
        "batch": "Evaluate explicit catalog skill IDs sequentially with per-skill evidence",
        "plan": "Show resolved non-secret configuration and operations without executing them",
        "doctor": "Check dependencies and remote workload readiness without running models",
        "init": "Initialize a run manifest for independent container phases",
    }.items():
        child = subcommands.add_parser(name, help=help_text)
        workflow_options(child)
        if name == "batch":
            child.add_argument(
                "--skill-ids", nargs="*", required=True, help="Ordered unique <pack>/<skill> identifiers"
            )
        if name == "doctor":
            child.add_argument(
                "--offline", action="store_true", help="Check local prerequisites without contacting a cluster"
            )
    validate = subcommands.add_parser("validate", help="Validate one skill or AEH submission without model calls")
    validate.add_argument("kind", choices=("skill", "submission"))
    validate.add_argument("path", type=Path)
    validate.add_argument("--output", type=Path, help="Optional JSON report path")
    package = subcommands.add_parser("batch-package", help="Archive existing batch reports for OCI attachment")
    package.add_argument("--batch-dir", type=Path, required=True)
    package.add_argument("--component-manifest", type=Path)
    package.add_argument("--image-url", default="")
    package.add_argument("--image-digest", default="")
    export = subcommands.add_parser(
        "batch-results", help="Validate batch evidence, emit Konflux results, and apply gates"
    )
    export.add_argument("--batch-dir", type=Path, required=True)
    export.add_argument("--skill-ids", nargs="*", required=True)
    export.add_argument("--revision", required=True)
    export.add_argument("--tiers", choices=("1", "2", "1,2"), required=True)
    export.add_argument("--persist", default="")
    export.add_argument("--results-dir", type=Path, required=True)
    export.add_argument("--image-url", default="")
    export.add_argument("--image-digest", default="")
    export.add_argument("--artifact-file", type=Path)
    export.add_argument("--fail-on-non-success", choices=("true", "false"), default="true")
    for name, help_text in {
        "report": "Regenerate local reports from existing run evidence",
        "publish": "Publish an existing run without repeating checks or trials",
        "phase": "Execute one selected phase from an initialized run",
    }.items():
        child = subcommands.add_parser(name, help=help_text)
        child.add_argument("--run-dir", type=Path, required=True)
        if name == "phase":
            child.add_argument(
                "name",
                choices=(
                    "prepare",
                    "structure",
                    "security",
                    "quality",
                    "skill-review",
                    "submission",
                    "submission-review",
                    "evaluate",
                    "report",
                ),
            )
        if name == "phase":
            child.add_argument("--if-selected", action="store_true", help="Skip phases absent from the saved plan")
        if name == "publish":
            child.add_argument("--persist", help="Override selected publication destinations")
            child.add_argument("--database-url", help="Database URL; prefer ABEVAL_DATABASE_URL for credentials")
            child.add_argument("--s3-endpoint")
            child.add_argument("--s3-bucket")
            child.add_argument("--persistence-required", action=argparse.BooleanOptionalAction, default=None)
    runtime = subcommands.add_parser("runtime", help="Install or check the external AEH runtime")
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)
    install = runtime_commands.add_parser("install", help="Install pinned AEH in an isolated environment")
    install.add_argument("--directory", type=Path, help="New installation directory (default: user cache)")
    install.add_argument("--python", type=Path, help="Python >=3.12 interpreter for the new environment")
    check = runtime_commands.add_parser("check", help="Check the complete AEH installation")
    check.add_argument("--aeh-root", type=Path)
    check.add_argument("--aeh-python", type=Path)
    return command


def package_version() -> str:
    try:
        return version("abevalflow")
    except PackageNotFoundError:
        return "0.4.1"


def emit(value) -> None:
    print(json.dumps(value, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    """Return 0 for success, 1 for policy failure, 2 for usage, or 3 for runtime failure."""
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    from abevalflow.observability.otel import setup_tracer_provider, shutdown_tracer_provider

    tracing = args.command in {"run", "batch", "phase", "report", "publish"}
    previous_term = None
    if tracing and threading.current_thread() is threading.main_thread():

        def interrupted(signum, frame):
            raise KeyboardInterrupt

        previous_term = signal.signal(signal.SIGTERM, interrupted)
    try:
        if tracing:
            setup_tracer_provider()
        if args.command == "batch":
            from abevalflow.workflow.batch import run_batch

            with redirect_stdout(sys.stderr):
                directory, result = run_batch(args.skill_ids, vars(args))
            emit({"batch_id": result.batch_id, "batch_dir": str(directory), "exit_code": result.exit_code})
            return result.exit_code
        if args.command == "batch-package":
            from abevalflow.results.konflux import component_evidence, package_batch

            if args.component_manifest:
                component_evidence(args.batch_dir, args.component_manifest, args.image_url, args.image_digest)
            package_batch(args.batch_dir)
            return 0
        if args.command == "batch-results":
            from abevalflow.results.konflux import emit_results

            return emit_results(
                args.batch_dir,
                args.skill_ids,
                args.revision,
                {int(t) for t in args.tiers.split(",")},
                {v.strip() for v in args.persist.split(",") if v.strip()},
                args.results_dir,
                args.image_url,
                args.image_digest,
                args.artifact_file,
                args.fail_on_non_success == "true",
            )
        if args.command == "runtime":
            from abevalflow.execution.runtime import AEH_REVISION, AEH_VERSION, install_runtime, resolve_runtime

            if args.runtime_command == "install":
                runtime = install_runtime(args.directory, args.python)
            else:
                runtime = resolve_runtime(args.aeh_root, args.aeh_python)
            checks = [
                ["-m", "agent_eval.harbor.run", "--help"],
                [str(runtime.task_generator), "--help"],
                [str(runtime.script("score.py")), "--help"],
                [str(runtime.script("report.py")), "--help"],
            ]
            ready = all(runtime.run(command, environment=None, timeout=60) == 0 for command in checks)
            emit(
                {
                    "ready": ready,
                    "root": runtime.root,
                    "python": runtime.python,
                    "supported_version": AEH_VERSION,
                    "supported_revision": AEH_REVISION,
                }
            )
            return 0 if ready else 3
        if args.command == "validate":
            from abevalflow.results.run import write_json

            if args.kind == "skill":
                from abevalflow.skill_structure import validate_skill_structure

                result = validate_skill_structure(args.path)
                payload = result.model_dump(mode="json")
                valid = result.passed
            else:
                from abevalflow.config.runtime import load_metadata
                from abevalflow.validation.submission import validate_submission

                metadata = load_metadata(args.path)
                errors = validate_submission(
                    args.path,
                    aeh_mode=metadata.aeh_mode.value,
                    aeh_control_config=metadata.aeh_control_config,
                    aeh_treatment_config=metadata.aeh_treatment_config,
                )
                valid = not errors
                payload = {"valid": valid, "errors": errors}
            if args.output:
                write_json(args.output, payload)
            emit(payload)
            return 0 if valid else 1
        if args.command in {"report", "publish", "phase"}:
            from abevalflow.workflow.operations import Operations
            from abevalflow.workflow.service import execute_step, load_run, phase, save_run
            from abevalflow.workflow.workspace import run_lock

            if args.command == "phase":
                with redirect_stdout(sys.stderr):
                    result = phase(args.run_dir, args.name, if_selected=args.if_selected)
            else:
                with run_lock(args.run_dir):
                    config, result = load_run(args.run_dir)
                    if args.command == "report":
                        execute_step(config, result, "report", Operations(config))
                    else:
                        from abevalflow.persistence.service import publish

                        if args.persist is not None:
                            destinations = tuple(v.strip() for v in args.persist.split(",") if v.strip())
                            if set(destinations) - {"postgres", "s3", "compass"}:
                                raise ConfigurationError("Persistence destinations: postgres,s3,compass")
                            config.persist = destinations
                        overrides = {
                            key: getattr(args, key)
                            for key in (
                                "database_url",
                                "s3_endpoint",
                                "s3_bucket",
                                "persistence_required",
                            )
                            if getattr(args, key) is not None
                        }
                        config = type(config).model_validate(
                            {
                                **config.model_dump(),
                                "database_url": config.database_url,
                                **overrides,
                                **{
                                    key: getattr(config, key)
                                    for key in (
                                        "llm_api_key",
                                        "agent_api_key",
                                        "judge_api_key",
                                        "s3_access_key",
                                        "s3_secret_key",
                                        "compass_token",
                                    )
                                },
                            }
                        )
                        result.publication = publish(config, result)
                        result.config["persist"] = list(config.persist)
                        save_run(config, result)
            emit(result.model_dump(mode="json"))
            return result.exit_code
        config = resolve_config(vars(args))
        if args.command == "plan":
            from abevalflow.workflow.planning import build_plan

            emit(build_plan(config))
            return 0
        if args.command == "doctor":
            from abevalflow.workflow.readiness import check_readiness

            checks = check_readiness(config, cluster=not args.offline)
            emit({"ready": all(c["ready"] for c in checks), "checks": checks})
            return 0 if all(c["ready"] for c in checks) else 3
        if args.command == "init":
            from abevalflow.workflow.service import create_result, save_run
            from abevalflow.workflow.workspace import run_lock

            with run_lock(config.output, create=True):
                save_run(config, create_result(config))
            emit({"run_dir": str(config.output)})
            return 0
        from abevalflow.workflow.service import run

        with redirect_stdout(sys.stderr):
            result = run(config)
        emit(
            {
                "run_id": result.run_id,
                "run_dir": str(config.output),
                "exit_code": result.exit_code,
                "steps": [{"name": s.name, "status": s.status, "verdict": s.verdict} for s in result.steps],
            }
        )
        return result.exit_code
    except KeyboardInterrupt:
        print("Evaluation interrupted; partial evidence retained", file=sys.stderr)
        return 130
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except RuntimeUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        print(f"Invalid configuration fields: {fields}", file=sys.stderr)
        return 2
    except FileExistsError:
        if args.command == "runtime":
            print(
                "Runtime installation directory already exists; use 'runtime check' or a new --directory",
                file=sys.stderr,
            )
        else:
            print("Run directory already exists; choose a new --output or --run-id", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Unable to execute command ({type(exc).__name__})", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Runtime failure ({type(exc).__name__})", file=sys.stderr)
        return 3
    finally:
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
        if tracing:
            shutdown_tracer_provider()
