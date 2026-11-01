"""Mark accounts made by the test-account button.

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 새 칸 하나만 더한다. 기존 계정은 모두 false(테스트 계정 아님)다.
    op.add_column("users", sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_test")
