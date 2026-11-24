"""v2 domains: hr_preference / app_event / hm_take / checklist_approval / legal_hold (P49)

Flip the new v2 file stores to Postgres with an IDEAL schema. The speculative
`hr_preference_set`/`hr_preference_override` tables (never wired to any code path)
are DROPPED and replaced by `hr_preference` — one row per preference, the shape the
P39/P40 feature actually uses. Four more tables mirror the remaining v2 file stores;
`feature.default_enabled` gives feature flags a global-default home so the whole of
signals/features.yaml can live in feature + region_feature.

GREENFIELD: no data migration off the dropped tables (all app data is disposable
test data until the first real user). File defaults stay, so this schema is inert
until a *_BACKEND env var is flipped.

Revision ID: a1b2c3d4e5f6
Revises: f6a1c3d9e2b4
Create Date: 2026-07-13 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f6a1c3d9e2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOG_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    # --- drop the speculative preference tables (greenfield reshape) ---------------
    op.drop_index(op.f("ix_hr_preference_override_signal_key"), table_name="hr_preference_override")
    op.drop_index(op.f("ix_hr_preference_override_preference_set_id"), table_name="hr_preference_override")
    op.drop_table("hr_preference_override")
    op.drop_index(op.f("ix_hr_preference_set_scope"), table_name="hr_preference_set")
    op.drop_index(op.f("ix_hr_preference_set_requisition_id"), table_name="hr_preference_set")
    op.drop_index(op.f("ix_hr_preference_set_region_id"), table_name="hr_preference_set")
    op.drop_table("hr_preference_set")

    # --- hr_preference (one row per pref, folded latest state) ---------------------
    op.create_table(
        "hr_preference",
        sa.Column("pref_id", sa.String(length=32), nullable=False),
        sa.Column("owner_email", sa.String(length=255), nullable=False),
        sa.Column("owner_name", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("classification", sa.JSON(), nullable=True),
        sa.Column("cap_points", sa.Float(), nullable=True),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.String(length=40), nullable=True),
        sa.Column("review_due", sa.String(length=40), nullable=True),
        sa.Column("decline_note", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("actor_role", sa.String(length=64), nullable=True),
        sa.Column("ts", sa.String(length=40), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_hr_preference_pref_id"), "hr_preference", ["pref_id"], unique=True)
    op.create_index(op.f("ix_hr_preference_owner_email"), "hr_preference", ["owner_email"], unique=False)
    op.create_index(op.f("ix_hr_preference_status"), "hr_preference", ["status"], unique=False)

    # --- app_event (append-only spine mirror; monotonic id = order) ----------------
    op.create_table(
        "app_event",
        sa.Column("id", _LOG_PK, autoincrement=True, nullable=False),
        sa.Column("ts", sa.String(length=40), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("actor_role", sa.String(length=64), nullable=True),
        sa.Column("req_id", sa.String(length=64), nullable=True),
        sa.Column("candidate_id", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_app_event_kind"), "app_event", ["kind"], unique=False)
    op.create_index(op.f("ix_app_event_req_id"), "app_event", ["req_id"], unique=False)
    op.create_index(op.f("ix_app_event_candidate_id"), "app_event", ["candidate_id"], unique=False)

    # --- hm_take (FKs requisition + app_user; latest-wins per key) -----------------
    op.create_table(
        "hm_take",
        sa.Column("take_id", sa.String(length=32), nullable=False),
        sa.Column("req_code", sa.String(length=64), nullable=True),
        sa.Column("requisition_id", sa.String(length=32), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("take", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("hm_email", sa.String(length=255), nullable=True),
        sa.Column("hm_name", sa.String(length=255), nullable=True),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("ts", sa.String(length=40), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requisition_id"], ["requisition.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "candidate_id", "hm_email", name="uq_hm_take_key"),
    )
    op.create_index(op.f("ix_hm_take_take_id"), "hm_take", ["take_id"], unique=True)
    op.create_index(op.f("ix_hm_take_req_code"), "hm_take", ["req_code"], unique=False)
    op.create_index(op.f("ix_hm_take_requisition_id"), "hm_take", ["requisition_id"], unique=False)
    op.create_index(op.f("ix_hm_take_run_id"), "hm_take", ["run_id"], unique=False)
    op.create_index(op.f("ix_hm_take_candidate_id"), "hm_take", ["candidate_id"], unique=False)
    op.create_index(op.f("ix_hm_take_hm_email"), "hm_take", ["hm_email"], unique=False)
    op.create_index(op.f("ix_hm_take_user_id"), "hm_take", ["user_id"], unique=False)

    # --- checklist_approval (one row per requisition) ------------------------------
    op.create_table(
        "checklist_approval",
        sa.Column("req_code", sa.String(length=64), nullable=False),
        sa.Column("requisition_id", sa.String(length=32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("flagged_requirement_ids", sa.JSON(), nullable=True),
        sa.Column("requested_by", sa.String(length=255), nullable=True),
        sa.Column("requested_at", sa.String(length=40), nullable=True),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.String(length=40), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requisition_id"], ["requisition.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("req_code", name="uq_checklist_approval_req"),
    )
    op.create_index(op.f("ix_checklist_approval_req_code"), "checklist_approval", ["req_code"], unique=False)
    op.create_index(op.f("ix_checklist_approval_requisition_id"), "checklist_approval", ["requisition_id"], unique=False)

    # --- legal_hold (one row per held requisition) ---------------------------------
    op.create_table(
        "legal_hold",
        sa.Column("req_id", sa.String(length=64), nullable=False),
        sa.Column("requisition_id", sa.String(length=32), nullable=True),
        sa.Column("held", sa.Boolean(), nullable=False),
        sa.Column("by", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("ts", sa.String(length=40), nullable=True),
        sa.Column("released_by", sa.String(length=255), nullable=True),
        sa.Column("released_at", sa.String(length=40), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requisition_id"], ["requisition.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("req_id", name="uq_legal_hold_req"),
    )
    op.create_index(op.f("ix_legal_hold_req_id"), "legal_hold", ["req_id"], unique=False)
    op.create_index(op.f("ix_legal_hold_requisition_id"), "legal_hold", ["requisition_id"], unique=False)

    # --- feature: a global-default home for feature flags --------------------------
    op.add_column(
        "feature",
        sa.Column("default_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("feature", "default_enabled")

    op.drop_index(op.f("ix_legal_hold_requisition_id"), table_name="legal_hold")
    op.drop_index(op.f("ix_legal_hold_req_id"), table_name="legal_hold")
    op.drop_table("legal_hold")

    op.drop_index(op.f("ix_checklist_approval_requisition_id"), table_name="checklist_approval")
    op.drop_index(op.f("ix_checklist_approval_req_code"), table_name="checklist_approval")
    op.drop_table("checklist_approval")

    for idx in ("user_id", "hm_email", "candidate_id", "run_id", "requisition_id", "req_code", "take_id"):
        op.drop_index(op.f(f"ix_hm_take_{idx}"), table_name="hm_take")
    op.drop_table("hm_take")

    op.drop_index(op.f("ix_app_event_candidate_id"), table_name="app_event")
    op.drop_index(op.f("ix_app_event_req_id"), table_name="app_event")
    op.drop_index(op.f("ix_app_event_kind"), table_name="app_event")
    op.drop_table("app_event")

    op.drop_index(op.f("ix_hr_preference_status"), table_name="hr_preference")
    op.drop_index(op.f("ix_hr_preference_owner_email"), table_name="hr_preference")
    op.drop_index(op.f("ix_hr_preference_pref_id"), table_name="hr_preference")
    op.drop_table("hr_preference")

    # recreate the dropped speculative tables (greenfield: empty shells only)
    op.create_table(
        "hr_preference_set",
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("region_id", sa.String(length=32), nullable=True),
        sa.Column("requisition_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["region_id"], ["region.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requisition_id"], ["requisition.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_hr_preference_set_region_id"), "hr_preference_set", ["region_id"], unique=False)
    op.create_index(op.f("ix_hr_preference_set_requisition_id"), "hr_preference_set", ["requisition_id"], unique=False)
    op.create_index(op.f("ix_hr_preference_set_scope"), "hr_preference_set", ["scope"], unique=False)
    op.create_table(
        "hr_preference_override",
        sa.Column("preference_set_id", sa.String(length=32), nullable=False),
        sa.Column("signal_key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("cap_points", sa.Float(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["preference_set_id"], ["hr_preference_set.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_hr_preference_override_preference_set_id"), "hr_preference_override", ["preference_set_id"], unique=False)
    op.create_index(op.f("ix_hr_preference_override_signal_key"), "hr_preference_override", ["signal_key"], unique=False)
