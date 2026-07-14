"""create a2a_tasks table

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "a2a_tasks",
        sa.Column("partition_key", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("endpoint_id", sa.String(), nullable=True),
        sa.Column("part_id", sa.String(), nullable=True),
        sa.Column("task_type", sa.String(), nullable=True),
        sa.Column("assigned_agent_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("priority", sa.String(), nullable=True),
        sa.Column("input_data", postgresql.JSONB(), nullable=True),
        sa.Column("output_data", postgresql.JSONB(), nullable=True),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("context_id", sa.String(), nullable=True),
        sa.Column("created_at_sdk", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_modified", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("partition_key", "task_id"),
    )
    op.create_index(
        "idx_a2a_tasks_partition_status",
        "a2a_tasks",
        ["partition_key", "status"],
    )
    op.create_index(
        "idx_a2a_tasks_partition_priority",
        "a2a_tasks",
        ["partition_key", "priority"],
    )


def downgrade() -> None:
    op.drop_index("idx_a2a_tasks_partition_priority", table_name="a2a_tasks")
    op.drop_index("idx_a2a_tasks_partition_status", table_name="a2a_tasks")
    op.drop_table("a2a_tasks")