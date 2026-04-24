from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SqlEnum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserRole(str, Enum):
    teacher = "teacher"
    student = "student"
    parent = "parent"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    class_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    registered_child_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    teacher_subjects_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    student_invite_code: Mapped[str | None] = mapped_column(String(16), nullable=True, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_verification_token: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    role: Mapped[UserRole] = mapped_column(SqlEnum(UserRole), nullable=False, index=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_link_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    telegram_link_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_tasks = relationship("Task", back_populates="teacher", foreign_keys="Task.teacher_id")
    assigned_tasks = relationship("Task", back_populates="student", foreign_keys="Task.student_id")
    preferences = relationship("UserPreference", back_populates="user", uselist=False, cascade="all, delete-orphan")
    student_links = relationship("StudentParentLink", foreign_keys="StudentParentLink.student_id", back_populates="student")
    parent_links = relationship("StudentParentLink", foreign_keys="StudentParentLink.parent_id", back_populates="parent")
    teacher_roster_entries = relationship(
        "TeacherStudentRoster", foreign_keys="TeacherStudentRoster.teacher_id", back_populates="teacher"
    )
    student_roster_entries = relationship(
        "TeacherStudentRoster", foreign_keys="TeacherStudentRoster.student_id", back_populates="student"
    )


# Подгружаем модель, чтобы строки "TeacherStudentRoster.*" в foreign_keys находили класс в registry.
import app.models.teacher_student_roster  # noqa: E402, F401
