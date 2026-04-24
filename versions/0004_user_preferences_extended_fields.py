"""user preferences extended fields

Revision ID: 0004_user_preferences_extended_fields
Revises: 0003_user_preferences_and_links
Create Date: 2026-04-23 10:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_user_preferences_extended_fields"
down_revision: Union[str, Sequence[str], None] = "0003_user_preferences_and_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing("user_preferences", sa.Column("ai_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    _add_column_if_missing("user_preferences", sa.Column("reminder_tone", sa.String(length=24), nullable=False, server_default="medium"))
    _add_column_if_missing("user_preferences", sa.Column("ai_verbosity", sa.String(length=12), nullable=False, server_default="short"))
    _add_column_if_missing("user_preferences", sa.Column("planning_mode", sa.String(length=12), nullable=False, server_default="light"))
    _add_column_if_missing("user_preferences", sa.Column("ai_language", sa.String(length=8), nullable=False, server_default="ru"))

    _add_column_if_missing("user_preferences", sa.Column("notify_telegram", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    _add_column_if_missing("user_preferences", sa.Column("notify_email", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    _add_column_if_missing("user_preferences", sa.Column("notify_web", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    _add_column_if_missing("user_preferences", sa.Column("notify_frequency", sa.String(length=12), nullable=False, server_default="normal"))
    _add_column_if_missing("user_preferences", sa.Column("urgent_override_quiet", sa.Boolean(), nullable=False, server_default=sa.text("1")))

    _add_column_if_missing("user_preferences", sa.Column("school_time_start", sa.String(length=5), nullable=False, server_default="08:00"))
    _add_column_if_missing("user_preferences", sa.Column("school_time_end", sa.String(length=5), nullable=False, server_default="14:30"))
    _add_column_if_missing("user_preferences", sa.Column("tutor_time_start", sa.String(length=5), nullable=False, server_default=""))
    _add_column_if_missing("user_preferences", sa.Column("tutor_time_end", sa.String(length=5), nullable=False, server_default=""))
    _add_column_if_missing("user_preferences", sa.Column("sleep_time_start", sa.String(length=5), nullable=False, server_default="22:30"))
    _add_column_if_missing("user_preferences", sa.Column("sleep_time_end", sa.String(length=5), nullable=False, server_default="07:00"))
    _add_column_if_missing("user_preferences", sa.Column("best_focus_time", sa.String(length=12), nullable=False, server_default="evening"))

    _add_column_if_missing("user_preferences", sa.Column("pomodoro_auto_repeat", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    _add_column_if_missing("user_preferences", sa.Column("ui_large_buttons", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    _add_column_if_missing("user_preferences", sa.Column("ui_simplified", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    _add_column_if_missing("user_preferences", sa.Column("ui_high_contrast", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    if _has_column("user_preferences", "ui_high_contrast"):
        op.drop_column("user_preferences", "ui_high_contrast")
    if _has_column("user_preferences", "ui_simplified"):
        op.drop_column("user_preferences", "ui_simplified")
    if _has_column("user_preferences", "ui_large_buttons"):
        op.drop_column("user_preferences", "ui_large_buttons")
    if _has_column("user_preferences", "pomodoro_auto_repeat"):
        op.drop_column("user_preferences", "pomodoro_auto_repeat")

    if _has_column("user_preferences", "best_focus_time"):
        op.drop_column("user_preferences", "best_focus_time")
    if _has_column("user_preferences", "sleep_time_end"):
        op.drop_column("user_preferences", "sleep_time_end")
    if _has_column("user_preferences", "sleep_time_start"):
        op.drop_column("user_preferences", "sleep_time_start")
    if _has_column("user_preferences", "tutor_time_end"):
        op.drop_column("user_preferences", "tutor_time_end")
    if _has_column("user_preferences", "tutor_time_start"):
        op.drop_column("user_preferences", "tutor_time_start")
    if _has_column("user_preferences", "school_time_end"):
        op.drop_column("user_preferences", "school_time_end")
    if _has_column("user_preferences", "school_time_start"):
        op.drop_column("user_preferences", "school_time_start")

    if _has_column("user_preferences", "urgent_override_quiet"):
        op.drop_column("user_preferences", "urgent_override_quiet")
    if _has_column("user_preferences", "notify_frequency"):
        op.drop_column("user_preferences", "notify_frequency")
    if _has_column("user_preferences", "notify_web"):
        op.drop_column("user_preferences", "notify_web")
    if _has_column("user_preferences", "notify_email"):
        op.drop_column("user_preferences", "notify_email")
    if _has_column("user_preferences", "notify_telegram"):
        op.drop_column("user_preferences", "notify_telegram")

    if _has_column("user_preferences", "ai_language"):
        op.drop_column("user_preferences", "ai_language")
    if _has_column("user_preferences", "planning_mode"):
        op.drop_column("user_preferences", "planning_mode")
    if _has_column("user_preferences", "ai_verbosity"):
        op.drop_column("user_preferences", "ai_verbosity")
    if _has_column("user_preferences", "reminder_tone"):
        op.drop_column("user_preferences", "reminder_tone")
    if _has_column("user_preferences", "ai_enabled"):
        op.drop_column("user_preferences", "ai_enabled")
