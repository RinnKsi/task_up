"""user preferences and student-parent links

Revision ID: 0003_user_preferences_and_links
Revises: 0002_task_ai_metadata
Create Date: 2026-04-22 14:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_user_preferences_and_links"
down_revision: Union[str, Sequence[str], None] = "0002_task_ai_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("class_name", sa.String(length=32), nullable=True))

    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("theme", sa.String(length=16), nullable=False, server_default="light"),
        sa.Column("accessibility_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("anti_procrastination_mode", sa.String(length=16), nullable=False, server_default="balanced"),
        sa.Column("quiet_hours_start", sa.String(length=5), nullable=False, server_default="22:00"),
        sa.Column("quiet_hours_end", sa.String(length=5), nullable=False, server_default="07:00"),
        sa.Column("free_time_start", sa.String(length=5), nullable=False, server_default="16:00"),
        sa.Column("free_time_end", sa.String(length=5), nullable=False, server_default="20:00"),
        sa.Column("pomodoro_focus_minutes", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("pomodoro_break_minutes", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_user_preferences_id"), "user_preferences", ["id"], unique=False)
    op.create_index(op.f("ix_user_preferences_user_id"), "user_preferences", ["user_id"], unique=True)

    op.create_table(
        "student_parent_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum("pending", "confirmed", "rejected", name="studentparentlinkstatus"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["parent_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_student_parent_links_id"), "student_parent_links", ["id"], unique=False)
    op.create_index(op.f("ix_student_parent_links_parent_id"), "student_parent_links", ["parent_id"], unique=False)
    op.create_index(op.f("ix_student_parent_links_status"), "student_parent_links", ["status"], unique=False)
    op.create_index(op.f("ix_student_parent_links_student_id"), "student_parent_links", ["student_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_student_parent_links_student_id"), table_name="student_parent_links")
    op.drop_index(op.f("ix_student_parent_links_status"), table_name="student_parent_links")
    op.drop_index(op.f("ix_student_parent_links_parent_id"), table_name="student_parent_links")
    op.drop_index(op.f("ix_student_parent_links_id"), table_name="student_parent_links")
    op.drop_table("student_parent_links")

    op.drop_index(op.f("ix_user_preferences_user_id"), table_name="user_preferences")
    op.drop_index(op.f("ix_user_preferences_id"), table_name="user_preferences")
    op.drop_table("user_preferences")

    op.drop_column("users", "class_name")
