"""Add the persistent analysis-status guard to jobs.

Revision ID: 0003_job_analysis_status
Revises: 0002_analysis_result

Additive and reversible: new rows default to PENDING; the analysis service
flips the flag to COMPLETED once a validated analysis has been persisted.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_job_analysis_status"
down_revision = "0002_analysis_result"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("analysis_status", sa.Text(), nullable=False, server_default="PENDING"),
    )


def downgrade() -> None:
    op.drop_column("jobs", "analysis_status")
