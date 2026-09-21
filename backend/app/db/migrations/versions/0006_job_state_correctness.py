"""Normalize company identity and add explicit job seen state.

Revision ID: 0006_job_state_correctness
Revises: 0005_product_experience
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_job_state_correctness"
down_revision = "0005_product_experience"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True))
    # Preserve both Energinet rows and every historical relationship. The empty
    # duplicate is archived under a non-canonical tombstone domain; the row
    # carrying sources/jobs becomes the normalized company identity.
    op.execute("""
        UPDATE companies
        SET domain = 'archived-' || id::text || '.energinet.dk',
            lifecycle_status = 'ARCHIVED',
            archived_at = COALESCE(archived_at, now())
        WHERE domain = 'energinet.dk'
          AND NOT EXISTS (SELECT 1 FROM job_sources s WHERE s.company_id = companies.id)
          AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.company_id = companies.id)
    """)
    op.execute("UPDATE companies SET domain = 'energinet.dk' WHERE domain = 'www.energinet.dk'")


def downgrade() -> None:
    op.execute("UPDATE companies SET domain = 'www.energinet.dk' WHERE domain = 'energinet.dk' AND name = 'Energinet'")
    op.execute("""
        UPDATE companies
        SET domain = 'energinet.dk', lifecycle_status = 'ACTIVE', archived_at = NULL
        WHERE name = 'Energinet' AND domain LIKE 'archived-%.energinet.dk'
          AND NOT EXISTS (SELECT 1 FROM job_sources s WHERE s.company_id = companies.id)
          AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.company_id = companies.id)
    """)
    op.drop_column("jobs", "seen_at")
