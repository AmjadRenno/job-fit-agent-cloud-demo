"""Add product experience preferences and job starring.

Revision ID: 0005_product_experience
Revises: 0004_source_management
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_product_experience"
down_revision = "0004_source_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("starred", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("candidate_profiles", sa.Column("profile_data", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("candidate_profiles", "profile_data")
    op.drop_column("jobs", "starred")
