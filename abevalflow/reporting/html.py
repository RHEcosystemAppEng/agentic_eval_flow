"""Render a human-readable, skill-level HTML report from saved run evidence."""

from __future__ import annotations

import html
import json
import mimetypes
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from abevalflow.results.run import RunResult


SCAN_FILES = {
    "quality": ("skillmd-quality-scan.json", "Skill quality scanner"),
    "security": ("security-scan.json", "Cisco security scanner"),
    "markdown_security": ("skillmd-security-scan.json", "Skill Markdown security scanner"),
}
SEVERITIES = ("critical", "high", "medium", "low", "info")
PENALTIES = {"critical": 1.0, "high": 0.30, "medium": 0.10, "low": 0.02, "info": 0.0}


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _step(result: RunResult, name: str):
    return next((item for item in result.steps if item.name == name), None)


def _score(findings: list[dict], *, complete: bool) -> float | None:
    if not complete:
        return None
    penalty = sum(PENALTIES.get(str(item.get("severity", "info")).lower(), 0.0) for item in findings)
    return max(0.0, min(1.0, 1.0 - penalty))


def _scanner_findings(path: Path) -> list[dict]:
    payload = _read_json(path)
    return [finding for finding in payload.get("findings", []) if isinstance(finding, dict)]


def _metric_data(reports_dir: Path, result: RunResult, scorecard: dict) -> list[dict]:
    structure_step = _step(result, "structure")
    structure_payload = _read_json(reports_dir / "skill-structure-validation.json")
    structure_complete = structure_step is not None and structure_step.status == "completed" and bool(structure_payload)
    structure_passed = bool(structure_payload.get("passed"))

    quality_step = _step(result, "quality")
    quality_path = reports_dir / SCAN_FILES["quality"][0]
    quality_complete = (
        quality_step is not None
        and quality_step.status == "completed"
        and isinstance(_read_json(quality_path).get("findings"), list)
    )
    quality_findings = _scanner_findings(quality_path)

    security_sources = [
        reports_dir / SCAN_FILES["security"][0],
        reports_dir / SCAN_FILES["markdown_security"][0],
    ]
    security_step = _step(result, "security")
    security_complete = (
        security_step is not None
        and security_step.status == "completed"
        and isinstance(_read_json(security_sources[0]).get("findings"), list)
    )
    if "quality" in result.requested_steps:
        security_complete = (
            security_complete
            and quality_complete
            and isinstance(_read_json(security_sources[1]).get("findings"), list)
        )
    security_findings = [finding for path in security_sources for finding in _scanner_findings(path)]

    evaluation_step = _step(result, "evaluate")
    evaluation_gate = next(
        (
            gate
            for gate in scorecard.get("gates", [])
            if gate.get("policy_key") == "aeh" or gate.get("gate_name") == "evaluation"
        ),
        {},
    )
    behavior_available = evaluation_step is not None and evaluation_step.status == "completed"
    behavior_score = evaluation_gate.get("score") if behavior_available else None
    behavior_details = evaluation_gate.get("details", {})
    behavior_method = behavior_details.get("score_method")
    if behavior_method == "pairwise_win_rate":
        behavior_label = "Treatment pairwise win rate"
    elif behavior_method == "mean_reward":
        behavior_label = "Treatment mean reward"
    elif behavior_available and behavior_details.get("mode") == "single":
        behavior_label = "Treatment mean reward"
    elif behavior_available:
        behavior_label = "Evaluation score"
    else:
        behavior_label = "Evaluation not run"

    return [
        {
            "key": "quality",
            "label": "Quality",
            "score": _score(quality_findings, complete=quality_complete),
            "note": "Skill quality scan · severity weighted",
        },
        {
            "key": "structure",
            "label": "Structure",
            "score": 1.0 if structure_complete and structure_passed else (0.0 if structure_complete else None),
            "note": "Structure validator · pass/fail",
        },
        {
            "key": "security",
            "label": "Security",
            "score": _score(security_findings, complete=security_complete),
            "note": "Security scanners · severity weighted",
        },
        {
            "key": "behavioral",
            "label": "Behavioral",
            "score": behavior_score if isinstance(behavior_score, (int, float)) else None,
            "note": behavior_label,
        },
    ]


