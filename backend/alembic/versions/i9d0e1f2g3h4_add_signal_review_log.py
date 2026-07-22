"""Add the transactional signal review audit ledger.

Revision ID: i9d0e1f2g3h4
Revises: h8c9d0e1f2g3
Create Date: 2026-07-18
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i9d0e1f2g3h4"
down_revision: Union[str, Sequence[str], None] = "h8c9d0e1f2g3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("signal_review_log"):
        op.create_table(
            "signal_review_log",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("signal_id", sa.Uuid(), nullable=False),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("previous_status", sa.String(length=20), nullable=True),
            sa.Column("new_status", sa.String(length=20), nullable=False),
            sa.Column("reviewer", sa.String(length=50), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["signal_id"],
                ["news_signals.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _has_index("signal_review_log", "ix_signal_review_log_signal_id"):
        op.create_index(
            "ix_signal_review_log_signal_id",
            "signal_review_log",
            ["signal_id"],
            unique=False,
        )
    if not _has_index("signal_review_log", "ix_signal_review_log_created_at"):
        op.create_index(
            "ix_signal_review_log_created_at",
            "signal_review_log",
            ["created_at"],
            unique=False,
        )


def downgrade() -> None:
    if not _has_table("signal_review_log"):
        return
    if _has_index("signal_review_log", "ix_signal_review_log_created_at"):
        op.drop_index("ix_signal_review_log_created_at", table_name="signal_review_log")
    if _has_index("signal_review_log", "ix_signal_review_log_signal_id"):
        op.drop_index("ix_signal_review_log_signal_id", table_name="signal_review_log")
    op.drop_table("signal_review_log")
