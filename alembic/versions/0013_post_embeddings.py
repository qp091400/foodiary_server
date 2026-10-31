"""Post embeddings for hybrid (keyword + vector) community search.

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Vectors are created lazily by the first searches; this migration only adds the table.
    op.create_table("post_embeddings",
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("content_hash", sa.String(40), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))


def downgrade() -> None:
    op.drop_table("post_embeddings")