def _score_image(metrics: list[dict], destination: Path) -> None:
    """Write a portable SVG overview image for the four skill score dimensions."""
    width, height = 760, 260
    left, right = 148, 55
    bar_width = width - left - right
    rows: list[str] = []
    axis_y = 226
    for tick in (0, 25, 50, 75, 100):
        x = left + bar_width * tick / 100
        rows.append(
            f'<line x1="{x:.1f}" y1="22" x2="{x:.1f}" y2="{axis_y}" class="grid" />'
            f'<text x="{x:.1f}" y="248" text-anchor="middle" class="tick">{tick}</text>'
        )
    for index, metric in enumerate(metrics):
        y = 52 + index * 48
        score = metric["score"]
        rows.append(f'<text x="0" y="{y + 5}" class="label">{xml_escape(metric["label"])}</text>')
        rows.append(f'<rect x="{left}" y="{y - 12}" width="{bar_width}" height="20" rx="10" class="track" />')
        if score is not None:
            amount = max(0.0, min(1.0, float(score))) * bar_width
            rows.append(f'<rect x="{left}" y="{y - 12}" width="{amount:.1f}" height="20" rx="10" class="bar" />')
            value = f"{float(score) * 100:.0f}%"
        else:
            value = "N/A"
        rows.append(f'<text x="{width - 4}" y="{y + 5}" text-anchor="end" class="value">{value}</text>')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">Skill quality overview</title>
