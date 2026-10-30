"""share links, view-only friends and change requests; the old friend-request flow is gone

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31 03:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 친구는 이제 공유 링크로만 맺는다. 답을 못 받은 예전 요청은 의미가 없어서 지운다.
    op.execute("DELETE FROM friendships WHERE status <> 'accepted'")
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
    sa.ForeignKeyConstraint(['fridge_id'], ['fridges.id'], name=op.f('fk_change_requests_fridge_id_fridges'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['handled_by'], ['users.id'], name=op.f('fk_change_requests_handled_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['food_items.id'], name=op.f('fk_change_requests_item_id_food_items'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['requester_id'], ['users.id'], name=op.f('fk_change_requests_requester_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_change_requests'))
    )
    op.create_index(op.f('ix_change_requests_requester_id'), 'change_requests', ['requester_id'], unique=False)
    op.create_index('ix_change_requests_fridge_id_status', 'change_requests', ['fridge_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_change_requests_fridge_id_status', table_name='change_requests')
    op.drop_index(op.f('ix_change_requests_requester_id'), table_name='change_requests')
    op.drop_table('change_requests')
    op.drop_index(op.f('ix_fridge_share_links_fridge_id'), table_name='fridge_share_links')
    op.drop_table('fridge_share_links')
    op.drop_index(op.f('ix_fridge_viewers_user_id'), table_name='fridge_viewers')
    op.drop_table('fridge_viewers')
