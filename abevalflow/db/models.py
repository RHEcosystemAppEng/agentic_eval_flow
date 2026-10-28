"""Queryable pipeline evidence, from plugin summaries down to individual judge values."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


Json = JSON().with_variant(postgresql.JSONB(), "postgresql")


class Identifier:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class SchemaVersion(Base):
    __tablename__ = "evidence_schema"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class Plugin(Identifier, Base):
    __tablename__ = "plugins"
    name: Mapped[str] = mapped_column(String(255), unique=True)
    skills: Mapped[list[Skill]] = relationship(back_populates="plugin", cascade="all, delete-orphan")


class Skill(Identifier, Base):
    __tablename__ = "skills"
    plugin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plugins.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    plugin: Mapped[Plugin] = relationship(back_populates="skills")
    evaluations: Mapped[list[SkillEvaluation]] = relationship(back_populates="skill", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("plugin_id", "name"),)


class SkillEvaluation(Identifier, Base):
    __tablename__ = "skill_evaluations"
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    run_id: Mapped[str] = mapped_column(String(255), unique=True)
    submission_name: Mapped[str] = mapped_column(String(255))
    commit_sha: Mapped[str] = mapped_column(String(64))
    built_image_ref: Mapped[str | None] = mapped_column(Text)
    trial_image_ref: Mapped[str | None] = mapped_column(Text)
    source_digest: Mapped[str | None] = mapped_column(String(128))
    evaluator_version: Mapped[str] = mapped_column(String(100))
    eval_engine: Mapped[str] = mapped_column(String(50), default="aeh")
    mode: Mapped[str | None] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))
    evidence_complete: Mapped[bool] = mapped_column(Boolean)
    recommendation: Mapped[str | None] = mapped_column(String(20))
    recommendation_reason: Mapped[str | None] = mapped_column(Text)
    policy_json: Mapped[dict | None] = mapped_column(Json)
    comparison_statistics: Mapped[dict | None] = mapped_column(Json)
    requested_steps: Mapped[list] = mapped_column(Json, default=list)
    execution_details: Mapped[dict] = mapped_column(Json, default=dict)
    warnings: Mapped[list] = mapped_column(Json, default=list)
    skill: Mapped[Skill] = relationship(back_populates="evaluations")
    tiers: Mapped[list[TierResult]] = relationship(back_populates="evaluation", cascade="all, delete-orphan")
    variants: Mapped[list[EvaluationVariant]] = relationship(back_populates="evaluation", cascade="all, delete-orphan")
    pairwise: Mapped[PairwiseResult | None] = relationship(back_populates="evaluation", cascade="all, delete-orphan")
    artifacts: Mapped[list[EvidenceArtifact]] = relationship(back_populates="evaluation", cascade="all, delete-orphan")
    certifications: Mapped[list[CertificationResult]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan"
    )
    metrics: Mapped[EvaluationMetrics | None] = relationship(back_populates="evaluation", cascade="all, delete-orphan")
    __table_args__ = (Index("ix_skill_evaluations_latest", "skill_id", "started_at", "run_id"),)


class TierResult(Identifier, Base):
    __tablename__ = "tier_results"
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skill_evaluations.id", ondelete="CASCADE"))
    tier: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    verdict: Mapped[str] = mapped_column(String(20))
    evaluation: Mapped[SkillEvaluation] = relationship(back_populates="tiers")
    checks: Mapped[list[CheckResult]] = relationship(back_populates="tier_result", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("evaluation_id", "tier"), CheckConstraint("tier IN (1, 2)"))


class CheckResult(Identifier, Base):
    """An actual workflow operation: scanner, validation, review, or AEH evaluation."""

    __tablename__ = "check_results"
    tier_result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tier_results.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))
    tool: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    verdict: Mapped[str] = mapped_column(String(20))
    blocking: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float)
    score_method: Mapped[str | None] = mapped_column(String(100))
    enforcement_mode: Mapped[str | None] = mapped_column(String(20))
    passed: Mapped[bool | None] = mapped_column(Boolean)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    threshold: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(Json, default=dict)
    tier_result: Mapped[TierResult] = relationship(back_populates="checks")
    findings: Mapped[list[Finding]] = relationship(back_populates="check", cascade="all, delete-orphan")
    components: Mapped[list[CheckScore]] = relationship(back_populates="check", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("tier_result_id", "name"),)


class CheckScore(Identifier, Base):
    """Tool-emitted dimensions of a quality/review check."""

    __tablename__ = "check_scores"
    check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("check_results.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    score: Mapped[float | None] = mapped_column(Float)
    details: Mapped[Any | None] = mapped_column(Json)
    check: Mapped[CheckResult] = relationship(back_populates="components")
    __table_args__ = (UniqueConstraint("check_id", "name"),)


class Finding(Identifier, Base):
    __tablename__ = "findings"
    check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("check_results.id", ondelete="CASCADE"), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    rule_id: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    case_key: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict | None] = mapped_column(Json)
    check: Mapped[CheckResult] = relationship(back_populates="findings")
    __table_args__ = (CheckConstraint("severity IN ('critical', 'high', 'medium', 'low', 'info')"),)


class EvaluationVariant(Identifier, Base):
    __tablename__ = "evaluation_variants"
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skill_evaluations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(20))
    mean_reward: Mapped[float | None] = mapped_column(Float)
    median_reward: Mapped[float | None] = mapped_column(Float)
    std_reward: Mapped[float | None] = mapped_column(Float)
    n_trials: Mapped[int] = mapped_column(Integer)
    n_passed: Mapped[int] = mapped_column(Integer)
    n_failed: Mapped[int] = mapped_column(Integer)
    n_errors: Mapped[int] = mapped_column(Integer)
    pass_rate: Mapped[float] = mapped_column(Float)
    execution: Mapped[dict | None] = mapped_column(Json)
    run_metrics: Mapped[dict | None] = mapped_column(Json)
    scoring: Mapped[dict] = mapped_column(Json, default=dict)
    evaluation: Mapped[SkillEvaluation] = relationship(back_populates="variants")
    cases: Mapped[list[CaseResult]] = relationship(back_populates="variant", cascade="all, delete-orphan")
    judges: Mapped[list[JudgeSummary]] = relationship(back_populates="variant", cascade="all, delete-orphan")
    __table_args__ = (
        UniqueConstraint("evaluation_id", "name"),
        CheckConstraint("name IN ('control', 'treatment')"),
    )


class JudgeSummary(Identifier, Base):
    __tablename__ = "judge_summaries"
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_variants.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    mean: Mapped[float | None] = mapped_column(Float)
    pass_rate: Mapped[float | None] = mapped_column(Float)
    errored_cases: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[Any | None] = mapped_column(Json)
    variant: Mapped[EvaluationVariant] = relationship(back_populates="judges")
    __table_args__ = (UniqueConstraint("variant_id", "name"),)


class CaseResult(Identifier, Base):
    __tablename__ = "case_results"
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_variants.id", ondelete="CASCADE"))
    case_key: Mapped[str] = mapped_column(String(255))
    reward: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))
    passed: Mapped[bool | None] = mapped_column(Boolean)
    variant: Mapped[EvaluationVariant] = relationship(back_populates="cases")
    judges: Mapped[list[JudgeResult]] = relationship(back_populates="case", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("variant_id", "case_key"),)


class JudgeResult(Identifier, Base):
    __tablename__ = "judge_results"
    case_result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_results.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    judge_type: Mapped[str | None] = mapped_column(String(50))
    raw_value: Mapped[Any | None] = mapped_column(Json)
    normalized_score: Mapped[float | None] = mapped_column(Float)
    score_range: Mapped[list | None] = mapped_column(Json)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(Json)
    case: Mapped[CaseResult] = relationship(back_populates="judges")
    __table_args__ = (UniqueConstraint("case_result_id", "name"),)


class PairwiseResult(Identifier, Base):
    __tablename__ = "pairwise_results"
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill_evaluations.id", ondelete="CASCADE"), unique=True
    )
    run_a: Mapped[str] = mapped_column(Text)
    run_b: Mapped[str] = mapped_column(Text)
    cases_compared: Mapped[int] = mapped_column(Integer)
    treatment_wins: Mapped[int] = mapped_column(Integer)
    control_wins: Mapped[int] = mapped_column(Integer)
    ties: Mapped[int] = mapped_column(Integer)
    errors: Mapped[int] = mapped_column(Integer)
    win_rate: Mapped[float] = mapped_column(Float)
    stability: Mapped[dict | None] = mapped_column(Json)
    evaluation: Mapped[SkillEvaluation] = relationship(back_populates="pairwise")
    cases: Mapped[list[PairwiseCaseResult]] = relationship(back_populates="pairwise", cascade="all, delete-orphan")


class PairwiseCaseResult(Identifier, Base):
    __tablename__ = "pairwise_case_results"
    pairwise_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pairwise_results.id", ondelete="CASCADE"))
    case_key: Mapped[str] = mapped_column(String(255))
    treatment_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_results.id", ondelete="SET NULL"))
    control_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_results.id", ondelete="SET NULL"))
    outcome: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(Json)
    pairwise: Mapped[PairwiseResult] = relationship(back_populates="cases")
    __table_args__ = (UniqueConstraint("pairwise_id", "case_key"),)


class EvidenceArtifact(Identifier, Base):
    __tablename__ = "evidence_artifacts"
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skill_evaluations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    uri: Mapped[str] = mapped_column(Text)
    endpoint: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(255))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    evaluation: Mapped[SkillEvaluation] = relationship(back_populates="artifacts")
    __table_args__ = (UniqueConstraint("evaluation_id", "uri"),)


class CertificationResult(Identifier, Base):
    """Certification checks emitted by the pipeline, with no human decision records."""

    __tablename__ = "certification_results"
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skill_evaluations.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(20))
    passed: Mapped[bool] = mapped_column(Boolean)
    checks_total: Mapped[int] = mapped_column(Integer)
    checks_passed: Mapped[int] = mapped_column(Integer)
    checks: Mapped[list] = mapped_column(Json)
    evaluation: Mapped[SkillEvaluation] = relationship(back_populates="certifications")
    __table_args__ = (UniqueConstraint("evaluation_id", "level"),)


class EvaluationMetrics(Base):
    __tablename__ = "evaluation_metrics"
    phase_metrics: Mapped[dict] = mapped_column(Json, default=dict)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill_evaluations.id", ondelete="CASCADE"), primary_key=True
    )
    model_name: Mapped[str | None] = mapped_column(String(100))
    pipeline_duration_ms: Mapped[int | None] = mapped_column(Integer)
    prepare_duration_ms: Mapped[int | None] = mapped_column(Integer)
    test_duration_ms: Mapped[int | None] = mapped_column(Integer)
    evaluate_duration_ms: Mapped[int | None] = mapped_column(Integer)
    analyze_duration_ms: Mapped[int | None] = mapped_column(Integer)
    store_duration_ms: Mapped[int | None] = mapped_column(Integer)
    total_prompt_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_completion_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    llm_calls_count: Mapped[int | None] = mapped_column(Integer)
    evaluation: Mapped[SkillEvaluation] = relationship(back_populates="metrics")
