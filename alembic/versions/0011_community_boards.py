"""Community boards, post editing and automatic recipe review posts.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("posts") as batch:
        batch.add_column(sa.Column("board", sa.String(10), nullable=False, server_default="general"))
        batch.add_column(sa.Column("review_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key("fk_posts_review_id", "recipe_reviews", ["review_id"], ["id"], ondelete="SET NULL")
        batch.create_unique_constraint("uq_posts_review_id", ["review_id"])
        batch.create_index("ix_posts_board_deleted_id", ["board", "deleted_at", "id"])
    # Existing reviews appear in the recipe board too, preserving their dates.
    connection = op.get_bind()
    reviews = sa.table("recipe_reviews", sa.column("id"), sa.column("author_id"), sa.column("recipe_name"),
                       sa.column("rating"), sa.column("body"), sa.column("created_at"), sa.column("updated_at"), sa.column("deleted_at"))
    posts = sa.table("posts", sa.column("author_id"), sa.column("title"), sa.column("body"),
                     sa.column("board"), sa.column("review_id"), sa.column("created_at"), sa.column("updated_at"))
    for review in connection.execute(sa.select(reviews).where(reviews.c.deleted_at.is_(None)).order_by(reviews.c.id)).mappings():
        connection.execute(posts.insert().values(
            author_id=review["author_id"], title=f'{review["recipe_name"][:90]} 요리 후기',
            body=review["body"] or f'별점 {review["rating"]}점을 남겼어요.', board="recipe", review_id=review["id"],
            created_at=review["created_at"], updated_at=review["updated_at"],
        ))


def downgrade() -> None:
    # Remove generated posts and their dependents before removing their link.
    connection = op.get_bind()
    for table in ("post_likes", "comments", "post_photos"):
        connection.execute(sa.text(f"DELETE FROM {table} WHERE post_id IN (SELECT id FROM posts WHERE review_id IS NOT NULL)"))
    connection.execute(sa.text("DELETE FROM posts WHERE review_id IS NOT NULL"))
    with op.batch_alter_table("posts") as batch:
        batch.drop_index("ix_posts_board_deleted_id")
        batch.drop_constraint("uq_posts_review_id", type_="unique")
        batch.drop_constraint("fk_posts_review_id", type_="foreignkey")
        batch.drop_column("updated_at")
        batch.drop_column("review_id")
        batch.drop_column("board")
