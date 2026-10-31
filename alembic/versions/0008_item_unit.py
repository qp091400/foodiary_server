"""food items and receipt lines remember the unit of their quantity (개 / g / kg / ml / L)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-01 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 지금까지 들어온 수량은 모두 개수였다.
    op.add_column('food_items', sa.Column('unit', sa.String(length=4), nullable=False, server_default='개'))
    op.add_column('receipt_items', sa.Column('unit', sa.String(length=4), nullable=False, server_default='개'))


def downgrade() -> None:
    op.drop_column('receipt_items', 'unit')
    op.drop_column('food_items', 'unit')
