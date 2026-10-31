"""Read-only summaries derived from the latest evaluation of each plugin skill.

Ordering uses execution start time, never publication/retry time. Failed and
partial evaluations remain visible instead of falling back to older passing data.
"""

from sqlalchemy import Connection, text

VIEWS = {
    "latest_skill_evaluations": """
        SELECT * FROM (
            SELECT e.*, ROW_NUMBER() OVER (
                PARTITION BY skill_id ORDER BY started_at DESC, run_id DESC
            ) AS latest_rank FROM skill_evaluations e
        ) ranked WHERE latest_rank = 1
    """,
    "check_finding_counts": """
        SELECT c.id AS check_id, COUNT(f.id) AS findings_count,
            SUM(CASE WHEN f.severity = 'critical' THEN 1 ELSE 0 END) AS critical_count,
            SUM(CASE WHEN f.severity = 'high' THEN 1 ELSE 0 END) AS high_count,
            SUM(CASE WHEN f.severity = 'medium' THEN 1 ELSE 0 END) AS medium_count,
            SUM(CASE WHEN f.severity = 'low' THEN 1 ELSE 0 END) AS low_count,
            SUM(CASE WHEN f.severity = 'info' THEN 1 ELSE 0 END) AS info_count
        FROM check_results c LEFT JOIN findings f ON f.check_id = c.id GROUP BY c.id
    """,
    "latest_skill_results": """
        SELECT p.name AS plugin_name, s.name AS skill_name, s.id AS skill_id,
            e.id AS evaluation_id, e.run_id, e.commit_sha, e.built_image_ref,
            e.started_at, e.completed_at, e.status, e.evidence_complete, e.recommendation, e.mode,
            t1.status AS tier1_status, t1.verdict AS tier1_verdict,
            t2.status AS tier2_status, t2.verdict AS tier2_verdict,
            treatment.mean_reward AS treatment_mean_reward, control.mean_reward AS control_mean_reward,
            pw.win_rate AS pairwise_win_rate, pw.treatment_wins, pw.control_wins, pw.ties, pw.errors,
            COALESCE(fc.findings_count, 0) AS findings_count,
            COALESCE(fc.critical_count, 0) AS critical_count, COALESCE(fc.high_count, 0) AS high_count,
            COALESCE(fc.medium_count, 0) AS medium_count, COALESCE(fc.low_count, 0) AS low_count,
            COALESCE(fc.info_count, 0) AS info_count
        FROM latest_skill_evaluations e
        JOIN skills s ON s.id = e.skill_id JOIN plugins p ON p.id = s.plugin_id
        LEFT JOIN tier_results t1 ON t1.evaluation_id = e.id AND t1.tier = 1
        LEFT JOIN tier_results t2 ON t2.evaluation_id = e.id AND t2.tier = 2
        LEFT JOIN evaluation_variants treatment ON treatment.evaluation_id = e.id AND treatment.name = 'treatment'
        LEFT JOIN evaluation_variants control ON control.evaluation_id = e.id AND control.name = 'control'
        LEFT JOIN pairwise_results pw ON pw.evaluation_id = e.id
        LEFT JOIN (
            SELECT t.evaluation_id, SUM(f.findings_count) AS findings_count,
                SUM(f.critical_count) AS critical_count, SUM(f.high_count) AS high_count,
                SUM(f.medium_count) AS medium_count, SUM(f.low_count) AS low_count, SUM(f.info_count) AS info_count
            FROM tier_results t JOIN check_results c ON c.tier_result_id = t.id
            JOIN check_finding_counts f ON f.check_id = c.id GROUP BY t.evaluation_id
        ) fc ON fc.evaluation_id = e.id
    """,
    "latest_skill_judge_scores": """
        SELECT p.name AS plugin_name, s.name AS skill_name, e.id AS evaluation_id, e.commit_sha,
            v.name AS variant, j.name AS judge_name, j.mean, j.pass_rate, j.errored_cases,
            j.details, v.scoring AS scoring_policy
        FROM latest_skill_evaluations e
        JOIN skills s ON s.id = e.skill_id JOIN plugins p ON p.id = s.plugin_id
        JOIN evaluation_variants v ON v.evaluation_id = e.id JOIN judge_summaries j ON j.variant_id = v.id
    """,
    "latest_skill_scorecards": """
        WITH scan_counts AS (
            SELECT t.evaluation_id, c.name AS scan_name,
                SUM(CASE WHEN f.severity = 'critical' THEN 1 ELSE 0 END) AS critical_count,
                SUM(CASE WHEN f.severity = 'high' THEN 1 ELSE 0 END) AS high_count,
                SUM(CASE WHEN f.severity = 'medium' THEN 1 ELSE 0 END) AS medium_count,
                SUM(CASE WHEN f.severity = 'low' THEN 1 ELSE 0 END) AS low_count,
                SUM(CASE WHEN f.severity = 'info' THEN 1 ELSE 0 END) AS info_count
            FROM tier_results t
            JOIN check_results c ON c.tier_result_id = t.id
            LEFT JOIN findings f ON f.check_id = c.id
            WHERE c.name IN ('quality', 'security')
            GROUP BY t.evaluation_id, c.name
        )
        SELECT p.name AS plugin_name, s.name AS skill_name, s.id AS skill_id,
            e.id AS evaluation_id, e.run_id, e.started_at, e.completed_at,
            e.status AS evaluation_status, e.evidence_complete, e.recommendation, e.mode,
            ROUND(CAST(100.0 * quality.score AS NUMERIC), 1) AS quality_scan_score_100,
            quality.status AS quality_scan_status, quality.verdict AS quality_scan_verdict,
            quality.score_method AS quality_score_method,
            COALESCE(qc.critical_count, 0) AS quality_critical_findings,
            COALESCE(qc.high_count, 0) AS quality_high_findings,
            COALESCE(qc.medium_count, 0) AS quality_medium_findings,
            COALESCE(qc.low_count, 0) AS quality_low_findings,
            COALESCE(qc.info_count, 0) AS quality_info_findings,
            ROUND(CAST(100.0 * security.score AS NUMERIC), 1) AS security_scan_score_100,
            security.status AS security_scan_status, security.verdict AS security_scan_verdict,
            security.score_method AS security_score_method,
            COALESCE(sc.critical_count, 0) AS security_critical_findings,
            COALESCE(sc.high_count, 0) AS security_high_findings,
            COALESCE(sc.medium_count, 0) AS security_medium_findings,
            COALESCE(sc.low_count, 0) AS security_low_findings,
            COALESCE(sc.info_count, 0) AS security_info_findings,
            ROUND(CAST(100.0 * review.score AS NUMERIC), 1) AS submission_review_score_100,
            review.verdict AS submission_review_verdict,
            ROUND(CAST(100.0 * treatment.mean_reward AS NUMERIC), 1) AS behavior_score_100,
            treatment.mean_reward AS behavior_mean_reward,
            treatment.n_trials AS behavior_case_count,
            treatment.std_reward AS behavior_std_reward,
            ROUND(CAST(100.0 * pw.win_rate AS NUMERIC), 1) AS pairwise_win_rate_pct,
            pw.cases_compared AS pairwise_case_count,
            pw.treatment_wins, pw.control_wins, pw.ties, pw.errors
        FROM latest_skill_evaluations e
        JOIN skills s ON s.id = e.skill_id JOIN plugins p ON p.id = s.plugin_id
        LEFT JOIN tier_results t1 ON t1.evaluation_id = e.id AND t1.tier = 1
        LEFT JOIN tier_results t2 ON t2.evaluation_id = e.id AND t2.tier = 2
        LEFT JOIN check_results quality ON quality.tier_result_id = t1.id AND quality.name = 'quality'
        LEFT JOIN check_results security ON security.tier_result_id = t1.id AND security.name = 'security'
        LEFT JOIN check_results review ON review.tier_result_id = t2.id AND review.name = 'submission-review'
        LEFT JOIN evaluation_variants treatment ON treatment.evaluation_id = e.id AND treatment.name = 'treatment'
        LEFT JOIN pairwise_results pw ON pw.evaluation_id = e.id
        LEFT JOIN scan_counts qc ON qc.evaluation_id = e.id AND qc.scan_name = 'quality'
        LEFT JOIN scan_counts sc ON sc.evaluation_id = e.id AND sc.scan_name = 'security'
    """,
    "plugin_results": """
        SELECT p.name AS plugin_name, COUNT(s.id) AS skills_count,
            COUNT(r.evaluation_id) AS evaluated_skills,
            SUM(CASE WHEN r.evidence_complete THEN 1 ELSE 0 END) AS complete_skills,
            SUM(CASE WHEN r.status = 'error' THEN 1 ELSE 0 END) AS error_skills,
            SUM(CASE WHEN r.recommendation = 'pass' THEN 1 ELSE 0 END) AS passed_skills,
            SUM(CASE WHEN r.recommendation = 'warn' THEN 1 ELSE 0 END) AS warned_skills,
            SUM(CASE WHEN r.recommendation = 'fail' THEN 1 ELSE 0 END) AS failed_skills,
            COALESCE(SUM(r.critical_count), 0) AS critical_count,
            COALESCE(SUM(r.high_count), 0) AS high_count, COALESCE(SUM(r.medium_count), 0) AS medium_count,
            COALESCE(SUM(r.low_count), 0) AS low_count, COALESCE(SUM(r.info_count), 0) AS info_count
        FROM plugins p LEFT JOIN skills s ON s.plugin_id = p.id
        LEFT JOIN latest_skill_results r ON r.skill_id = s.id GROUP BY p.id, p.name
    """,
}


def create_views(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        # CREATE VIEW IF NOT EXISTS does not refresh an existing SQLite view.
        # Drop derived views in reverse dependency order before recreating them.
        for name in reversed(tuple(VIEWS)):
            connection.execute(text(f"DROP VIEW IF EXISTS {name}"))
    for name, query in VIEWS.items():
        create = "CREATE OR REPLACE VIEW" if connection.dialect.name == "postgresql" else "CREATE VIEW IF NOT EXISTS"
        connection.execute(text(f"{create} {name} AS {query}"))
