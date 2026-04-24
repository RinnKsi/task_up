from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TeacherStudentRosterStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"


class TeacherStudentRoster(Base):
    """До 10 подтверждённых учеников; приглашение по логину — ученик подтверждает в настройках."""

    __tablename__ = "teacher_student_roster"
    __table_args__ = (UniqueConstraint("teacher_id", "student_id", name="uq_teacher_student_roster_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[TeacherStudentRosterStatus] = mapped_column(
        SQLEnum(TeacherStudentRosterStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=TeacherStudentRosterStatus.confirmed,
        nullable=False,
    )
    requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    teacher = relationship("User", foreign_keys=[teacher_id], back_populates="teacher_roster_entries")
    student = relationship("User", foreign_keys=[student_id], back_populates="student_roster_entries")
