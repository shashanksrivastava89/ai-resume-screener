"""add run.req_code (soft link to the role slug / output folder)

Phase 4: the run index needs to address a run by the role's human handle (the
output-folder slug the API routes on) before the requisition table is populated
(Phase 6). Adds a nullable, indexed `req_code` to `run`. Nullable + additive, so it
applies cleanly to an existing database with rows.

Revision ID: b7f1c9a24d80
Revises: c2625f42970a
Create Date: 2026-07-10 00:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f1c9a24d80"
down_revision: str | None = "c2625f42970a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run", sa.Column("req_code", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_run_req_code"), "run", ["req_code"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_run_req_code"), table_name="run")
    op.drop_column("run", "req_code")
