"""Plugin projections always use the latest execution for each individual skill."""

from datetime import timedelta

from sqlalchemy import create_engine, text

from abevalflow.persistence.postgres import store_snapshot
from abevalflow.results.run import StepResult


def rows(url, query):
    with create_engine(url).connect() as connection:
        return connection.execute(text(query)).mappings().all()


def test_changed_skill_updates_do_not_remove_unchanged_skill_results(pipeline_evidence):
    config, result, url = pipeline_evidence
    store_snapshot(config, result, url)
    other_skill = config.skill.parent / "other"
    other_skill.mkdir()
    second = config.model_copy(update={"skill": other_skill, "run_id": "second"})
    second_run = result.model_copy(update={"run_id": "second", "started_at": result.started_at + timedelta(days=1)})
    store_snapshot(second, second_run, url)
    assert {r["skill_name"] for r in rows(url, "SELECT * FROM latest_skill_results")} == {"demo", "other"}
    plugin = rows(url, "SELECT * FROM plugin_results")[0]
    assert plugin["evaluated_skills"] == 2
    assert plugin["low_count"] == 4


def test_later_failed_evaluation_replaces_previous_score_and_old_retry_does_not_revert_it(pipeline_evidence):
    config, result, url = pipeline_evidence
    store_snapshot(config, result, url)
    new_config = config.model_copy(update={"run_id": "new-run", "revision": "c" * 40})
    new_run = result.model_copy(
        update={
            "run_id": "new-run",
            "source_revision": "c" * 40,
            "started_at": result.started_at + timedelta(days=1),
            "steps": [StepResult(name="prepare", status="error", verdict="unavailable")],
        }
    )
    # New execution has no tool score evidence. Use a different empty report directory.
    new_config = new_config.model_copy(update={"output": config.output / "new"})
    store_snapshot(new_config, new_run, url)
    store_snapshot(config, result, url)
    latest = rows(url, "SELECT * FROM latest_skill_results")[0]
    assert latest["run_id"] == "new-run" and latest["status"] == "error"
    assert latest["pairwise_win_rate"] is None
    assert latest["commit_sha"] == "c" * 40
    assert rows(url, "SELECT * FROM plugin_results")[0]["error_skills"] == 1
    assert rows(url, "SELECT * FROM latest_skill_judge_scores") == []


def test_latest_summary_and_subscores_are_separate_signals(pipeline_evidence):
    config, result, url = pipeline_evidence
    store_snapshot(config, result, url)
    latest = rows(url, "SELECT * FROM latest_skill_results")[0]
    assert latest["pairwise_win_rate"] == 0.5
    assert latest["treatment_mean_reward"] == 0.9
    assert latest["control_mean_reward"] == 0.3
    judges = rows(url, "SELECT * FROM latest_skill_judge_scores")
    assert {r["variant"]: r["mean"] for r in judges} == {"treatment": 0.9, "control": 0.3}
