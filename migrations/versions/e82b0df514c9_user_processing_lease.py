"""Add a per-user request processing lease.

Revision ID: e82b0df514c9
Revises: d71a9ce403b8
"""

import sqlalchemy as sa
from alembic import op

revision = "e82b0df514c9"
down_revision = "d71a9ce403b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("processing_token", sa.Text(), nullable=True))
    op.add_column(
        "users", sa.Column("processing_lease_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_users_processing_lease",
        "users",
        "(processing_token IS NULL) = (processing_lease_until IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_processing_lease", "users", type_="check")
    op.drop_column("users", "processing_lease_until")
    op.drop_column("users", "processing_token")
