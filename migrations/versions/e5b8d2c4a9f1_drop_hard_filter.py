"""drop hard_filter — the single-list rubric contract (P33)

A hard gate is now a `requirement` row with kind='hard_gate' and weight 0; the
separate hard_filter table has no ORM model anymore. Greenfield mandate: the
table's contents are disposable test data, so this drops it outright — no data
migration, no dual-format reader.

Revision ID: e5b8d2c4a9f1
Revises: d4e9f2a1b6c3
Create Date: 2026-07-12 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b8d2c4a9f1"
down_revision: str | None = "d4e9f2a1b6c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_hard_filter_rubric_id"), table_name="hard_filter")
    op.drop_table("hard_filter")


def downgrade() -> None:
    op.create_table(
        "hard_filter",
        sa.Column("rubric_id", sa.String(length=32), nullable=False),
        sa.Column("handle", sa.String(length=16), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["rubric_id"], ["rubric.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_hard_filter_rubric_id"), "hard_filter", ["rubric_id"], unique=False)
