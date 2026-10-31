"""recipe reviews and the notification icon's read marker

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('recipe_reviews',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recipe_id', sa.String(length=64), nullable=False),
    sa.Column('recipe_name', sa.String(length=200), nullable=False),
    sa.Column('author_id', sa.Integer(), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=False),
    sa.Column('body', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.Column('deleted_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_recipe_reviews_author_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], name=op.f('fk_recipe_reviews_deleted_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recipe_reviews'))
    )
    op.create_index(op.f('ix_recipe_reviews_author_id'), 'recipe_reviews', ['author_id'], unique=False)
    op.create_index('ix_recipe_reviews_recipe_deleted_id', 'recipe_reviews', ['recipe_id', 'deleted_at', 'id'], unique=False)
    op.create_index(
        'uq_recipe_reviews_live_author',
        'recipe_reviews',
        ['recipe_id', 'author_id'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
        sqlite_where=sa.text('deleted_at IS NULL'),
    )
    # 이미 달린 댓글은 읽은 것으로 시작한다 (알림 아이콘이 처음 켜질 때 옛 댓글이 한꺼번에 새 알림으로 뜨지 않게)
    op.add_column('users', sa.Column('comments_seen_id', sa.Integer(), nullable=False, server_default='0'))
    op.execute("UPDATE users SET comments_seen_id = (SELECT COALESCE(MAX(id), 0) FROM comments)")


def downgrade() -> None:
    op.drop_column('users', 'comments_seen_id')
    op.drop_index('uq_recipe_reviews_live_author', table_name='recipe_reviews')
    op.drop_index('ix_recipe_reviews_recipe_deleted_id', table_name='recipe_reviews')
    op.drop_index(op.f('ix_recipe_reviews_author_id'), table_name='recipe_reviews')
    op.drop_table('recipe_reviews')
