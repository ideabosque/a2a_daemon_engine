"""add context_id and role columns to a2a_messages

Phase 12: Conversation grouping via contextId. Adds context_id (conversation
group key, matching the A2A protocol's contextId) and role (user/agent) to
the a2a_messages table, plus an index for partition + context_id queries.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-29
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("a2a_messages", sa.Column("context_id", sa.String(), nullable=True))
    op.add_column("a2a_messages", sa.Column("role", sa.String(), nullable=True))
    op.create_index(
        "idx_a2a_messages_partition_context",
        "a2a_messages",
        ["partition_key", "context_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_a2a_messages_partition_context", table_name="a2a_messages")
    op.drop_column("a2a_messages", "role")
    op.drop_column("a2a_messages", "context_id")
