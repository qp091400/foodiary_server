"""Give every invited participant inventory editing access.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO fridge_members (fridge_id, user_id, role, joined_at)
        SELECT v.fridge_id, v.user_id, 'member', v.created_at
        FROM fridge_viewers v
        WHERE NOT EXISTS (
            SELECT 1 FROM fridge_members m
            WHERE m.fridge_id = v.fridge_id AND m.user_id = v.user_id
        )
    """)
    op.execute("DELETE FROM fridge_viewers")
    op.execute("UPDATE fridge_share_links SET permission = 'editor'")


def downgrade():
    # Memberships remain valid under the previous schema; never remove access.
    pass
