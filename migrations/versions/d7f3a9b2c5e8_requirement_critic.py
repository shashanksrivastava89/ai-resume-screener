"""requirement: critic_level + critic_reason (the checklist fairness flag survives the DB)

The checklist critic flags a row that needs HR Head sign-off (`critic_level="warn"`),
and the screen gate holds screening until that version is approved
(`service.checklist_gate`). The file store kept the flag; the database store had no
column for it, so under RUBRIC_BACKEND=db a flagged row reloaded clean and the gate
never held (found 2026-10-16 while answering pen-test RS-01). `critic_reason` keeps
the sentence the critic gave, so a flag raised by the intent screen is shown as it
was given rather than recomputed. Both nullable, additive. Rows saved before this
stay unflagged until their checklist is next published.

Revision ID: d7f3a9b2c5e8
Revises: c8e2f4a6b1d3
Create Date: 2026-10-16 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7f3a9b2c5e8"
down_revision: str | None = "c8e2f4a6b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("requirement", sa.Column("critic_level", sa.String(length=16), nullable=True))
    op.add_column("requirement", sa.Column("critic_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("requirement", "critic_reason")
    op.drop_column("requirement", "critic_level")
