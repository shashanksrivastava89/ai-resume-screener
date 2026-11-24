"""add screening_job.req_code (soft link to the role slug / output folder)

Phase 5: the DB-backed job store needs to find "the active job for this role" by
the role's human handle (the output-folder slug the Screen page routes on) before
the requisition table is populated (Phase 6). Adds a nullable, indexed `req_code`
to `screening_job`. Nullable + additive, so it applies cleanly to an existing
database with rows.

Revision ID: a3d5e8b1c7f2
Revises: b7f1c9a24d80
Create Date: 2026-07-10 01:15:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3d5e8b1c7f2"
down_revision: str | None = "b7f1c9a24d80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("screening_job", sa.Column("req_code", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_screening_job_req_code"), "screening_job", ["req_code"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_screening_job_req_code"), table_name="screening_job")
    op.drop_column("screening_job", "req_code")
