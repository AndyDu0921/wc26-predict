"""Add V4.11 Match Data OS tables.

Revision ID: h8c9d0e1f2g3
Revises: g7b8c9d0e1f2
Create Date: 2026-07-08
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h8c9d0e1f2g3"
down_revision: Union[str, Sequence[str], None] = "g7b8c9d0e1f2"
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
    op.create_table(table_name, *columns, *constraints)


def _ensure_indexes(table_name: str, indexes: Sequence[tuple[str, Sequence[str], bool]]) -> None:
    for index_name, columns, unique in indexes:
        if not _has_index(table_name, index_name):
            op.create_index(index_name, table_name, list(columns), unique=unique)


def _base_text_pk_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    _create_table_if_missing(
        "match_data_raw",
        [
            *_base_text_pk_columns(),
            sa.Column("match_id", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("provider_match_id", sa.String(96), nullable=True),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("payload_hash", sa.String(96), nullable=False),
            sa.Column("content_type", sa.String(120), nullable=True),
            sa.Column("parser_version", sa.String(32), nullable=False, server_default="v4.11"),
            sa.Column("status", sa.String(32), nullable=False, server_default="fetched"),
            sa.Column("data_scope", sa.String(32), nullable=False, server_default="postmatch"),
            sa.Column("notes", sa.Text(), nullable=True),
        ],
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "provider", "payload_hash", name="uq_match_data_raw_hash"),
    )
    _ensure_indexes(
        "match_data_raw",
        (
            ("ix_match_data_raw_match", ("match_id",), False),
            ("ix_match_data_raw_provider", ("provider",), False),
            ("ix_match_data_raw_hash", ("payload_hash",), False),
        ),
    )

    _create_table_if_missing(
        "match_events",
        [
            *_base_text_pk_columns(),
            sa.Column("event_key", sa.String(96), nullable=False),
            sa.Column("match_id", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("provider_event_id", sa.String(96), nullable=True),
            sa.Column("minute", sa.Integer(), nullable=True),
            sa.Column("stoppage_minute", sa.Integer(), nullable=True),
            sa.Column("period", sa.String(40), nullable=True),
            sa.Column("team_name", sa.String(120), nullable=True),
            sa.Column("side", sa.String(16), nullable=True),
            sa.Column("player_name", sa.String(120), nullable=True),
            sa.Column("related_player_name", sa.String(120), nullable=True),
            sa.Column("event_type", sa.String(40), nullable=False),
            sa.Column("outcome", sa.String(60), nullable=True),
            sa.Column("xg", sa.Float(), nullable=True),
            sa.Column("home_score_after", sa.Integer(), nullable=True),
            sa.Column("away_score_after", sa.Integer(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
        ],
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_match_events_event_key"),
    )
    _ensure_indexes(
        "match_events",
        (
            ("ix_match_events_match", ("match_id",), False),
            ("ix_match_events_type", ("event_type",), False),
            ("ix_match_events_minute", ("match_id", "minute"), False),
        ),
    )

    _create_table_if_missing(
        "shot_events",
        [
            *_base_text_pk_columns(),
            sa.Column("shot_key", sa.String(96), nullable=False),
            sa.Column("event_id", sa.String(64), nullable=True),
            sa.Column("match_id", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("minute", sa.Integer(), nullable=True),
            sa.Column("stoppage_minute", sa.Integer(), nullable=True),
            sa.Column("period", sa.String(40), nullable=True),
            sa.Column("team_name", sa.String(120), nullable=True),
            sa.Column("side", sa.String(16), nullable=True),
            sa.Column("player_name", sa.String(120), nullable=True),
            sa.Column("xg", sa.Float(), nullable=True),
            sa.Column("outcome", sa.String(60), nullable=True),
            sa.Column("body_part", sa.String(60), nullable=True),
            sa.Column("shot_type", sa.String(60), nullable=True),
            sa.Column("assist_player", sa.String(120), nullable=True),
            sa.Column("home_score_after", sa.Integer(), nullable=True),
            sa.Column("away_score_after", sa.Integer(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
        ],
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shot_key", name="uq_shot_events_shot_key"),
    )
    _ensure_indexes(
        "shot_events",
        (
            ("ix_shot_events_match", ("match_id",), False),
            ("ix_shot_events_minute", ("match_id", "minute"), False),
        ),
    )

    _create_table_if_missing(
        "match_lineups",
        [
            *_base_text_pk_columns(),
            sa.Column("lineup_key", sa.String(96), nullable=False),
            sa.Column("match_id", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("team_name", sa.String(120), nullable=True),
            sa.Column("side", sa.String(16), nullable=True),
            sa.Column("player_id", sa.String(96), nullable=True),
            sa.Column("player_name", sa.String(120), nullable=False),
            sa.Column("position", sa.String(40), nullable=True),
            sa.Column("shirt_number", sa.Integer(), nullable=True),
            sa.Column("is_starting", sa.Boolean(), nullable=True),
            sa.Column("is_captain", sa.Boolean(), nullable=True),
            sa.Column("is_goalkeeper", sa.Boolean(), nullable=True),
            sa.Column("minute_on", sa.Integer(), nullable=True),
            sa.Column("minute_off", sa.Integer(), nullable=True),
            sa.Column("minutes_played", sa.Integer(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
        ],
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lineup_key", name="uq_match_lineups_lineup_key"),
    )
    _ensure_indexes(
        "match_lineups",
        (
            ("ix_match_lineups_match", ("match_id",), False),
            ("ix_match_lineups_player", ("player_name",), False),
        ),
    )

    _create_table_if_missing(
        "player_match_minutes",
        [
            *_base_text_pk_columns(),
            sa.Column("minute_key", sa.String(96), nullable=False),
            sa.Column("match_id", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("team_name", sa.String(120), nullable=True),
            sa.Column("side", sa.String(16), nullable=True),
            sa.Column("player_id", sa.String(96), nullable=True),
            sa.Column("player_name", sa.String(120), nullable=False),
            sa.Column("minute_on", sa.Integer(), nullable=True),
            sa.Column("minute_off", sa.Integer(), nullable=True),
            sa.Column("minutes_played", sa.Integer(), nullable=True),
            sa.Column("source_lineup_id", sa.String(64), nullable=True),
        ],
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("minute_key", name="uq_player_match_minutes_minute_key"),
    )
    _ensure_indexes(
        "player_match_minutes",
        (("ix_player_match_minutes_match", ("match_id",), False),),
    )

    _create_table_if_missing(
        "match_player_statistics",
        [
            *_base_text_pk_columns(),
            sa.Column("stats_key", sa.String(96), nullable=False),
            sa.Column("match_id", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("team_name", sa.String(120), nullable=True),
            sa.Column("side", sa.String(16), nullable=True),
            sa.Column("player_id", sa.String(96), nullable=True),
            sa.Column("player_name", sa.String(120), nullable=False),
            sa.Column("minutes_played", sa.Integer(), nullable=True),
            sa.Column("goals", sa.Integer(), nullable=True),
            sa.Column("assists", sa.Integer(), nullable=True),
            sa.Column("shots", sa.Integer(), nullable=True),
            sa.Column("xg", sa.Float(), nullable=True),
            sa.Column("passes_attempted", sa.Integer(), nullable=True),
            sa.Column("pass_accuracy_pct", sa.Float(), nullable=True),
            sa.Column("tackles", sa.Integer(), nullable=True),
            sa.Column("saves", sa.Integer(), nullable=True),
            sa.Column("stats_json", sa.Text(), nullable=True),
        ],
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stats_key", name="uq_match_player_statistics_stats_key"),
    )
    _ensure_indexes(
        "match_player_statistics",
        (("ix_match_player_statistics_match", ("match_id",), False),),
    )

    _create_table_if_missing(
        "match_game_state_segments",
        [
            *_base_text_pk_columns(),
            sa.Column("segment_key", sa.String(96), nullable=False),
            sa.Column("match_id", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False, server_default="derived"),
            sa.Column("period", sa.String(40), nullable=True),
            sa.Column("minute_start", sa.Integer(), nullable=False),
            sa.Column("minute_end", sa.Integer(), nullable=False),
            sa.Column("home_score_start", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("away_score_start", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("home_score_end", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("away_score_end", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("leader_start", sa.String(16), nullable=True),
            sa.Column("leader_end", sa.String(16), nullable=True),
            sa.Column("home_events_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("away_events_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("home_shots", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("away_shots", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("home_xg", sa.Float(), nullable=True),
            sa.Column("away_xg", sa.Float(), nullable=True),
            sa.Column("cards_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("substitutions_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("state_json", sa.Text(), nullable=True),
        ],
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_key", name="uq_match_game_state_segments_segment_key"),
    )
    _ensure_indexes(
        "match_game_state_segments",
        (("ix_match_game_state_segments_match", ("match_id",), False),),
    )

    if _has_table("prediction_learning_log"):
        _add_missing_columns(
            "prediction_learning_log",
            [
                sa.Column("game_state_profile", sa.JSON(), nullable=True),
                sa.Column("comeback_profile", sa.JSON(), nullable=True),
                sa.Column("event_quality_score", sa.Float(), nullable=True),
            ],
        )


def downgrade() -> None:
    if _has_table("prediction_learning_log"):
        existing = _existing_columns("prediction_learning_log")
        with op.batch_alter_table("prediction_learning_log") as batch_op:
            for column_name in ("event_quality_score", "comeback_profile", "game_state_profile"):
                if column_name in existing:
                    batch_op.drop_column(column_name)

    for table_name, indexes in (
        ("match_game_state_segments", ("ix_match_game_state_segments_match",)),
        ("match_player_statistics", ("ix_match_player_statistics_match",)),
        ("player_match_minutes", ("ix_player_match_minutes_match",)),
        ("match_lineups", ("ix_match_lineups_player", "ix_match_lineups_match")),
        ("shot_events", ("ix_shot_events_minute", "ix_shot_events_match")),
        ("match_events", ("ix_match_events_minute", "ix_match_events_type", "ix_match_events_match")),
        ("match_data_raw", ("ix_match_data_raw_hash", "ix_match_data_raw_provider", "ix_match_data_raw_match")),
    ):
        if _has_table(table_name):
            for index_name in indexes:
                if _has_index(table_name, index_name):
                    op.drop_index(index_name, table_name=table_name)
            op.drop_table(table_name)

