"""Private, idempotent recipe behavioral events."""
from alembic import op
import sqlalchemy as sa
revision = '0016'
down_revision = '0015'
branch_labels = depends_on = None


def upgrade():
    op.create_table('recipe_events', sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('recipe_id',sa.String(100),nullable=False),sa.Column('event_key',sa.String(100),nullable=False),
        sa.Column('event_type',sa.String(20),nullable=False),sa.Column('source',sa.String(30),nullable=False),
        sa.Column('rating',sa.Integer(),nullable=True),sa.Column('selected_at',sa.DateTime(),nullable=False),
        sa.UniqueConstraint('user_id','event_key',name='uq_recipe_events_user_key'))
    op.create_index('ix_recipe_events_user_recipe','recipe_events',['user_id','recipe_id','event_type'])


def downgrade():
    op.drop_index('ix_recipe_events_user_recipe',table_name='recipe_events')
    op.drop_table('recipe_events')
