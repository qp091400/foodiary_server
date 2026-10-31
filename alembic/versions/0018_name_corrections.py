"""Receipt item names that users corrected, used to fix the next receipts.

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 새 표 하나만 더한다. 기존 데이터는 건드리지 않는다.
    op.create_table(
        "name_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seen", sa.String(100), nullable=False),
        sa.Column("fixed", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "seen", name="uq_name_corrections_user_seen"),
    )
    op.create_index("ix_name_corrections_seen", "name_corrections", ["seen"])


def downgrade() -> None:
    op.drop_index("ix_name_corrections_seen", table_name="name_corrections")
    op.drop_table("name_corrections")
