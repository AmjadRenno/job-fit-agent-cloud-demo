"""Add Company Source Management lifecycle and preview persistence.

Revision ID: 0004_source_management
Revises: 0003_job_analysis_status
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_source_management"
down_revision = "0003_job_analysis_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("lifecycle_status", sa.Text(), nullable=False, server_default="ACTIVE"))
    op.add_column("companies", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_companies_lifecycle_status",
        "companies",
        "lifecycle_status IN ('ACTIVE','ARCHIVED')",
    )

    op.add_column("job_sources", sa.Column("canonical_url", sa.Text()))
    op.execute("UPDATE job_sources SET canonical_url = official_jobs_url WHERE canonical_url IS NULL")
    op.create_unique_constraint("uq_job_sources_canonical_url", "job_sources", ["canonical_url"])
    op.add_column("job_sources", sa.Column("lifecycle_status", sa.Text(), nullable=False, server_default="DRAFT"))
    op.execute("UPDATE job_sources SET lifecycle_status = CASE WHEN enabled THEN 'ACTIVE' ELSE 'DRAFT' END")
    op.add_column("job_sources", sa.Column("readiness_status", sa.Text(), nullable=False, server_default="UNKNOWN"))
    op.add_column("job_sources", sa.Column("boundary_fingerprint", sa.Text()))
    op.add_column("job_sources", sa.Column("last_validated_at", sa.DateTime(timezone=True)))
    op.add_column("job_sources", sa.Column("approved_preview_version", sa.Integer()))
    op.add_column("job_sources", sa.Column("approved_at", sa.DateTime(timezone=True)))
    op.add_column("job_sources", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_job_sources_lifecycle_status",
        "job_sources",
        "lifecycle_status IN ('DRAFT','ONBOARDING','PREVIEW_READY','ACTIVE','DISABLED','REVALIDATION_REQUIRED','ARCHIVED')",
    )
    op.create_check_constraint(
        "ck_job_sources_readiness_status",
        "job_sources",
        "readiness_status IN ('UNKNOWN','READY','BLOCKED')",
    )

    op.create_table(
        "source_previews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_sources.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("boundary_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="READY"),
        sa.Column("validation_summary", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("discarded_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_id", "version", name="uq_source_previews_source_version"),
        sa.CheckConstraint(
            "status IN ('READY','APPROVED','DISCARDED','EXPIRED','INVALID')",
            name="ck_source_previews_status",
        ),
    )
    op.create_index("idx_source_previews_source_status", "source_previews", ["source_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_source_previews_source_status", table_name="source_previews")
    op.drop_table("source_previews")
    op.drop_constraint("ck_job_sources_readiness_status", "job_sources", type_="check")
    op.drop_constraint("ck_job_sources_lifecycle_status", "job_sources", type_="check")
    for column in (
        "archived_at",
        "approved_at",
        "approved_preview_version",
        "last_validated_at",
        "boundary_fingerprint",
        "readiness_status",
        "lifecycle_status",
    ):
        op.drop_column("job_sources", column)
    op.drop_constraint("uq_job_sources_canonical_url", "job_sources", type_="unique")
    op.drop_column("job_sources", "canonical_url")
    op.drop_constraint("ck_companies_lifecycle_status", "companies", type_="check")
    op.drop_column("companies", "archived_at")
    op.drop_column("companies", "lifecycle_status")
