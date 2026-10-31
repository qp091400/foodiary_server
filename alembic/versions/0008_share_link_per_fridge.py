"""one fixed share link per fridge; friends, view-only access, change requests and invite codes are gone

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31 18:00:00
"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 0002와 같다. 되돌릴 때 친구 코드를 다시 만들어 준다.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def upgrade() -> None:
    # 친구, 보기만 하는 권한, 변경 요청, 예전 공유 링크와 초대코드를 지운다. 고칠 수 있는 멤버(fridge_members)는 그대로 남는다.
    op.drop_index('ix_change_requests_fridge_id_status', table_name='change_requests')
    op.drop_index(op.f('ix_change_requests_requester_id'), table_name='change_requests')
    op.drop_table('change_requests')
    op.drop_index(op.f('ix_fridge_share_links_fridge_id'), table_name='fridge_share_links')
    op.drop_table('fridge_share_links')
    op.drop_index(op.f('ix_fridge_viewers_user_id'), table_name='fridge_viewers')
    op.drop_table('fridge_viewers')
    op.drop_index(op.f('ix_friendships_requester_id'), table_name='friendships')
    op.drop_index(op.f('ix_friendships_addressee_id'), table_name='friendships')
    op.drop_table('friendships')
    op.drop_index(op.f('ix_fridge_invites_fridge_id'), table_name='fridge_invites')
    op.drop_table('fridge_invites')
    with op.batch_alter_table('users') as batch:
        batch.drop_constraint(op.f('uq_users_friend_code'), type_='unique')
        batch.drop_column('friend_code')
    # '친구에게' 공개는 없어졌다. 더 넓게 열리지 않도록 '나만'으로 바꾼다.
    op.execute("UPDATE fridges SET visibility = 'private' WHERE visibility = 'friends'")
    with op.batch_alter_table('fridges') as batch:
        batch.add_column(sa.Column('share_token', sa.String(length=32), nullable=True))
        batch.create_unique_constraint(op.f('uq_fridges_share_token'), ['share_token'])


def downgrade() -> None:
    # 표와 칸만 되살린다. 지운 친구 관계, 보기 권한, 변경 요청, '친구에게' 공개 설정은 돌아오지 않는다.
    with op.batch_alter_table('fridges') as batch:
        batch.drop_constraint(op.f('uq_fridges_share_token'), type_='unique')
        batch.drop_column('share_token')

    op.add_column('users', sa.Column('friend_code', sa.String(length=8), nullable=True))
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

    op.create_table('fridge_invites',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fridge_id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=8), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_fridge_invites_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['fridge_id'], ['fridges.id'], name=op.f('fk_fridge_invites_fridge_id_fridges'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fridge_invites')),
    sa.UniqueConstraint('code', name=op.f('uq_fridge_invites_code'))
    )
    op.create_index(op.f('ix_fridge_invites_fridge_id'), 'fridge_invites', ['fridge_id'], unique=False)
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
    op.create_table('fridge_viewers',
    sa.Column('fridge_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['fridge_id'], ['fridges.id'], name=op.f('fk_fridge_viewers_fridge_id_fridges'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_fridge_viewers_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('fridge_id', 'user_id', name=op.f('pk_fridge_viewers'))
    )
    op.create_index(op.f('ix_fridge_viewers_user_id'), 'fridge_viewers', ['user_id'], unique=False)
    op.create_table('fridge_share_links',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fridge_id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=32), nullable=False),
    sa.Column('permission', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('max_uses', sa.Integer(), nullable=True),
    sa.Column('use_count', sa.Integer(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['fridge_id'], ['fridges.id'], name=op.f('fk_fridge_share_links_fridge_id_fridges'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fridge_share_links')),
    sa.UniqueConstraint('token', name=op.f('uq_fridge_share_links_token'))
    )
    op.create_index(op.f('ix_fridge_share_links_fridge_id'), 'fridge_share_links', ['fridge_id'], unique=False)
    op.create_table('change_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fridge_id', sa.Integer(), nullable=False),
    sa.Column('requester_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=True),
    sa.Column('item_name', sa.String(length=200), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('handled_by', sa.Integer(), nullable=True),
    sa.Column('handled_at', sa.DateTime(), nullable=True),
    sa.Column('result_seen_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['fridge_id'], ['fridges.id'], name=op.f('fk_change_requests_fridge_id_fridges'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['handled_by'], ['users.id'], name=op.f('fk_change_requests_handled_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['food_items.id'], name=op.f('fk_change_requests_item_id_food_items'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['requester_id'], ['users.id'], name=op.f('fk_change_requests_requester_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_change_requests'))
    )
    op.create_index(op.f('ix_change_requests_requester_id'), 'change_requests', ['requester_id'], unique=False)
    op.create_index('ix_change_requests_fridge_id_status', 'change_requests', ['fridge_id', 'status'], unique=False)
