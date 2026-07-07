"""Add score matrix audit payloads to prediction_snapshots.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-06
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCORE_MATRIX_COLUMNS = (
    sa.Column("fused_score_matrix", sa.JSON(), nullable=True),
    sa.Column("source_score_matrices", sa.JSON(), nullable=True),
)


def _existing_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns("prediction_snapshots")}


def upgrade() -> None:
    existing = _existing_columns()
    missing = [column.copy() for column in SCORE_MATRIX_COLUMNS if column.name not in existing]
    if not missing:
        return
    with op.batch_alter_table("prediction_snapshots") as batch_op:
        for column in missing:
            batch_op.add_column(column)


def downgrade() -> None:
    existing = _existing_columns()
    present = [column.name for column in SCORE_MATRIX_COLUMNS if column.name in existing]
    if not present:
        return
    with op.batch_alter_table("prediction_snapshots") as batch_op:
        for column_name in reversed(present):
            batch_op.drop_column(column_name)
