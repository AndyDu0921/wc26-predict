"""Add score-level fields to prediction_learning_log.

Revision ID: d4e5f6a7b8c9
Revises: c7d8e9f0a1b2
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCORE_COLUMNS = (
    sa.Column("score_log_loss", sa.Float(), nullable=True),
    sa.Column("score_exact_hit", sa.Boolean(), nullable=True),
    sa.Column("score_top3_hit", sa.Boolean(), nullable=True),
    sa.Column("dc_score_log_loss", sa.Float(), nullable=True),
    sa.Column("negbin_score_log_loss", sa.Float(), nullable=True),
    sa.Column("weibull_score_log_loss", sa.Float(), nullable=True),
)


def _existing_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns("prediction_learning_log")}


def upgrade() -> None:
    existing = _existing_columns()
    missing = [column.copy() for column in SCORE_COLUMNS if column.name not in existing]
    if not missing:
        return
    with op.batch_alter_table("prediction_learning_log") as batch_op:
        for column in missing:
            batch_op.add_column(column)


def downgrade() -> None:
    existing = _existing_columns()
    present = [column.name for column in SCORE_COLUMNS if column.name in existing]
    if not present:
        return
    with op.batch_alter_table("prediction_learning_log") as batch_op:
        for column_name in reversed(present):
            batch_op.drop_column(column_name)
