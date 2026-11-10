"""Account profile photo stored in the existing private uploads volume."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('users', sa.Column('avatar_path', sa.String(255), nullable=True))

def downgrade():
    op.drop_column('users', 'avatar_path')
