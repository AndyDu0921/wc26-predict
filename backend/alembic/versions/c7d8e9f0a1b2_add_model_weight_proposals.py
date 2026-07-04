"""Add model weight proposal audit table.

Revision ID: c7d8e9f0a1b2
Revises: b1c2d3e4f5a6
Create Date: 2026-07-03
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_weight_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("competition", sa.String(120), nullable=False),
        sa.Column("stage", sa.String(120), nullable=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column(
            "status",
            sa.String(30),
            server_default="gate_pending",
            nullable=False,
        ),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("fold_count", sa.Integer(), nullable=True),
        sa.Column("max_weight_delta", sa.Float(), nullable=False),
        sa.Column("min_improvement", sa.Float(), nullable=False),
        sa.Column("base_weights", sa.JSON(), nullable=False),
        sa.Column("candidate_weights", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("gate_decision", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_weight_proposals_competition",
        "model_weight_proposals",
        ["competition"],
    )
    op.create_index(
        "ix_model_weight_proposals_stage",
        "model_weight_proposals",
        ["stage"],
    )
    op.create_index(
        "ix_model_weight_proposals_status",
        "model_weight_proposals",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_weight_proposals_status",
        table_name="model_weight_proposals",
    )
    op.drop_index(
        "ix_model_weight_proposals_stage",
        table_name="model_weight_proposals",
    )
    op.drop_index(
        "ix_model_weight_proposals_competition",
        table_name="model_weight_proposals",
    )
    op.drop_table("model_weight_proposals")
