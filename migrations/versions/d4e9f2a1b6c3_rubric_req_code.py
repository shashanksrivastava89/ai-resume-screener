"""rubric: add req_code soft link, make requisition_id nullable, UNIQUE(req_code, version)

Phase 6c: rubrics move into the DB before the requisition table is populated
(Phase 6d), so a rubric is keyed by `req_code` (the JD stem the API routes on) the
same way run/screening_job are. requisition_id becomes nullable (backfilled in 6d)
and a UNIQUE(req_code, version) natural key is added alongside the existing
UNIQUE(requisition_id, version). Additive + the table is empty (rubrics were
file-based), so this is data-safe. Batch mode so it also applies on SQLite.

Revision ID: d4e9f2a1b6c3
Revises: a3d5e8b1c7f2
Create Date: 2026-07-10 02:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e9f2a1b6c3"
down_revision: str | None = "a3d5e8b1c7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("rubric", schema=None) as batch:
        batch.add_column(sa.Column("req_code", sa.String(length=64), nullable=True))
        batch.alter_column("requisition_id", existing_type=sa.String(length=32), nullable=True)
        batch.create_index(batch.f("ix_rubric_req_code"), ["req_code"], unique=False)
        batch.create_unique_constraint("uq_rubric_reqcode_version", ["req_code", "version"])


def downgrade() -> None:
    with op.batch_alter_table("rubric", schema=None) as batch:
        batch.drop_constraint("uq_rubric_reqcode_version", type_="unique")
        batch.drop_index(batch.f("ix_rubric_req_code"))
        batch.alter_column("requisition_id", existing_type=sa.String(length=32), nullable=False)
        batch.drop_column("req_code")
