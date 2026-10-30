"""several fridges per person: access blocks, derived grants, fridge groups and group sharing

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31 05:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('fridge_members', sa.Column('derived', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('fridge_viewers', sa.Column('derived', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('fridge_access_blocks',
    sa.Column('fridge_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['fridge_id'], ['fridges.id'], name=op.f('fk_fridge_access_blocks_fridge_id_fridges'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_fridge_access_blocks_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('fridge_id', 'user_id', name=op.f('pk_fridge_access_blocks'))
    )
    op.create_index(op.f('ix_fridge_access_blocks_user_id'), 'fridge_access_blocks', ['user_id'], unique=False)
    op.create_table('fridge_groups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_fridge_groups_owner_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fridge_groups'))
    )
    op.create_index(op.f('ix_fridge_groups_owner_id'), 'fridge_groups', ['owner_id'], unique=False)
    op.create_table('fridge_group_items',
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('fridge_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['fridge_id'], ['fridges.id'], name=op.f('fk_fridge_group_items_fridge_id_fridges'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['group_id'], ['fridge_groups.id'], name=op.f('fk_fridge_group_items_group_id_fridge_groups'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('group_id', 'fridge_id', name=op.f('pk_fridge_group_items'))
    )
    op.create_index(op.f('ix_fridge_group_items_fridge_id'), 'fridge_group_items', ['fridge_id'], unique=False)
    op.create_table('fridge_group_shares',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('permission', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['fridge_groups.id'], name=op.f('fk_fridge_group_shares_group_id_fridge_groups'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_fridge_group_shares_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fridge_group_shares')),
    sa.UniqueConstraint('group_id', 'user_id', name='uq_fridge_group_shares_group_user')
    )
    op.create_index(op.f('ix_fridge_group_shares_group_id'), 'fridge_group_shares', ['group_id'], unique=False)
    op.create_index(op.f('ix_fridge_group_shares_user_id'), 'fridge_group_shares', ['user_id'], unique=False)
    op.create_table('fridge_group_links',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=32), nullable=False),
    sa.Column('permission', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('max_uses', sa.Integer(), nullable=True),
    sa.Column('use_count', sa.Integer(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['group_id'], ['fridge_groups.id'], name=op.f('fk_fridge_group_links_group_id_fridge_groups'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fridge_group_links')),
    sa.UniqueConstraint('token', name=op.f('uq_fridge_group_links_token'))
    )
    op.create_index(op.f('ix_fridge_group_links_group_id'), 'fridge_group_links', ['group_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_fridge_group_links_group_id'), table_name='fridge_group_links')
    op.drop_table('fridge_group_links')
    op.drop_index(op.f('ix_fridge_group_shares_user_id'), table_name='fridge_group_shares')
    op.drop_index(op.f('ix_fridge_group_shares_group_id'), table_name='fridge_group_shares')
    op.drop_table('fridge_group_shares')
    op.drop_index(op.f('ix_fridge_group_items_fridge_id'), table_name='fridge_group_items')
    op.drop_table('fridge_group_items')
    op.drop_index(op.f('ix_fridge_groups_owner_id'), table_name='fridge_groups')
    op.drop_table('fridge_groups')
    op.drop_index(op.f('ix_fridge_access_blocks_user_id'), table_name='fridge_access_blocks')
    op.drop_table('fridge_access_blocks')
    # 묶음에서 받은 권한(derived)은 이 열이 없어지면 보통 권한과 구별할 수 없으므로 먼저 지운다
    op.execute("DELETE FROM fridge_viewers WHERE derived")
    op.execute("DELETE FROM fridge_members WHERE derived")
    op.drop_column('fridge_viewers', 'derived')
    op.drop_column('fridge_members', 'derived')
