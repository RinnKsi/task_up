from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True, index=True)
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="light")
    accessibility_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    anti_procrastination_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="balanced")
    quiet_hours_start: Mapped[str] = mapped_column(String(5), nullable=False, default="22:00")
    quiet_hours_end: Mapped[str] = mapped_column(String(5), nullable=False, default="07:00")
    free_time_start: Mapped[str] = mapped_column(String(5), nullable=False, default="16:00")
    free_time_end: Mapped[str] = mapped_column(String(5), nullable=False, default="20:00")
    pomodoro_focus_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    pomodoro_break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reminder_tone: Mapped[str] = mapped_column(String(24), nullable=False, default="medium")
    ai_verbosity: Mapped[str] = mapped_column(String(12), nullable=False, default="short")
    planning_mode: Mapped[str] = mapped_column(String(12), nullable=False, default="light")
    ai_language: Mapped[str] = mapped_column(String(8), nullable=False, default="ru")

    # Notification channels
    notify_telegram: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_web: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_frequency: Mapped[str] = mapped_column(String(12), nullable=False, default="normal")  # low|normal|high
    urgent_override_quiet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Time-of-day profile
    school_time_start: Mapped[str] = mapped_column(String(5), nullable=False, default="08:00")
    school_time_end: Mapped[str] = mapped_column(String(5), nullable=False, default="14:30")
    tutor_time_start: Mapped[str] = mapped_column(String(5), nullable=False, default="")
    tutor_time_end: Mapped[str] = mapped_column(String(5), nullable=False, default="")
    sleep_time_start: Mapped[str] = mapped_column(String(5), nullable=False, default="22:30")
    sleep_time_end: Mapped[str] = mapped_column(String(5), nullable=False, default="07:00")
    best_focus_time: Mapped[str] = mapped_column(String(12), nullable=False, default="evening")  # morning|afternoon|evening

    # Pomodoro auto-repeat
    pomodoro_auto_repeat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # UI / Accessibility extras
    ui_large_buttons: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ui_simplified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ui_high_contrast: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user = relationship("User", back_populates="preferences")
