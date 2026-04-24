"""Best-effort SQLite schema sync for new columns we added over time.

Prod-grade projects use Alembic migrations. For this MVP we also need to run on
existing user DBs без явной миграции. We check each column and ALTER if missing.
Safe no-op on every subsequent boot.
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.session import engine


_USER_PREFERENCES_COLUMNS: list[tuple[str, str]] = [
    ("ai_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    ("reminder_tone", "VARCHAR(24) NOT NULL DEFAULT 'medium'"),
    ("ai_verbosity", "VARCHAR(12) NOT NULL DEFAULT 'short'"),
    ("planning_mode", "VARCHAR(12) NOT NULL DEFAULT 'light'"),
    ("ai_language", "VARCHAR(8) NOT NULL DEFAULT 'ru'"),
    # Notifications
    ("notify_telegram", "BOOLEAN NOT NULL DEFAULT 1"),
    ("notify_email", "BOOLEAN NOT NULL DEFAULT 1"),
    ("notify_web", "BOOLEAN NOT NULL DEFAULT 1"),
    ("notify_frequency", "VARCHAR(12) NOT NULL DEFAULT 'normal'"),
    ("urgent_override_quiet", "BOOLEAN NOT NULL DEFAULT 1"),
    # Time-of-day
    ("school_time_start", "VARCHAR(5) NOT NULL DEFAULT '08:00'"),
    ("school_time_end", "VARCHAR(5) NOT NULL DEFAULT '14:30'"),
    ("tutor_time_start", "VARCHAR(5) NOT NULL DEFAULT ''"),
    ("tutor_time_end", "VARCHAR(5) NOT NULL DEFAULT ''"),
    ("sleep_time_start", "VARCHAR(5) NOT NULL DEFAULT '22:30'"),
    ("sleep_time_end", "VARCHAR(5) NOT NULL DEFAULT '07:00'"),
    ("best_focus_time", "VARCHAR(12) NOT NULL DEFAULT 'evening'"),
    # Focus
    ("pomodoro_auto_repeat", "BOOLEAN NOT NULL DEFAULT 0"),
    # UI
    ("ui_large_buttons", "BOOLEAN NOT NULL DEFAULT 0"),
    ("ui_simplified", "BOOLEAN NOT NULL DEFAULT 0"),
    ("ui_high_contrast", "BOOLEAN NOT NULL DEFAULT 0"),
]


def _existing_columns(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
        return {row[1] for row in rows}
    except Exception:
        return set()


_PASSWORD_RESET_DDL = """
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    used_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
)
"""


def ensure_schema() -> None:
    try:
        with engine.begin() as conn:
            existing = _existing_columns(conn, "user_preferences")
            if existing:
                for name, ddl in _USER_PREFERENCES_COLUMNS:
                    if name in existing:
                        continue
                    try:
                        conn.execute(text(f"ALTER TABLE user_preferences ADD COLUMN {name} {ddl}"))
                    except Exception:
                        pass
            try:
                conn.execute(text(_PASSWORD_RESET_DDL))
            except Exception:
                pass
            try:
                conn.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_user_id ON password_reset_tokens(user_id)")
                )
            except Exception:
                pass
            ucols = _existing_columns(conn, "users")
            if ucols:
                for name, ddl in (
                    ("registered_child_username", "VARCHAR(64)"),
                    ("teacher_subjects_json", "TEXT"),
                    ("student_invite_code", "VARCHAR(16)"),
                ):
                    if name not in ucols:
                        try:
                            conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
                        except Exception:
                            pass
            try:
                conn.execute(
                    text(
                        """
CREATE TABLE IF NOT EXISTS teacher_student_roster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(teacher_id) REFERENCES users(id),
    FOREIGN KEY(student_id) REFERENCES users(id),
    UNIQUE(teacher_id, student_id)
)
"""
                    )
                )
            except Exception:
                pass
            try:
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_teacher_student_roster_teacher_id ON teacher_student_roster(teacher_id)"
                    )
                )
            except Exception:
                pass
            try:
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_teacher_student_roster_student_id ON teacher_student_roster(student_id)"
                    )
                )
            except Exception:
                pass
            try:
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_student_invite_code ON users(student_invite_code)"
                    )
                )
            except Exception:
                pass
            rcols = _existing_columns(conn, "teacher_student_roster")
            if rcols:
                for name, ddl in (
                    ("status", "VARCHAR(16) NOT NULL DEFAULT 'confirmed'"),
                    ("requested_by_user_id", "INTEGER"),
                    ("confirmed_at", "DATETIME"),
                ):
                    if name not in rcols:
                        try:
                            conn.execute(text(f"ALTER TABLE teacher_student_roster ADD COLUMN {name} {ddl}"))
                        except Exception:
                            pass
                try:
                    conn.execute(
                        text(
                            "UPDATE teacher_student_roster SET requested_by_user_id = teacher_id "
                            "WHERE requested_by_user_id IS NULL"
                        )
                    )
                except Exception:
                    pass
                try:
                    conn.execute(
                        text(
                            "UPDATE teacher_student_roster SET confirmed_at = created_at WHERE confirmed_at IS NULL"
                        )
                    )
                except Exception:
                    pass
    except Exception:
        pass
