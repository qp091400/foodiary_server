"""friends, fridge visibility, community posts

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30 23:00:00
"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# app.security.INVITE_ALPHABET와 같다. 마이그레이션은 앱 코드가 나중에 바뀌어도 같은 결과여야 해서 값을 복사해 둔다.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def upgrade() -> None:
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('friend_code', sa.String(length=8), nullable=True))
    # 이미 가입한 사람에게도 친구 코드를 하나씩 준다 (겹치지 않게)
    connection = op.get_bind()
    taken: set[str] = set()
    for (user_id,) in connection.execute(sa.text("SELECT id FROM users")).fetchall():
        while True:
            code = "".join(secrets.choice(ALPHABET) for _ in range(8))
            if code not in taken:
                taken.add(code)
                break
        connection.execute(sa.text("UPDATE users SET friend_code = :code WHERE id = :id"), {"code": code, "id": user_id})
    with op.batch_alter_table('users') as batch:
        batch.alter_column('friend_code', existing_type=sa.String(length=8), nullable=False)
        batch.create_unique_constraint(op.f('uq_users_friend_code'), ['friend_code'])

    op.add_column('fridges', sa.Column('visibility', sa.String(length=10), nullable=False, server_default='private'))

    op.create_table('friendships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('requester_id', sa.Integer(), nullable=False),
    sa.Column('addressee_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('responded_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['addressee_id'], ['users.id'], name=op.f('fk_friendships_addressee_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requester_id'], ['users.id'], name=op.f('fk_friendships_requester_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_friendships')),
    sa.UniqueConstraint('requester_id', 'addressee_id', name='uq_friendships_pair')
    )
    op.create_index(op.f('ix_friendships_addressee_id'), 'friendships', ['addressee_id'], unique=False)
    op.create_index(op.f('ix_friendships_requester_id'), 'friendships', ['requester_id'], unique=False)

    op.create_table('posts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('author_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.Column('deleted_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_posts_author_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], name=op.f('fk_posts_deleted_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_posts'))
    )
    op.create_index(op.f('ix_posts_author_id'), 'posts', ['author_id'], unique=False)
    op.create_index('ix_posts_deleted_at_id', 'posts', ['deleted_at', 'id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_posts_deleted_at_id', table_name='posts')
    op.drop_index(op.f('ix_posts_author_id'), table_name='posts')
    op.drop_table('posts')
    op.drop_index(op.f('ix_friendships_requester_id'), table_name='friendships')
    op.drop_index(op.f('ix_friendships_addressee_id'), table_name='friendships')
    op.drop_table('friendships')
    op.drop_column('fridges', 'visibility')
    with op.batch_alter_table('users') as batch:
        batch.drop_constraint(op.f('uq_users_friend_code'), type_='unique')
        batch.drop_column('friend_code')
        batch.drop_column('is_admin')
