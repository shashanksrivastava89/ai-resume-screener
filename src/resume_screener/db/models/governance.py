"""Config-as-data: Signal registry + Jurisdiction policy packs (versioned).

Mirrors the §4 SignalRegistry / PolicyPack contracts. Base exclusions carry an
`is_base_locked` flag (the rail that they can never be removed lives in the
repository/governance layer, not a DB constraint). Policy packs and the registry
are versioned so every run can stamp exactly what it used.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from resume_screener.db.base import Base
from resume_screener.db.models.common import PkMixin, TimestampMixin


class Signal(PkMixin, TimestampMixin, Base):
    __tablename__ = "signal"

    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # registry signal id
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="judged")  # computed | judged
    direction: Mapped[str] = mapped_column(String(8), default="both")  # plus | minus | both
    cap_points: Mapped[float] = mapped_column(Float, default=1.0)
    enabled_default: Mapped[bool] = mapped_column(Boolean, default=True)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    job_relatedness: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExcludedSignal(PkMixin, TimestampMixin, Base):
    __tablename__ = "excluded_signal"

    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    reason: Mapped[str] = mapped_column(Text)
    is_base_locked: Mapped[bool] = mapped_column(Boolean, default=False)  # base 8 = never removable


class RegistryVersion(PkMixin, TimestampMixin, Base):
    """One row per published registry version (audit of the registry's own history)."""

    __tablename__ = "registry_version"

    version: Mapped[int] = mapped_column(Integer, index=True)
    published_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class PolicyPack(PkMixin, TimestampMixin, Base):
    __tablename__ = "policy_pack"
    __table_args__ = (
        UniqueConstraint("jurisdiction_code", "version", name="uq_policy_pack_version"),
    )

    jurisdiction_code: Mapped[str] = mapped_column(String(32), index=True)  # "IN","US-CA","base"
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    floor: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    obligations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    keep_at_least_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # P45
    purge_by_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # P45
    monitoring_categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    signal_overrides: Mapped[list["PolicySignalOverride"]] = relationship(
        back_populates="policy_pack", cascade="all, delete-orphan"
    )


class PolicySignalOverride(PkMixin, Base):
    __tablename__ = "policy_signal_override"

    policy_pack_id: Mapped[str] = mapped_column(
        ForeignKey("policy_pack.id", ondelete="CASCADE"), index=True
    )
    signal_key: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cap_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    policy_pack: Mapped["PolicyPack"] = relationship(back_populates="signal_overrides")
