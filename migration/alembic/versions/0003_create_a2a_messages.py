"""create a2a_messages table

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "a2a_messages",
        sa.Column("partition_key", sa.String(128), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("endpoint_id", sa.String(), nullable=True),
        sa.Column("part_id", sa.String(), nullable=True),
        sa.Column("from_agent_id", sa.String(), nullable=True),
        sa.Column("to_agent_id", sa.String(), nullable=True),
        sa.Column("message_type", sa.String(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("partition_key", "message_id"),
    )
    op.create_index(
        "idx_a2a_messages_partition_status",
        "a2a_messages",
        ["partition_key", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_a2a_messages_partition_status", table_name="a2a_messages")
    op.drop_table("a2a_messages")