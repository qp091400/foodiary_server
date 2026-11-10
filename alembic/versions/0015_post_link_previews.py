"""A post keeps one link preview per link, not just the first.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 새 칸 하나만 더한다. 기존 글은 null이고, 읽을 때 예전 칸(link_preview)의 것을 하나짜리 목록으로 쓴다.
    op.add_column("posts", sa.Column("link_previews", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("posts") as batch:
        batch.drop_column("link_previews")
