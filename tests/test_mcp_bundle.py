"""Integration tests for MCP eval bundle content and structure."""

import unittest
from pathlib import Path

import yaml


class TestMCPBundleContent(unittest.TestCase):
    """Verify bundle contains all required resources with correct references."""

    def setUp(self):
        """Load bundle components."""
        self.repo_root = Path(__file__).parent.parent

        # Load pipeline
        pipeline_file = self.repo_root / "pipeline" / "pipelines" / "mcp-eval-pipeline.yaml"
        self.assertTrue(pipeline_file.exists(), f"Pipeline file not found: {pipeline_file}")
        with open(pipeline_file) as f:
            self.pipeline = yaml.safe_load(f)

        # Load task files that should be in bundle
        self.tasks_dir = self.repo_root / "pipeline" / "tasks" / "konflux"
        self.task_files = {
            "mcp-static-eval": self.tasks_dir / "mcp-static-eval.yaml",
            "mcp-conformance-eval": self.tasks_dir / "mcp-conformance-eval.yaml",
            "mcp-behavioral-eval": self.tasks_dir / "mcp-behavioral-eval.yaml",
            "mcp-compass-publish": self.tasks_dir / "mcp-compass-publish.yaml",
        }

        self.tasks = {}
        for name, path in self.task_files.items():
            self.assertTrue(path.exists(), f"Task file not found: {path}")
            with open(path) as f:
                self.tasks[name] = yaml.safe_load(f)

    def test_pipeline_kind_and_name(self):
        """Pipeline has correct kind and name."""
        self.assertEqual(self.pipeline["apiVersion"], "tekton.dev/v1")
        self.assertEqual(self.pipeline["kind"], "Pipeline")
        self.assertEqual(self.pipeline["metadata"]["name"], "mcp-eval-pipeline")

    def test_pipeline_has_required_params(self):
        """Pipeline accepts all required parameters."""
        params = {p["name"]: p for p in self.pipeline["spec"]["params"]}

        required_params = {
            "mcp-server-url",
            "submission-name",
            "namespace-name",
            "service-name",
            "service-port",
            "mcp-eval-image",
            "compass-url",
            "compass-dry-run",
            "entity-ref",
        }

        for param in required_params:
            self.assertIn(param, params, f"Required param missing: {param}")

    def test_pipeline_has_workspace(self):
        """Pipeline defines shared workspace."""
        workspaces = self.pipeline["spec"].get("workspaces", [])
        self.assertTrue(len(workspaces) > 0, "Pipeline must have at least one workspace")

        workspace_names = {w["name"] for w in workspaces}
        self.assertIn("workspace", workspace_names, "Pipeline must define 'workspace' workspace")

    def test_pipeline_tasks_use_workspace(self):
        """All pipeline tasks reference the shared workspace."""
        for task in self.pipeline["spec"]["tasks"]:
            task_workspaces = task.get("workspaces", [])
            workspace_names = {w["name"] for w in task_workspaces}
            self.assertIn(
                "source", workspace_names or ["source"], f"Task {task['name']} must reference 'source' workspace"
            )

    def test_all_tasks_exist(self):
        """All tasks referenced by pipeline exist as YAML files."""
        for task_name in self.tasks.keys():
            self.assertIsNotNone(self.tasks[task_name], f"Task {task_name} failed to load from YAML")

    def test_all_tasks_are_tasks(self):
        """All task YAMLs have kind=Task."""
        for task_name, task_spec in self.tasks.items():
            self.assertEqual(
                task_spec["kind"], "Task", f"File {task_name} is not a Task (kind={task_spec.get('kind')})"
            )

    def test_task_metadata_names_match_filenames(self):
        """Task metadata.name matches expected name."""
        expected_names = {
            "mcp-static-eval": "mcp-static-eval",
            "mcp-conformance-eval": "mcp-conformance-eval",
            "mcp-behavioral-eval": "mcp-behavioral-eval",
            "mcp-compass-publish": "mcp-compass-publish",
        }

        for file_name, task_spec in self.tasks.items():
            expected_name = expected_names[file_name]
            actual_name = task_spec["metadata"]["name"]
            self.assertEqual(
                actual_name, expected_name, f"Task file {file_name} has name={actual_name}, expected {expected_name}"
            )

    def test_makefile_has_bundle_target(self):
        """Makefile includes bundle-mcp-eval target."""
        makefile_path = self.repo_root / "pipeline" / "integration" / "Makefile"
        with open(makefile_path) as f:
            makefile = f.read()

        self.assertIn("bundle-mcp-eval:", makefile, "Makefile missing bundle-mcp-eval target")
        self.assertIn("abevalflow-mcp-eval-bundle", makefile, "Makefile bundle-mcp-eval target missing bundle name")

    def test_makefile_includes_all_mcp_tasks(self):
        """Makefile TASKS list includes all 4 MCP tasks."""
        makefile_path = self.repo_root / "pipeline" / "integration" / "Makefile"
        with open(makefile_path) as f:
            makefile = f.read()

        mcp_tasks = [
            "mcp-static-eval",
            "mcp-conformance-eval",
            "mcp-behavioral-eval",
            "mcp-compass-publish",
        ]

        for task in mcp_tasks:
            self.assertIn(task, makefile, f"Makefile TASKS list missing {task}")

    def test_workflow_publishes_mcp_bundle(self):
        """GitHub workflow includes step to push MCP bundle."""
        workflow_path = self.repo_root / ".github" / "workflows" / "push-bundles.yaml"
        with open(workflow_path) as f:
            workflow = yaml.safe_load(f)

        # Find MCP bundle step
        steps = workflow["jobs"]["push-bundles"]["steps"]
        step_names = [s.get("name", "") for s in steps]

        self.assertTrue(
            any("MCP" in name or "mcp" in name for name in step_names), "Workflow missing MCP bundle push step"
        )

    def test_pipeline_tasks_match_phase_registry(self):
        """Pipeline evaluation tasks match the configured phases."""
        from abevalflow.mcp.phase_config import PHASES

        tasks = self.pipeline["spec"]["tasks"]
        actual_names = [task["name"] for task in tasks]

        self.assertEqual(actual_names, list(PHASES))

        # Compass publishing runs in finally, not as an evaluation phase.
        final_tasks = self.pipeline["spec"]["finally"]
        final_names = [task["name"] for task in final_tasks]

        self.assertIn("compass-publish", final_names)

    def test_pipeline_task_order(self):
        """Pipeline dependencies match the phase registry."""
        from abevalflow.mcp.phase_config import PHASES

        tasks = {
            task["name"]: task
            for task in self.pipeline["spec"]["tasks"]
        }

        for phase_name, phase in PHASES.items():
            expected_dependencies = phase.get("depends_on", [])
            actual_dependencies = tasks[phase_name].get("runAfter", [])

            self.assertEqual(
                actual_dependencies,
                expected_dependencies,
                f"Incorrect dependencies for {phase_name}",
            )

        # Publishing is executed after the evaluation tasks.
        final_names = [
            task["name"]
            for task in self.pipeline["spec"]["finally"]
        ]
        self.assertIn("compass-publish", final_names)

    def test_pipeline_task_references(self):
        """All evaluation tasks use the correct Tekton Bundle references."""
        from abevalflow.mcp.phase_config import PHASES

        tasks = {
            task["name"]: task
            for task in self.pipeline["spec"]["tasks"]
        }

        tasks["compass-publish"] = next(
            task
            for task in self.pipeline["spec"]["finally"]
            if task["name"] == "compass-publish"
        )

        expected = {
            name: phase["task"]["name"]
            for name, phase in PHASES.items()
        }
        expected["compass-publish"] = "mcp-compass-publish"

        for logical_name, task_name in expected.items():
            ref = tasks[logical_name]["taskRef"]

            params = {
                param["name"]: param["value"]
                for param in ref["params"]
            }

            self.assertEqual(ref.get("resolver"), "bundles", logical_name)
            self.assertEqual(
                params.get("bundle"),
                "$(params.task-bundle)",
                logical_name,
            )
            self.assertEqual(params.get("name"), task_name, logical_name)
            self.assertEqual(params.get("kind"), "Task", logical_name)

    def test_bundle_makefile_uses_registry(self):
        """Bundle build discovers evaluation tasks from the phase registry."""
        makefile_path = (
                self.repo_root / "pipeline" / "integration" / "Makefile"
        )
        makefile = makefile_path.read_text()

        expected_entries = (
            "MCP_EVAL_TASK_FILES",
            "scripts.mcp.generate_mcp_pipeline --task-files",
            "$(foreach file,$(MCP_EVAL_TASK_FILES),-f $(file))",
            "mcp-compass-publish.yaml",
            "mcp-eval-pipeline.yaml",
            "verify-mcp-pipeline",
        )

        for entry in expected_entries:
            self.assertIn(entry, makefile)

if __name__ == "__main__":
    unittest.main()
