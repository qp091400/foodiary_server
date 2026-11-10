"""Retire friend visibility; preserve all explicit fridge memberships and viewers.

Revision ID: 0008
Revises: 0007
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE fridges SET visibility = 'private' WHERE visibility = 'friends'")


def downgrade():
    # Private/public are valid in the previous schema too. Do not widen visibility.
    pass
