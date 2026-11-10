"""Community link previews on posts and temporary community suspensions.

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 새 칸과 새 표만 더한다. 기존 글에는 미리보기가 없고(null), 지금 정지 중인 사람은 없다.
    op.add_column("posts", sa.Column("link_preview", sa.JSON(), nullable=True))
    op.create_table(
        "community_suspensions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("lifted_at", sa.DateTime(), nullable=True),
        sa.Column("lifted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_community_suspensions_user_ends", "community_suspensions", ["user_id", "ends_at"])


def downgrade() -> None:
    op.drop_index("ix_community_suspensions_user_ends", table_name="community_suspensions")
    op.drop_table("community_suspensions")
    with op.batch_alter_table("posts") as batch:
        batch.drop_column("link_preview")
