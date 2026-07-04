"""Add V4.8 accuracy-engine audit tables.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-04
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    return any(idx["name"] == index_name for idx in _inspector().get_indexes(table_name))


def _existing_columns(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {col["name"] for col in _inspector().get_columns(table_name)}


def _add_missing_columns(table_name: str, columns: Sequence[sa.Column]) -> None:
    existing = _existing_columns(table_name)
    missing = [col.copy() for col in columns if col.name not in existing]
    if not missing:
        return
    with op.batch_alter_table(table_name) as batch_op:
        for column in missing:
            batch_op.add_column(column)


FEATURE_SNAPSHOT_COLUMNS = (
    sa.Column("sample_id", sa.String(64), nullable=False),
    sa.Column("match_id", sa.String(64), nullable=True),
    sa.Column("source", sa.String(80), nullable=False, server_default="evaluation_registry"),
    sa.Column("as_of_time", sa.DateTime(timezone=True), nullable=True),
    sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("horizon_hours", sa.Float(), nullable=True),
    sa.Column("feature_hash", sa.String(96), nullable=False),
    sa.Column("payload", sa.JSON(), nullable=False),
    sa.Column("data_availability", sa.JSON(), nullable=False),
    sa.Column("leakage_status", sa.String(40), nullable=False),
)

EXPERIMENT_RUN_COLUMNS = (
    sa.Column("experiment_id", sa.String(64), nullable=False),
    sa.Column("candidate_name", sa.String(80), nullable=False),
    sa.Column("champion_name", sa.String(80), nullable=False, server_default="current_fusion"),
    sa.Column("sample_registry_hash", sa.String(96), nullable=False),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("n_samples", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("metrics_current", sa.JSON(), nullable=True),
    sa.Column("metrics_candidate", sa.JSON(), nullable=True),
    sa.Column("paired_deltas", sa.JSON(), nullable=True),
    sa.Column("group_metrics", sa.JSON(), nullable=True),
    sa.Column("leakage_checks", sa.JSON(), nullable=True),
    sa.Column("gate_decision", sa.JSON(), nullable=True),
    sa.Column("notes", sa.Text(), nullable=True),
)

CANDIDATE_PREDICTION_COLUMNS = (
    sa.Column("experiment_id", sa.String(64), nullable=False),
    sa.Column("sample_id", sa.String(64), nullable=False),
    sa.Column("candidate_name", sa.String(80), nullable=False),
    sa.Column("actual_result", sa.String(10), nullable=True),
    sa.Column("current_probs", sa.JSON(), nullable=False),
    sa.Column("candidate_probs", sa.JSON(), nullable=False),
    sa.Column("component_payload", sa.JSON(), nullable=True),
)

MODEL_CHANGE_PROPOSAL_COLUMNS = (
    sa.Column("proposal_type", sa.String(40), nullable=False),
    sa.Column("candidate_name", sa.String(80), nullable=False),
    sa.Column("source", sa.String(80), nullable=False, server_default="self_evolution"),
    sa.Column("status", sa.String(40), nullable=False, server_default="proposal_pending"),
    sa.Column("sample_registry_hash", sa.String(96), nullable=True),
    sa.Column("target_table", sa.String(80), nullable=True),
    sa.Column("target_key", sa.String(120), nullable=True),
    sa.Column("base_payload", sa.JSON(), nullable=True),
    sa.Column("candidate_payload", sa.JSON(), nullable=False),
    sa.Column("metrics", sa.JSON(), nullable=True),
    sa.Column("gate_decision", sa.JSON(), nullable=False),
    sa.Column("evidence", sa.JSON(), nullable=True),
    sa.Column("notes", sa.Text(), nullable=True),
)


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
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


def _create_table_if_missing(table_name: str, columns: Sequence[sa.Column], *constraints: sa.Constraint) -> None:
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


def upgrade() -> None:
    _create_table_if_missing("feature_snapshots", FEATURE_SNAPSHOT_COLUMNS)
    _ensure_indexes(
        "feature_snapshots",
        (
            ("ix_feature_snapshots_sample_id", ("sample_id",), False),
            ("ix_feature_snapshots_match_id", ("match_id",), False),
            ("ix_feature_snapshots_feature_hash", ("feature_hash",), False),
            ("ix_feature_snapshots_leakage_status", ("leakage_status",), False),
        ),
    )

    _create_table_if_missing(
        "experiment_runs",
        EXPERIMENT_RUN_COLUMNS,
        sa.UniqueConstraint("experiment_id", name="uq_experiment_runs_experiment_id"),
    )
    _ensure_indexes(
        "experiment_runs",
        (
            ("ix_experiment_runs_experiment_id", ("experiment_id",), True),
            ("ix_experiment_runs_candidate_name", ("candidate_name",), False),
            ("ix_experiment_runs_sample_registry_hash", ("sample_registry_hash",), False),
            ("ix_experiment_runs_status", ("status",), False),
        ),
    )

    _create_table_if_missing("candidate_predictions", CANDIDATE_PREDICTION_COLUMNS)
    _ensure_indexes(
        "candidate_predictions",
        (
            ("ix_candidate_predictions_experiment_id", ("experiment_id",), False),
            ("ix_candidate_predictions_sample_id", ("sample_id",), False),
            ("ix_candidate_predictions_candidate_name", ("candidate_name",), False),
        ),
    )

    _create_table_if_missing("model_change_proposals", MODEL_CHANGE_PROPOSAL_COLUMNS)
    _ensure_indexes(
        "model_change_proposals",
        (
            ("ix_model_change_proposals_proposal_type", ("proposal_type",), False),
            ("ix_model_change_proposals_candidate_name", ("candidate_name",), False),
            ("ix_model_change_proposals_status", ("status",), False),
            ("ix_model_change_proposals_sample_registry_hash", ("sample_registry_hash",), False),
        ),
    )


def downgrade() -> None:
    for table_name, index_names in (
        (
            "model_change_proposals",
            (
                "ix_model_change_proposals_sample_registry_hash",
                "ix_model_change_proposals_status",
                "ix_model_change_proposals_candidate_name",
                "ix_model_change_proposals_proposal_type",
            ),
        ),
        (
            "candidate_predictions",
            (
                "ix_candidate_predictions_candidate_name",
                "ix_candidate_predictions_sample_id",
                "ix_candidate_predictions_experiment_id",
            ),
        ),
        (
            "experiment_runs",
            (
                "ix_experiment_runs_status",
                "ix_experiment_runs_sample_registry_hash",
                "ix_experiment_runs_candidate_name",
                "ix_experiment_runs_experiment_id",
            ),
        ),
        (
            "feature_snapshots",
            (
                "ix_feature_snapshots_leakage_status",
                "ix_feature_snapshots_feature_hash",
                "ix_feature_snapshots_match_id",
                "ix_feature_snapshots_sample_id",
            ),
        ),
    ):
        if _has_table(table_name):
            for index_name in index_names:
                if _has_index(table_name, index_name):
                    op.drop_index(index_name, table_name=table_name)
            op.drop_table(table_name)
