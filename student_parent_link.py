from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class StudentParentLinkStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"


class StudentParentLink(Base):
    __tablename__ = "student_parent_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[StudentParentLinkStatus] = mapped_column(
        SqlEnum(StudentParentLinkStatus), nullable=False, default=StudentParentLinkStatus.pending, index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    student = relationship("User", foreign_keys=[student_id], back_populates="student_links")
    parent = relationship("User", foreign_keys=[parent_id], back_populates="parent_links")
