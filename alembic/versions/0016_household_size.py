"""Private household size for recipe portions and recommendations."""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("household_size", sa.Integer(), nullable=True))
    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint("ck_users_household_size", "users", "household_size IS NULL OR (household_size BETWEEN 1 AND 20)")

def downgrade():
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("ck_users_household_size", "users", type_="check")
    op.drop_column("users", "household_size")
