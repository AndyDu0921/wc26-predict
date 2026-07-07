"""Add V4.10 information-state evidence tables.

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-07
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(idx["name"] == index_name for idx in _inspector().get_indexes(table_name))


def _existing_columns(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {col["name"] for col in _inspector().get_columns(table_name)}


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def _add_missing_columns(table_name: str, columns: Sequence[sa.Column]) -> None:
    existing = _existing_columns(table_name)
    missing = [
        column.copy()
        for column in columns
        if isinstance(column, sa.Column) and column.name not in existing
    ]
    if not missing:
        return
    with op.batch_alter_table(table_name) as batch_op:
        for column in missing:
            batch_op.add_column(column)


def _create_table_if_missing(
    table_name: str,
    columns: Sequence[sa.Column],
    *constraints: sa.Constraint,
) -> None:
    if _has_table(table_name):
        _add_missing_columns(table_name, columns)
        return
    op.create_table(
        table_name,
        *_base_columns(),
        *columns,
        sa.PrimaryKeyConstraint("id"),
        *constraints,
    )


def _ensure_indexes(table_name: str, indexes: Sequence[tuple[str, Sequence[str], bool]]) -> None:
    for index_name, columns, unique in indexes:
        if not _has_index(table_name, index_name):
            op.create_index(index_name, table_name, list(columns), unique=unique)


EVIDENCE_COLUMNS = (
    sa.Column("evidence_key", sa.String(96), nullable=False),
    sa.Column("match_id", sa.String(64), nullable=True),
    sa.Column("home_team", sa.String(100), nullable=True),
    sa.Column("away_team", sa.String(100), nullable=True),
    sa.Column("evidence_type", sa.String(40), nullable=False),
    sa.Column("source_url", sa.Text(), nullable=False),
    sa.Column("source_name", sa.String(120), nullable=True),
    sa.Column("title", sa.Text(), nullable=True),
    sa.Column("content_excerpt", sa.Text(), nullable=True),
    sa.Column("raw_text_hash", sa.String(96), nullable=False),
    sa.Column("language", sa.String(16), nullable=True),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("reliability_score", sa.Float(), nullable=False, server_default="0.5"),
    sa.Column("metadata", sa.JSON(), nullable=True),
)

SIGNAL_COLUMNS = (
    sa.Column("signal_key", sa.String(96), nullable=False),
    sa.Column("match_id", sa.String(64), nullable=True),
    sa.Column("team", sa.String(100), nullable=False),
    sa.Column("player", sa.String(100), nullable=True),
    sa.Column("signal_type", sa.String(40), nullable=False),
    sa.Column("direction", sa.String(16), nullable=False),
    sa.Column("magnitude", sa.Float(), nullable=False, server_default="0"),
    sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
    sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("evidence_ids", sa.JSON(), nullable=False),
    sa.Column("status", sa.String(32), nullable=False, server_default="shadow"),
    sa.Column("source_status", sa.String(32), nullable=False, server_default="used_pre_match"),
    sa.Column("shadow_adjustment", sa.JSON(), nullable=True),
    sa.Column("summary", sa.Text(), nullable=True),
    sa.Column("metadata", sa.JSON(), nullable=True),
)

EVALUATION_COLUMNS = (
    sa.Column("evaluation_key", sa.String(120), nullable=False),
    sa.Column("match_id", sa.String(64), nullable=False),
    sa.Column("prediction_run_id", sa.String(64), nullable=True),
    sa.Column("signal_id", sa.String(64), nullable=False),
    sa.Column("actual_outcome", sa.String(16), nullable=False),
    sa.Column("verdict", sa.String(24), nullable=False),
    sa.Column("contribution_score", sa.Float(), nullable=False, server_default="0"),
    sa.Column("notes", sa.Text(), nullable=True),
    sa.Column("metrics", sa.JSON(), nullable=True),
)


def upgrade() -> None:
    _create_table_if_missing(
        "evidence_items",
        EVIDENCE_COLUMNS,
        sa.UniqueConstraint("evidence_key", name="uq_evidence_items_evidence_key"),
    )
    _ensure_indexes(
        "evidence_items",
        (
            ("ix_evidence_items_evidence_key", ("evidence_key",), True),
            ("ix_evidence_items_match_id", ("match_id",), False),
            ("ix_evidence_items_evidence_type", ("evidence_type",), False),
            ("ix_evidence_items_available_at", ("available_at",), False),
            ("ix_evidence_items_raw_text_hash", ("raw_text_hash",), False),
        ),
    )

    _create_table_if_missing(
        "information_state_signals",
        SIGNAL_COLUMNS,
        sa.UniqueConstraint("signal_key", name="uq_information_state_signals_signal_key"),
    )
    _ensure_indexes(
        "information_state_signals",
        (
            ("ix_information_state_signals_signal_key", ("signal_key",), True),
            ("ix_information_state_signals_match_id", ("match_id",), False),
            ("ix_information_state_signals_team", ("team",), False),
            ("ix_information_state_signals_type", ("signal_type",), False),
            ("ix_information_state_signals_status", ("status",), False),
            ("ix_information_state_signals_available_at", ("available_at",), False),
        ),
    )

    _create_table_if_missing(
        "signal_evaluations",
        EVALUATION_COLUMNS,
        sa.UniqueConstraint("evaluation_key", name="uq_signal_evaluations_evaluation_key"),
    )
    _ensure_indexes(
        "signal_evaluations",
        (
            ("ix_signal_evaluations_evaluation_key", ("evaluation_key",), True),
            ("ix_signal_evaluations_match_id", ("match_id",), False),
            ("ix_signal_evaluations_signal_id", ("signal_id",), False),
            ("ix_signal_evaluations_verdict", ("verdict",), False),
        ),
    )


def downgrade() -> None:
    for table_name, indexes in (
        (
            "signal_evaluations",
            (
                "ix_signal_evaluations_verdict",
                "ix_signal_evaluations_signal_id",
                "ix_signal_evaluations_match_id",
                "ix_signal_evaluations_evaluation_key",
            ),
        ),
        (
            "information_state_signals",
            (
                "ix_information_state_signals_available_at",
                "ix_information_state_signals_status",
                "ix_information_state_signals_type",
                "ix_information_state_signals_team",
                "ix_information_state_signals_match_id",
                "ix_information_state_signals_signal_key",
            ),
        ),
        (
            "evidence_items",
            (
                "ix_evidence_items_raw_text_hash",
                "ix_evidence_items_available_at",
                "ix_evidence_items_evidence_type",
                "ix_evidence_items_match_id",
                "ix_evidence_items_evidence_key",
            ),
        ),
    ):
        if _has_table(table_name):
            for index_name in indexes:
                if _has_index(table_name, index_name):
                    op.drop_index(index_name, table_name=table_name)
            op.drop_table(table_name)