<desc id="desc">Scores from zero to one hundred for quality, structure, security, and behavioral evaluation.</desc>
<style>
  text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #273449; }}
  .label {{ font-size: 15px; font-weight: 600; }} .value {{ font-size: 14px; font-weight: 700; }}
  .tick {{ font-size: 11px; fill: #66758a; }} .grid {{ stroke: #dce3eb; stroke-width: 1; }}
  .track {{ fill: #eaf0f5; }} .bar {{ fill: #167d78; }}
</style>
<rect width="100%" height="100%" rx="16" fill="#ffffff" />
{''.join(rows)}
<text x="{left + bar_width / 2:.1f}" y="258" text-anchor="middle" class="tick">Score (0–100)</text>
</svg>'''
    destination.write_text(svg, encoding="utf-8")


def _display(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Pass" if value else "Fail"
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_display(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "; ".join(_display(item) for item in value)
    return str(value)


def _esc(value) -> str:
    return html.escape(_display(value))


def _finding_table(findings: list[dict]) -> str:
    if not findings:
        return '<p class="empty">No findings were reported by this scanner.</p>'
    rows = []
    for finding in findings:
        severity = str(finding.get("severity", "info")).lower()
        rule = finding.get("rule_id") or finding.get("rule") or finding.get("id") or finding.get("title") or "—"
        location = finding.get("file_path") or finding.get("file") or finding.get("location") or "—"
        line = finding.get("line_number") or finding.get("line")
        if line:
            location = f"{location}:{line}"
        message = finding.get("message") or finding.get("description") or finding.get("title") or "Scanner finding"
        rows.append(
            f'<tr><td><span class="severity {html.escape(severity)}">{html.escape(severity.title())}</span></td>'
            f'<td>{_esc(rule)}</td><td><code>{_esc(location)}</code></td><td>{_esc(message)}</td></tr>'
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>Severity</th><th>Rule</th><th>Location</th>'
        f"<th>Finding</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _scanner_section(reports_dir: Path, filename: str, title: str, anchor: str) -> str:
    path = reports_dir / filename
    if not path.is_file():
        return (
            f'<details class="detail-panel" id="{anchor}"><summary>{html.escape(title)}'
            '<span class="pill muted">Not available</span></summary>'
            '<p class="empty">No scanner result was saved for this run.</p></details>'
        )
    payload = _read_json(path)
    findings = [item for item in payload.get("findings", []) if isinstance(item, dict)]
    counts = Counter(str(item.get("severity", "info")).lower() for item in findings)
    count_text = " · ".join(f"{severity.title()} {counts[severity]}" for severity in SEVERITIES if counts[severity])
    count_text = count_text or "0 findings"
    scanner = payload.get("scanner") or payload.get("tool") or payload.get("version")
    metadata = f'<p class="scanner-meta">{html.escape(str(scanner))} · {len(findings)} findings · {html.escape(count_text)}</p>' if scanner else f'<p class="scanner-meta">{len(findings)} findings · {html.escape(count_text)}</p>'
    return (
        f'<details class="detail-panel" id="{anchor}"><summary>{html.escape(title)}'
        f'<span class="pill">{len(findings)} findings</span></summary>{metadata}'
        f"{_finding_table(findings)}"
        f'<p class="artifact-link"><a href="{quote(filename)}">View scanner artifact</a></p></details>'
    )


def _format_judges(judges: dict) -> str:
    if not judges:
        return "No judge data"
    entries = []
    for name, result in judges.items():
        if isinstance(result, dict):
            value = result.get("value", result.get("error", "—"))
            suffix = []
            for key in ("scores", "subscores", "dimensions"):
                if isinstance(result.get(key), dict):
                    suffix.extend(f"{key}: {k} {v}" for k, v in result[key].items())
            rendered = _esc(value)
            if suffix:
                rendered += f'<small class="judge-extra">{_esc(" · ".join(suffix))}</small>'
        else:
            rendered = _esc(result)
        entries.append(f'<li><strong>{_esc(name)}</strong><span>{rendered}</span></li>')
    return f'<ul class="judge-list">{"".join(entries)}</ul>'


def _case_section(report: dict, reports_dir: Path, skill_name: str) -> str:
    trials = report.get("trials", {}) or {}
    treatment_cases = {item.get("trial_name"): item for item in trials.get("treatment", []) if isinstance(item, dict)}
    control_cases = {item.get("trial_name"): item for item in trials.get("control", []) if isinstance(item, dict)}
    pairwise = report.get("pairwise") or {}
    pairwise_cases = {item.get("case_id"): item for item in pairwise.get("per_case", []) if isinstance(item, dict)}
    case_ids = list(dict.fromkeys([*treatment_cases, *control_cases, *pairwise_cases]))
    if not case_ids:
        return '<p class="empty">No case-level evaluation data is available.</p>'

    details = []
    for case_id in case_ids:
        treatment = treatment_cases.get(case_id, {})
        control = control_cases.get(case_id, {})
        comparison = pairwise_cases.get(case_id, {})
        winner = comparison.get("winner")
        winner_label = {"A": "Treatment", "B": "Control"}.get(winner, winner or "Not available")
        reward = treatment.get("reward")
        summary = (
            f"{_esc(case_id)} · treatment {float(reward) * 100:.0f}%" if isinstance(reward, (float, int))
            else f"{_esc(case_id)} · pairwise: {_esc(winner_label)}"
        )
        pairwise_html = ""
        reasoning = comparison.get("reasoning")
        if reasoning:
            pairwise_html = f'<div class="reasoning"><h4>Pairwise judge reasoning</h4><p>{_esc(reasoning)}</p></div>'
        if comparison.get("error"):
            pairwise_html += f'<p class="callout warning">Pairwise comparison error: {_esc(comparison["error"])}</p>'
        details.append(
            f'<details class="case-panel"><summary>{summary}<span class="pill">Winner: {_esc(winner_label)}</span></summary>'
            '<div class="case-grid">'
            f'<section><h4>Treatment</h4><p class="case-score">Reward: {_esc(treatment.get("reward"))}</p>{_format_judges(treatment.get("judges") or {})}</section>'
            f'<section><h4>Control</h4><p class="case-score">Reward: {_esc(control.get("reward"))}</p>{_format_judges(control.get("judges") or {})}</section>'
            '</div>'
            + pairwise_html
            + '</details>'
        )

    links = []
    for variant in ("treatment", "control"):
        run_id = (report.get(variant) or {}).get("run_id")
        if run_id:
            path = f"{quote(skill_name)}/{quote(str(run_id))}/report.html"
            if (reports_dir / skill_name / str(run_id) / "report.html").is_file():
                links.append(f'<a href="{path}">Open {variant} AEH report</a>')
    link_html = f'<p class="artifact-link">{" · ".join(links)}</p>' if links else ""
    return f'<div class="case-list">{"".join(details)}</div>{link_html}'


def _review_section(review: dict, title: str, anchor: str, filename: str | None = None) -> str:
    if not review:
        return ""
    dimensions = review.get("dimensions", {})
    rows = []
    for name, item in dimensions.items():
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        score_cell = f'{float(score) * 100:.0f}' if isinstance(score, (float, int)) else "—"
        rows.append(f'<tr><th>{_esc(name)}</th><td>{score_cell}</td><td>{_esc(item.get("finding", ""))}</td></tr>')
    summary = _esc(review.get("summary", "No summary was provided."))
    overall = review.get("overall_score")
    score_text = f'{float(overall) * 100:.0f}/100' if isinstance(overall, (int, float)) else "—"
    artifact_link = (
        f'<p class="artifact-link"><a href="{quote(filename)}">View review artifact</a></p>'
        if filename
        else ""
    )
    return (
        f'<details class="detail-panel" id="{anchor}"><summary>{html.escape(title)}'
        f'<span class="pill">{_esc(review.get("recommendation", "Review"))} · {score_text}</span></summary>'
        f'<p>{summary}</p><div class="table-wrap"><table><thead><tr><th>Dimension</th><th>Score / 100</th>'
        f'<th>Review finding</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        f'{artifact_link}</details>'
    )


def _review_sections(reports_dir: Path, result: RunResult) -> str:
    sections = []
    skill_path = reports_dir / "skill-review.json"
    if skill_path.is_file():
        sections.append(
            _review_section(_read_json(skill_path), "Skill review", "skill-review", "skill-review.json")
        )
    submission_step = _step(result, "submission-review")
    if submission_step and submission_step.status == "completed":
        sections.append(
            _review_section(submission_step.details, "Submission quality review", "submission-review")
        )
    return "\n".join(sections)


def _structure_section(reports_dir: Path) -> str:
    payload = _read_json(reports_dir / "skill-structure-validation.json")
    if not payload:
        return '<details class="detail-panel" id="structure-findings"><summary>Structure validation<span class="pill muted">Not available</span></summary><p class="empty">No structure result was saved.</p></details>'
    details = payload.get("details", {}) if isinstance(payload.get("details"), dict) else {}
    errors = details.get("errors", payload.get("errors", [])) or []
    warnings = details.get("warnings", payload.get("warnings", [])) or []
    messages = []
    for label, items, cls in (("Errors", errors, "error"), ("Warnings", warnings, "warning")):
        if items:
            messages.append(f'<h4>{label} ({len(items)})</h4><ul class="finding-list">{"".join(f"<li class={cls}>{_esc(item)}</li>" for item in items)}</ul>')
        else:
            messages.append(f'<p class="empty">No {label.lower()}.</p>')
    passed = bool(payload.get("passed"))
    badge = '<span class="pill pass">Pass</span>' if passed else '<span class="pill fail">Fail</span>'
    return (
        f'<details class="detail-panel" id="structure-findings"><summary>Structure validation{badge}</summary>'
        f'<p>{_esc(payload.get("message", ""))}</p>{"".join(messages)}'
        '<p class="artifact-link"><a href="skill-structure-validation.json">View validation artifact</a></p></details>'
    )


def render_evidence_index(reports_dir: Path) -> Path:
    """Write a navigable HTML index for the run's report artifacts."""
    entries = []
    for path in sorted(reports_dir.rglob("*")):
        if not path.is_file() or path.name == "evidence.html" or path.is_symlink():
            continue
        rel = path.relative_to(reports_dir).as_posix()
        media = mimetypes.guess_type(path.name)[0] or "file"
        entries.append(f'<li><a href="{quote(rel)}">{html.escape(rel)}</a><span>{html.escape(media)}</span></li>')
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Run evidence</title><style>body{{font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#182433}}h1{{font-size:2rem}}li{{display:flex;justify-content:space-between;gap:16px;padding:10px;border-bottom:1px solid #e4e9ef}}a{{color:#146b67}}span{{color:#6a7685;font-size:.85rem}}</style>
<h1>Run evidence</h1><p>Artifacts produced for this skill evaluation.</p><ul>{''.join(entries)}</ul></html>'''
    destination = reports_dir / "evidence.html"
    destination.write_text(page, encoding="utf-8")
    return destination


def render_skill_report(reports_dir: Path, result: RunResult, scorecard: dict) -> tuple[Path, Path]:
    """Create the high-level HTML report and its score overview image."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    metrics = _metric_data(reports_dir, result, scorecard)
    score_image = reports_dir / "score-overview.svg"
    _score_image(metrics, score_image)
    report = _read_json(reports_dir / "report.json")
    skill_name = result.skill
    recommendation = str(scorecard.get("recommendation", "incomplete")).upper()
    status_class = recommendation.lower() if recommendation.lower() in {"pass", "warn", "fail"} else "muted"

    cards = []
    for metric in metrics:
        score = metric["score"]
        score_text = f"{float(score) * 100:.0f}" if isinstance(score, (int, float)) else "N/A"
        state = "Scored" if score is not None else "Not run / unavailable"
        cards.append(
            f'<article class="score-card {metric["key"]}"><div class="score-label">{html.escape(metric["label"])}</div>'
            f'<div class="score-number">{score_text}<small>{"/100" if score is not None else ""}</small></div>'
            f'<div class="meter"><span style="width:{float(score) * 100 if score is not None else 0:.1f}%"></span></div>'
            f'<div class="score-note">{html.escape(metric["note"])}</div><div class="score-state">{state}</div></article>'
        )

    scanner_sections = "".join(
        _scanner_section(reports_dir, filename, title, anchor)
        for filename, title, anchor in (
            ("skillmd-quality-scan.json", "Skill quality findings", "quality-findings"),
            ("security-scan.json", "Cisco security findings", "security-findings"),
            ("skillmd-security-scan.json", "Skill Markdown security findings", "markdown-security-findings"),
        )
    )
    provenance = report.get("provenance", {}) if report else {}
    run_id = result.run_id
    revision = provenance.get("commit_sha") or result.source_revision or "—"
    outcome_summary = scorecard.get("recommendation_reason", "")
    eval_details = (scorecard.get("gates") or [])
    behavioral_gate = next((gate for gate in eval_details if gate.get("gate_name") == "evaluation"), {})
    behavior_note = behavioral_gate.get("message") or "Behavioral evaluation was not completed."
    summary_metrics = report.get("summary", {}) if report else {}
    case_count = summary_metrics.get("treatment", {}).get("n_trials", 0)
    markdown_link = (
        '<a class="secondary" href="report.md">Open Markdown report</a>'
        if (reports_dir / "report.md").is_file()
        else ""
    )

    page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(skill_name)} · Skill evaluation report</title>
<style>
:root{{--ink:#172433;--muted:#607184;--line:#dce4eb;--paper:#fff;--canvas:#f2f5f8;--teal:#12756f;--teal-soft:#e5f4f2;--green:#1c7a4c;--amber:#9b5b00;--red:#b42330;--shadow:0 8px 28px rgba(21,41,60,.07)}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--canvas);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.shell{{max-width:1180px;margin:auto;padding:34px 26px 70px}}header{{background:linear-gradient(120deg,#123c48,#176c68);color:#fff;border-radius:20px;padding:30px 34px;box-shadow:var(--shadow)}}
.eyebrow{{font-size:.76rem;letter-spacing:.13em;text-transform:uppercase;opacity:.76;font-weight:700}}h1{{margin:8px 0 6px;font-size:clamp(2rem,4vw,3rem);letter-spacing:-.035em}}.subtitle{{margin:0;color:#d8ecea}}.header-row{{display:flex;justify-content:space-between;align-items:flex-start;gap:20px}}.status{{padding:9px 16px;border-radius:999px;background:#fff;color:#164b4d;font-size:.9rem;font-weight:800;letter-spacing:.08em;white-space:nowrap}}
.status.pass{{color:#176b49}}.status.warn{{color:#9b5b00}}.status.fail{{color:#b42330}}.status.muted{{color:#607184}}
.meta{{display:flex;flex-wrap:wrap;gap:9px;margin-top:23px}}.meta span{{padding:6px 10px;border:1px solid #ffffff45;border-radius:8px;color:#e4f0f0;font-size:.83rem}}.actions{{display:flex;gap:10px;flex-wrap:wrap;margin-top:17px}}.actions a,.artifact-link a{{display:inline-block;text-decoration:none;color:#075d5b;font-weight:700}}.actions a{{padding:9px 13px;border-radius:9px;background:#fff}}.actions a.secondary{{background:#ffffff18;color:#fff;border:1px solid #ffffff45}}
.intro{{margin:27px 0 12px;display:flex;justify-content:space-between;align-items:end;gap:16px}}h2{{font-size:1.35rem;letter-spacing:-.02em;margin:0}}.intro p,.muted-text{{color:var(--muted);margin:5px 0 0}}.score-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px;margin:15px 0 22px}}.score-card{{background:var(--paper);border:1px solid var(--line);border-radius:15px;padding:18px;box-shadow:var(--shadow);min-width:0}}.score-label{{font-weight:750;font-size:.95rem}}.score-number{{font-size:2.25rem;line-height:1.2;font-weight:800;letter-spacing:-.04em;margin:10px 0 12px;font-variant-numeric:tabular-nums}}.score-number small{{font-size:.9rem;color:var(--muted);letter-spacing:0;margin-left:3px}}.meter{{height:8px;background:#e9eef2;border-radius:10px;overflow:hidden}}.meter span{{display:block;height:100%;background:var(--teal);border-radius:10px}}.score-note,.score-state{{color:var(--muted);font-size:.8rem;margin-top:11px}}.score-state{{font-weight:650;margin-top:3px}}
.overview{{background:var(--paper);border:1px solid var(--line);border-radius:15px;padding:15px 20px;box-shadow:var(--shadow);margin:14px 0 31px}}.overview img{{display:block;width:100%;height:auto;max-height:310px;object-fit:contain}}.overview figcaption{{font-size:.82rem;color:var(--muted);padding:0 4px 3px}}
.section{{margin:34px 0}}.section-head{{margin-bottom:12px}}.section-head p{{margin:4px 0;color:var(--muted)}}.detail-panel,.case-panel{{background:var(--paper);border:1px solid var(--line);border-radius:12px;margin:10px 0;box-shadow:0 2px 9px rgba(21,41,60,.035);overflow:hidden}}summary{{cursor:pointer;list-style:none;padding:15px 18px;font-weight:750;display:flex;align-items:center;justify-content:space-between;gap:12px}}summary::-webkit-details-marker{{display:none}}summary:before{{content:"＋";color:var(--teal);font-weight:800;margin-right:2px}}details[open]>summary:before{{content:"−"}}details[open]>summary{{border-bottom:1px solid var(--line)}}.detail-panel>p,.detail-panel>h4,.detail-panel>.table-wrap,.detail-panel>.finding-list,.detail-panel>.artifact-link,.case-panel>.case-grid,.case-panel>.reasoning,.case-panel>.callout{{margin-left:18px;margin-right:18px}}.detail-panel>p:last-child,.case-panel>.reasoning:last-child{{margin-bottom:18px}}.pill{{font-size:.72rem;font-weight:700;color:#315b5c;background:var(--teal-soft);padding:4px 9px;border-radius:999px;white-space:nowrap}}.pill.muted{{background:#edf1f4;color:#718091}}.pill.pass{{background:#e8f5ed;color:var(--green)}}.pill.fail{{background:#fae9eb;color:var(--red)}}.scanner-meta,.empty{{color:var(--muted);font-size:.9rem}}.table-wrap{{overflow-x:auto;margin-top:12px;margin-bottom:16px}}table{{border-collapse:collapse;width:100%;min-width:650px}}th,td{{padding:10px 12px;text-align:left;vertical-align:top;border-bottom:1px solid #e8edf1}}th{{font-size:.76rem;color:#637487;text-transform:uppercase;letter-spacing:.06em;background:#f8fafb}}td{{font-size:.9rem}}code{{font-size:.83em;color:#42566b;overflow-wrap:anywhere}}.severity{{display:inline-block;border-radius:99px;padding:3px 8px;background:#edf1f4;color:#536476;font-size:.78rem;font-weight:700}}.severity.critical,.severity.high{{background:#fae9eb;color:var(--red)}}.severity.medium{{background:#fff3df;color:var(--amber)}}.severity.low{{background:#eaf2fb;color:#315e8e}}.finding-list{{padding-left:38px}}.finding-list li{{padding:4px 0}}.error{{color:var(--red)}}.warning{{color:var(--amber)}}
.case-list{{display:grid;gap:10px}}.case-panel summary{{background:#fff}}.case-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:14px 0}}.case-grid section{{background:#f8fafb;border:1px solid var(--line);border-radius:10px;padding:13px}}.case-grid h4,.reasoning h4{{margin:0 0 8px}}.case-score{{font-weight:750}}.judge-list{{list-style:none;padding:0;margin:0}}.judge-list li{{display:grid;grid-template-columns:minmax(120px,1fr) 2fr;gap:8px;padding:6px 0;border-top:1px solid #e4eaf0;font-size:.87rem}}.judge-extra{{display:block;color:var(--muted);margin-top:3px;overflow-wrap:anywhere}}.reasoning{{margin:0 18px 18px;padding:14px 16px;background:#f4f7fa;border-left:3px solid var(--teal);border-radius:5px}}.reasoning p{{white-space:pre-wrap;margin:0;color:#33475b}}.callout{{padding:12px;background:#fff6e8;border-radius:8px}}.artifact-link{{font-size:.88rem;margin-top:16px;margin-bottom:18px}}.basis{{background:#e9eff3;border-radius:12px;padding:17px 19px;color:#4a5e70;font-size:.88rem}}.basis ul{{margin:8px 0 0;padding-left:20px}}footer{{margin-top:33px;color:#758294;font-size:.8rem;text-align:center}}
@media(max-width:850px){{.score-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.header-row{{display:block}}.status{{display:inline-block;margin-top:15px}}}}
@media(max-width:560px){{.shell{{padding:18px 13px 45px}}header{{padding:24px 20px}}.score-grid{{gap:8px}}.score-card{{padding:14px}}.score-number{{font-size:1.8rem}}.case-grid{{grid-template-columns:1fr}}.intro{{display:block}}}}
</style></head><body><main class="shell">
<header><div class="header-row"><div><div class="eyebrow">Skill evaluation · overall report</div><h1>{html.escape(skill_name)}</h1><p class="subtitle">One view of quality, structure, security, and behavior.</p></div><div class="status {status_class}">{html.escape(recommendation)}</div></div>
<div class="meta"><span>Run {html.escape(run_id)}</span><span>Revision {html.escape(str(revision)[:12])}</span><span>{case_count} behavioral cases</span><span>Evidence {'complete' if result.evidence_complete else 'partial'}</span></div>
<div class="actions"><a href="evidence.html">Browse evidence directory</a>{markdown_link}</div></header>
<section class="intro"><div><h2>At a glance</h2><p>{html.escape(outcome_summary)}</p></div></section>
<section class="score-grid">{''.join(cards)}</section>
<figure class="overview"><a href="score-overview.svg"><img src="score-overview.svg" alt="Horizontal chart showing skill scores from zero to one hundred"></a><figcaption>Score overview · select the image to open it at full size.</figcaption></figure>
<section class="section"><div class="section-head"><h2>Detailed results</h2><p>Expand a scanner or case to inspect findings, judge results, and reasoning.</p></div>
{_review_sections(reports_dir, result)}
{_structure_section(reports_dir)}
{scanner_sections}
<details class="detail-panel" id="behavior-cases"><summary>Behavioral results by case<span class="pill">{case_count} cases</span></summary>
<p class="scanner-meta">{html.escape(str(behavior_note))}</p><div class="case-list">{_case_section(report, reports_dir, skill_name)}</div></details>
</section>
<section class="section basis"><strong>Score basis</strong><ul>
<li>Quality and security: 100 minus the configured severity penalties for the findings; unavailable scans show N/A.</li>
<li>Structure: validator pass is 100 and fail is 0; this check is binary.</li>
<li>Behavioral: the evaluator score for the selected mode (pairwise win rate or single-run mean reward).</li>
<li>Overall PASS/WARN/FAIL follows the existing evaluation policy; category scores are not averaged into a new composite.</li>
</ul><p>Recommendation detail: {html.escape(str(outcome_summary or 'No recommendation detail available.'))}</p></section>
<footer>Generated by abevalflow · {html.escape(str(result.completed_at or result.started_at))}</footer>
</main></body></html>'''
    report_path = reports_dir / "report.html"
    report_path.write_text(page, encoding="utf-8")
    return report_path, score_image
