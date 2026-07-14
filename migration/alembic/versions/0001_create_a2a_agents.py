"""create a2a_agents table

Revision ID: 0001
Revises:
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "a2a_agents",
        sa.Column("partition_key", sa.String(128), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("endpoint_id", sa.String(), nullable=True),
        sa.Column("part_id", sa.String(), nullable=True),
        sa.Column("agent_name", sa.String(), nullable=True),
        sa.Column("capabilities", sa.Text(), nullable=True),
        sa.Column("endpoint_url", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
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
        sa.PrimaryKeyConstraint("partition_key", "agent_id"),
    )
    op.create_index(
        "idx_a2a_agents_partition_status",
        "a2a_agents",
        ["partition_key", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_a2a_agents_partition_status", table_name="a2a_agents")
    op.drop_table("a2a_agents")