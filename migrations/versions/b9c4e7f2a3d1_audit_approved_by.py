"""P51 change-proposal workflow: audit entries record the second-leader approval.

audit_entry gains `approved_by` (the approver's email; NULL = a direct publish,
which still reopens the HR + Legal review banner) and `change_id` (the
change-request this publish executed, linking the trail back to
signals/change_requests.jsonl or its future DB twin).

Revision ID: b9c4e7f2a3d1
Revises: a1b2c3d4e5f6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9c4e7f2a3d1"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_entry", sa.Column("approved_by", sa.String(255), nullable=True))
    op.add_column("audit_entry", sa.Column("change_id", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_entry", "change_id")
    op.drop_column("audit_entry", "approved_by")
