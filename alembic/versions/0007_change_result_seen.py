"""change requests remember whether the requester has seen the approve/decline result

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31 15:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('change_requests', sa.Column('result_seen_at', sa.DateTime(), nullable=True))
    # 이미 처리된 요청은 본 것으로 시작한다 (알림 숫자가 처음 켜질 때 옛 결과가 한꺼번에 새 알림으로 뜨지 않게)
    op.execute(
        "UPDATE change_requests SET result_seen_at = COALESCE(handled_at, created_at) "
        "WHERE status IN ('approved', 'declined')"
    )


def downgrade() -> None:
    op.drop_column('change_requests', 'result_seen_at')
