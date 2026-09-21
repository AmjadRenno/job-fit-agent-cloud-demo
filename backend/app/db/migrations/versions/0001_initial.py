"""Create the Phase 2 persistence schema.

Revision ID: 0001_initial
Revises:
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def uuid_column(name, *args, **kwargs):
    if kwargs.get("primary_key") and "server_default" not in kwargs:
        kwargs["server_default"] = sa.text("gen_random_uuid()")
    return sa.Column(name, postgresql.UUID(as_uuid=True), *args, **kwargs)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table("companies",
        uuid_column("id", primary_key=True), sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False, unique=True), sa.Column("country", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("job_sources",
        uuid_column("id", primary_key=True), sa.Column("source_id", sa.Text(), nullable=False, unique=True),
        uuid_column("company_id", sa.ForeignKey("companies.id")), sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("official_jobs_url", sa.Text(), nullable=False), sa.Column("allowed_domains", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("robots_txt_url", sa.Text()), sa.Column("crawl_delay_seconds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("last_successful_run", sa.DateTime(timezone=True)),
        sa.Column("health_status", sa.Text(), nullable=False, server_default="UNKNOWN"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("jobs",
        uuid_column("id", primary_key=True), uuid_column("source_id", sa.ForeignKey("job_sources.id"), nullable=False),
        uuid_column("company_id", sa.ForeignKey("companies.id")), sa.Column("external_job_id", sa.Text()),
        sa.Column("source_job_url", sa.Text(), nullable=False), sa.Column("canonical_url", sa.Text()),
        sa.Column("title", sa.Text(), nullable=False), sa.Column("description", sa.Text()), sa.Column("raw_content", sa.Text()),
        sa.Column("location_raw", sa.Text()), sa.Column("city", sa.Text()), sa.Column("country_code", sa.CHAR(2)),
        sa.Column("is_hybrid", sa.Boolean()), sa.Column("is_remote", sa.Boolean()),
        sa.Column("employment_type", sa.Text(), server_default="UNKNOWN"), sa.Column("seniority_level", sa.Text()),
        sa.Column("language", sa.Text()), sa.Column("date_posted", sa.Date()), sa.Column("application_deadline", sa.Date()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("content_hash", sa.Text()), sa.Column("is_duplicate", sa.Boolean(), nullable=False, server_default=sa.false()),
        uuid_column("duplicate_of", sa.ForeignKey("jobs.id")),
        sa.Column("availability_status", sa.Text(), nullable=False, server_default="UNKNOWN"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_id", "external_job_id", name="uq_jobs_source_external"),
        sa.UniqueConstraint("source_id", "source_job_url", name="uq_jobs_source_url"),
        sa.UniqueConstraint("source_id", "canonical_url", name="uq_jobs_source_canonical_url"),
        sa.CheckConstraint("employment_type IS NULL OR employment_type IN ('FULL_TIME','PART_TIME','STUDENT','CONTRACT','UNKNOWN')", name="ck_jobs_employment_type"),
        sa.CheckConstraint("availability_status IN ('ACTIVE','CLOSED','EXPIRED','FILLED','UNAVAILABLE','UNKNOWN')", name="ck_jobs_availability_status"))
    for name, columns in (("idx_jobs_source_id", ["source_id"]), ("idx_jobs_availability", ["availability_status"]),
                          ("idx_jobs_first_seen", ["first_seen_at"]), ("idx_jobs_content_hash", ["content_hash"]),
                          ("idx_jobs_country_city", ["country_code", "city"])):
        op.create_index(name, "jobs", columns)
    op.create_table("agent_runs",
        uuid_column("id", primary_key=True), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)), sa.Column("status", sa.Text(), nullable=False, server_default="RUNNING"),
        *[sa.Column(name, sa.Integer(), nullable=False, server_default="0") for name in ("sources_total", "sources_success", "sources_partial", "sources_failed", "jobs_discovered", "jobs_new", "jobs_analyzed", "jobs_matched", "jobs_quarantined")],
        sa.Column("error_summary", sa.Text()))
    op.create_table("job_analyses",
        uuid_column("id", primary_key=True), uuid_column("job_id", sa.ForeignKey("jobs.id"), nullable=False),
        uuid_column("run_id", sa.ForeignKey("agent_runs.id")), sa.Column("responsibilities", postgresql.ARRAY(sa.Text())), sa.Column("must_have", postgresql.ARRAY(sa.Text())),
        sa.Column("nice_to_have", postgresql.ARRAY(sa.Text())), sa.Column("technologies", postgresql.ARRAY(sa.Text())), sa.Column("seniority_level", sa.Text()),
        sa.Column("language_requirement", sa.Text()), sa.Column("key_constraints", postgresql.ARRAY(sa.Text())), sa.Column("analysis_notes", sa.Text()),
        sa.Column("model", sa.Text(), nullable=False), sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("tokens_used", sa.Integer()), sa.Column("latency_ms", sa.Integer()), sa.Column("schema_valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("validation_errors", postgresql.JSONB()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("candidate_profiles",
        uuid_column("id", primary_key=True), sa.Column("version", sa.Text(), nullable=False, unique=True),
        sa.Column("profile_hash", sa.Text(), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("match_results",
        uuid_column("id", primary_key=True), uuid_column("job_id", sa.ForeignKey("jobs.id"), nullable=False),
        uuid_column("analysis_id", sa.ForeignKey("job_analyses.id")), uuid_column("candidate_profile_id", sa.ForeignKey("candidate_profiles.id")),
        uuid_column("run_id", sa.ForeignKey("agent_runs.id")), sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False), sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("matched_requirements", postgresql.ARRAY(sa.Text())), sa.Column("partial_matches", postgresql.ARRAY(sa.Text())), sa.Column("gaps", postgresql.ARRAY(sa.Text())),
        sa.Column("critical_gaps", postgresql.ARRAY(sa.Text())), sa.Column("evidence", postgresql.JSONB()), sa.Column("reasoning", sa.Text()),
        sa.Column("model", sa.Text(), nullable=False), sa.Column("prompt_version", sa.Text(), nullable=False), sa.Column("tokens_used", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("applications",
        uuid_column("id", primary_key=True), uuid_column("job_id", sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        uuid_column("match_result_id", sa.ForeignKey("match_results.id")), sa.Column("status", sa.Text(), nullable=False, server_default="DISCOVERED"),
        sa.Column("notes", sa.Text()), sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('DISCOVERED','INTERESTING','TO_APPLY','APPLIED','INTERVIEW','REJECTED','OFFER','CLOSED','IGNORED')", name="ck_applications_status"))
    op.create_table("application_events",
        uuid_column("id", primary_key=True), uuid_column("application_id", sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("from_status", sa.Text()), sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False, server_default="HUMAN"), sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("triggered_by IN ('HUMAN','SYSTEM')", name="ck_application_events_triggered_by"))
    op.create_table("cover_letters",
        uuid_column("id", primary_key=True), uuid_column("job_id", sa.ForeignKey("jobs.id"), nullable=False),
        uuid_column("application_id", sa.ForeignKey("applications.id")), uuid_column("match_result_id", sa.ForeignKey("match_results.id")),
        sa.Column("content", sa.Text(), nullable=False), sa.Column("model", sa.Text(), nullable=False), sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("grounding_valid", sa.Boolean()), sa.Column("grounding_notes", sa.Text()), sa.Column("human_reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("human_notes", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table("execution_events",
        uuid_column("id", primary_key=True), uuid_column("run_id", sa.ForeignKey("agent_runs.id"), nullable=False),
        uuid_column("operation_id"), sa.Column("source_id", sa.Text()), uuid_column("job_id", sa.ForeignKey("jobs.id")),
        sa.Column("event_type", sa.Text(), nullable=False), sa.Column("status", sa.Text(), nullable=False), sa.Column("error_code", sa.Text()),
        sa.Column("error_message", sa.Text()), sa.Column("duration_ms", sa.Integer()), sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    for name, columns in (("idx_execution_events_run_id", ["run_id"]), ("idx_execution_events_event_type", ["event_type"]), ("idx_execution_events_status", ["status"])):
        op.create_index(name, "execution_events", columns)
    op.create_table("evaluation_results",
        uuid_column("id", primary_key=True), sa.Column("evaluation_date", sa.Date(), nullable=False), sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("dataset_size", sa.Integer(), nullable=False), sa.Column("metrics", postgresql.JSONB(), nullable=False), sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def downgrade() -> None:
    for name in ("evaluation_results", "execution_events", "cover_letters", "application_events", "applications", "match_results", "candidate_profiles", "job_analyses", "agent_runs"):
        op.drop_table(name)
    for name in ("idx_jobs_country_city", "idx_jobs_content_hash", "idx_jobs_first_seen", "idx_jobs_availability", "idx_jobs_source_id"):
        op.drop_index(name, table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("job_sources")
    op.drop_table("companies")
