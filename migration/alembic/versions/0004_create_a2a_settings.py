"""create a2a_settings table

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "a2a_settings",
        sa.Column("partition_key", sa.String(128), nullable=False),
        sa.Column("setting_id", sa.String(), nullable=False),
        sa.Column("endpoint_id", sa.String(), nullable=True),
        sa.Column("part_id", sa.String(), nullable=True),
        sa.Column("setting", postgresql.JSONB(), nullable=True),
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
        sa.PrimaryKeyConstraint("partition_key", "setting_id"),
    )


def downgrade() -> None:
    op.drop_table("a2a_settings")