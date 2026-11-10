"""Personal fridge groups (e.g. home, parents' place) for the fridge list and the statistics picker.

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fridge_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_fridge_groups_owner_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fridge_groups")),
    )
    op.create_index(op.f("ix_fridge_groups_owner_id"), "fridge_groups", ["owner_id"], unique=False)
    op.create_table(
        "fridge_group_items",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("fridge_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["fridge_id"], ["fridges.id"], name=op.f("fk_fridge_group_items_fridge_id_fridges"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["fridge_groups.id"], name=op.f("fk_fridge_group_items_group_id_fridge_groups"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "fridge_id", name=op.f("pk_fridge_group_items")),
    )
    op.create_index(op.f("ix_fridge_group_items_fridge_id"), "fridge_group_items", ["fridge_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_fridge_group_items_fridge_id"), table_name="fridge_group_items")
    op.drop_table("fridge_group_items")
    op.drop_index(op.f("ix_fridge_groups_owner_id"), table_name="fridge_groups")
    op.drop_table("fridge_groups")
