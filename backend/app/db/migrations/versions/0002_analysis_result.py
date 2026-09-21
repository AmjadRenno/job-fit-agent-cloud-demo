"""Persist the validated Phase 3 analysis contract.

Revision ID: 0002_analysis_result
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_analysis_result"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_analyses",
        sa.Column("analysis_result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.alter_column("job_analyses", "analysis_result", server_default=None)


def downgrade() -> None:
    op.drop_column("job_analyses", "analysis_result")