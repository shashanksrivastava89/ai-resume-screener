"""Region, Jurisdiction, Feature, RegionFeature.

Region is the TOP organizational scope users are granted against (multi-region
support). A legal Jurisdiction (IN, US-CA, …) belongs to one Region — a
many-to-one we can later widen to many-to-many with a join table if a
jurisdiction ever spans regions. The Feature catalog (Feature + RegionFeature)
records which features are available in which region, so the app can behave
differently per region.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from resume_screener.db.base import Base
from resume_screener.db.models.common import PkMixin, TimestampMixin


class Region(PkMixin, TimestampMixin, Base):
    __tablename__ = "region"

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # "IN", "US", "EMEA"
    name: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    jurisdictions: Mapped[list["Jurisdiction"]] = relationship(back_populates="region")


class Jurisdiction(PkMixin, TimestampMixin, Base):
    __tablename__ = "jurisdiction"

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # "IN","US-CA","EU"
    name: Mapped[str] = mapped_column(String(128))
    law: Mapped[str | None] = mapped_column(String(255), nullable=True)  # plain-language note
    # The GOVERNING region: whose Regional HR Head/Legal owns this jurisdiction's
    # rules + audit. A job's governing region is derived through here from its
    # job_location, independent of who created it or where they sit. Many-to-one for
    # now (one region owns a jurisdiction); widen to a join table only if that ever
    # stops holding. Nullable so a jurisdiction can exist before it is assigned.
    region_id: Mapped[str | None] = mapped_column(
        ForeignKey("region.id", ondelete="SET NULL"), nullable=True, index=True
    )

    region: Mapped["Region | None"] = relationship(back_populates="jurisdictions")


class Feature(PkMixin, TimestampMixin, Base):
    __tablename__ = "feature"

    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # "blind_review", …
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The flag's GLOBAL default when FLAGS_BACKEND=db (P49). Per-region overrides live
    # on RegionFeature; env-controlled flags ignore this (resolved from their env var).
    default_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class RegionFeature(PkMixin, TimestampMixin, Base):
    __tablename__ = "region_feature"
    __table_args__ = (UniqueConstraint("region_id", "feature_id", name="uq_region_feature"),)

    region_id: Mapped[str] = mapped_column(
        ForeignKey("region.id", ondelete="CASCADE"), index=True
    )
    feature_id: Mapped[str] = mapped_column(
        ForeignKey("feature.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
