"""Add closed-loop resolution ledger.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-06-13
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("closed_loop_resolution_ledger"):
        op.create_table(
            "closed_loop_resolution_ledger",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("entity_table", sa.String(64), nullable=False),
            sa.Column("entity_id", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("resolved_match_id", sa.String(36), nullable=True),
            sa.Column("resolved_prediction_run_id", sa.String(36), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("resolver_version", sa.String(32), nullable=False),
            sa.Column("source_payload", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "entity_table", "entity_id", name="uq_closed_loop_resolution_entity"
            ),
        )
    if not _has_index("closed_loop_resolution_ledger", "ix_closed_loop_resolution_entity"):
        op.create_index(
            "ix_closed_loop_resolution_entity",
            "closed_loop_resolution_ledger",
            ["entity_table", "entity_id"],
        )
    if not _has_index("closed_loop_resolution_ledger", "ix_closed_loop_resolution_status"):
        op.create_index("ix_closed_loop_resolution_status", "closed_loop_resolution_ledger", ["status"])
    if not _has_index("closed_loop_resolution_ledger", "ix_closed_loop_resolution_match"):
        op.create_index(
            "ix_closed_loop_resolution_match",
            "closed_loop_resolution_ledger",
            ["resolved_match_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_closed_loop_resolution_match", table_name="closed_loop_resolution_ledger")
    op.drop_index("ix_closed_loop_resolution_status", table_name="closed_loop_resolution_ledger")
    op.drop_index("ix_closed_loop_resolution_entity", table_name="closed_loop_resolution_ledger")
    op.drop_table("closed_loop_resolution_ledger")
