"""decision: req_code (carry-forward) + verdict_enc (the AI's verdict, encrypted)

Two additive, nullable columns on `decision`:

- `req_code` — the requisition the decision was made on. The contract always carried
  it (`Decision.req_id`); the table did not, so on the DB backend a re-screen never
  carried a decision forward. Backfilled from `run.req_code` by `run_id`.
- `verdict_enc` — the AI's suggestion and the reviewer's note, sealed together when a
  decision key is configured (fieldcrypto). Existing rows stay plain until
  `screener db encrypt-decisions` seals them.

Revision ID: c8e2f4a6b1d3
Revises: b9c4e7f2a3d1
Create Date: 2026-10-14 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2f4a6b1d3"
down_revision: str | None = "b9c4e7f2a3d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("decision", sa.Column("req_code", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_decision_req_code"), "decision", ["req_code"], unique=False)
    op.add_column("decision", sa.Column("verdict_enc", sa.Text(), nullable=True))
    # A correlated subquery works on both SQLite and PostgreSQL; a run without a
    # req_code leaves the decision's NULL (it simply never carries forward, as before).
    op.execute(
        "UPDATE decision SET req_code = "
        "(SELECT run.req_code FROM run WHERE run.run_id = decision.run_id) "
        "WHERE req_code IS NULL"
    )


def downgrade() -> None:
    op.drop_column("decision", "verdict_enc")
    op.drop_index(op.f("ix_decision_req_code"), table_name="decision")
    op.drop_column("decision", "req_code")
