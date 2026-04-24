"""teacher_student_roster: status, requested_by, confirmed_at

Revision ID: 0007_teacher_roster_status
Revises: 0006_teacher_subjects_roster_invite
Create Date: 2026-04-23 18:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_teacher_roster_status"
down_revision: Union[str, Sequence[str], None] = "0006_teacher_subjects_roster_invite"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("teacher_student_roster", "status"):
        op.add_column(
            "teacher_student_roster",
            sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
        )
    if not _has_column("teacher_student_roster", "requested_by_user_id"):
        op.add_column(
            "teacher_student_roster",
            sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        )
    if not _has_column("teacher_student_roster", "confirmed_at"):
        op.add_column(
            "teacher_student_roster",
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        )
    try:
        op.execute(
            sa.text(
                "UPDATE teacher_student_roster SET requested_by_user_id = teacher_id "
                "WHERE requested_by_user_id IS NULL"
            )
        )
    except Exception:
        pass
    try:
        op.execute(
            sa.text("UPDATE teacher_student_roster SET confirmed_at = created_at WHERE confirmed_at IS NULL")
        )
    except Exception:
        pass


def downgrade() -> None:
    if _has_column("teacher_student_roster", "confirmed_at"):
        op.drop_column("teacher_student_roster", "confirmed_at")
    if _has_column("teacher_student_roster", "requested_by_user_id"):
        op.drop_column("teacher_student_roster", "requested_by_user_id")
    if _has_column("teacher_student_roster", "status"):
        op.drop_column("teacher_student_roster", "status")
