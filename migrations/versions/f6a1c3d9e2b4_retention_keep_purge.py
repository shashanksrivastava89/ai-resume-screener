"""policy_pack retention: keep_at_least_days + purge_by_days (P45)

Retention becomes two explicit duties per pack — keep_at_least_days (audit-record
floor) and purge_by_days (candidate-PII purge). GREENFIELD REPLACEMENT of the
single retention_days column: no data migration, no dual-format reader (all app
data is disposable test data until the first real user).

Revision ID: f6a1c3d9e2b4
Revises: e5b8d2c4a9f1
Create Date: 2026-07-13 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a1c3d9e2b4"
down_revision: str | None = "e5b8d2c4a9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("policy_pack", sa.Column("keep_at_least_days", sa.Integer(), nullable=True))
    op.add_column("policy_pack", sa.Column("purge_by_days", sa.Integer(), nullable=True))
    op.drop_column("policy_pack", "retention_days")


def downgrade() -> None:
    op.add_column("policy_pack", sa.Column("retention_days", sa.Integer(), nullable=True))
    op.drop_column("policy_pack", "purge_by_days")
    op.drop_column("policy_pack", "keep_at_least_days")
