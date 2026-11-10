"""Opt-in push devices and durable delivery deduplication.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("push_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(10), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("credential", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("expiry", sa.Boolean(), nullable=False),
        sa.Column("comments", sa.Boolean(), nullable=False),
        sa.Column("comments_after_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_push_devices_user_id", "push_devices", ["user_id"])
    op.create_table("push_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("push_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_key", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("ticket", sa.String(100), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("device_id", "event_key", name="uq_push_delivery_event"))
    op.create_index("ix_push_delivery_pending", "push_deliveries", ["status", "next_attempt_at"])


def downgrade():
    op.drop_table("push_deliveries")
    op.drop_table("push_devices")
