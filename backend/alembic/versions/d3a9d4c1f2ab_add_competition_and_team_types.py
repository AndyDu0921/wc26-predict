"""add competition and team types

Revision ID: d3a9d4c1f2ab
Revises: b8b7d1a21f6b
Create Date: 2026-04-21 23:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3a9d4c1f2ab"
down_revision: Union[str, Sequence[str], None] = "b8b7d1a21f6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("matches", "competition_type"):
        with op.batch_alter_table("matches") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "competition_type",
                    sa.String(length=20),
                    nullable=False,
                    server_default="national",
                )
            )

    missing_team_columns = []
    if not _has_column("teams", "team_type"):
        missing_team_columns.append(
            sa.Column(
                "team_type", sa.String(length=20), nullable=False, server_default="national"
            )
        )
    if not _has_column("teams", "country"):
        missing_team_columns.append(sa.Column("country", sa.String(length=100), nullable=True))
    if missing_team_columns:
        with op.batch_alter_table("teams") as batch_op:
            for column in missing_team_columns:
                batch_op.add_column(column)


def downgrade() -> None:
    team_columns = {col["name"] for col in sa.inspect(op.get_bind()).get_columns("teams")}
    drop_team_columns = [name for name in ("country", "team_type") if name in team_columns]
    if drop_team_columns:
        with op.batch_alter_table("teams") as batch_op:
            for column_name in drop_team_columns:
                batch_op.drop_column(column_name)

    if _has_column("matches", "competition_type"):
        with op.batch_alter_table("matches") as batch_op:
            batch_op.drop_column("competition_type")
