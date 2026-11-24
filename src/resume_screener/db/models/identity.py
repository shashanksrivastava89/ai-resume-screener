"""AppUser + UserRegionRole — identity and region-scoped RBAC.

Users arrive from SSO (P27); locally the DEV_USER/DEV_ROLE seed one. A user holds
one or more (region, role) grants. `region_id = NULL` means ALL regions — that's
how IT admin gets full-app access, while a Regional HR Head / Legal is granted a
specific region. Roles are plain strings (evolvable): the four existing ones plus
`HiringManager` and `Legal`.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from resume_screener.db.base import Base
from resume_screener.db.models.common import PkMixin, TimestampMixin

# Known role strings (not a DB enum — just a reference for callers/seeds).
ROLES = ("HRHead", "IT", "TASpecialist", "HRSpecialist", "HiringManager", "Legal")


class AppUser(PkMixin, TimestampMixin, Base):
    __tablename__ = "app_user"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entra_object_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    grants: Mapped[list["UserRegionRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserRegionRole(PkMixin, TimestampMixin, Base):
    __tablename__ = "user_region_role"
    __table_args__ = (
        UniqueConstraint("user_id", "region_id", "role", name="uq_user_region_role"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    # NULL region_id = all-regions grant (IT admin).
    region_id: Mapped[str | None] = mapped_column(
        ForeignKey("region.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(32), index=True)

    user: Mapped["AppUser"] = relationship(back_populates="grants")
