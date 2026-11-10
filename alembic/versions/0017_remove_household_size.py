"""Remove household settings after the API no longer reads them.

For an existing 0016 deployment, roll out the removal API first, then run this
migration. Do not route traffic back to an API that still maps household_size.
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("ck_users_household_size", "users", type_="check")
    op.drop_column("users", "household_size")


def downgrade():
    op.add_column("users", sa.Column("household_size", sa.Integer(), nullable=True))
    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint("ck_users_household_size", "users", "household_size IS NULL OR (household_size BETWEEN 1 AND 20)")
