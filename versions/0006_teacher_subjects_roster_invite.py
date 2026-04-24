"""teacher subjects, student invite code, teacher_student_roster

Revision ID: 0006_teacher_subjects_roster_invite
Revises: 0005_parent_registered_child_username
Create Date: 2026-04-23 16:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_teacher_subjects_roster_invite"
down_revision: Union[str, Sequence[str], None] = "0005_parent_registered_child_username"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_column("users", "teacher_subjects_json"):
        op.add_column("users", sa.Column("teacher_subjects_json", sa.Text(), nullable=True))
    if not _has_column("users", "student_invite_code"):
        op.add_column("users", sa.Column("student_invite_code", sa.String(length=16), nullable=True))
        op.create_index(op.f("ix_users_student_invite_code"), "users", ["student_invite_code"], unique=True)

    if not _has_table("teacher_student_roster"):
        op.create_table(
            "teacher_student_roster",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("teacher_id", sa.Integer(), nullable=False),
            sa.Column("student_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["student_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["teacher_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("teacher_id", "student_id", name="uq_teacher_student_roster_pair"),
        )
        op.create_index(op.f("ix_teacher_student_roster_id"), "teacher_student_roster", ["id"], unique=False)
        op.create_index(
            op.f("ix_teacher_student_roster_teacher_id"), "teacher_student_roster", ["teacher_id"], unique=False
        )
        op.create_index(
            op.f("ix_teacher_student_roster_student_id"), "teacher_student_roster", ["student_id"], unique=False
        )


def downgrade() -> None:
    if _has_table("teacher_student_roster"):
        op.drop_index(op.f("ix_teacher_student_roster_student_id"), table_name="teacher_student_roster")
        op.drop_index(op.f("ix_teacher_student_roster_teacher_id"), table_name="teacher_student_roster")
        op.drop_index(op.f("ix_teacher_student_roster_id"), table_name="teacher_student_roster")
        op.drop_table("teacher_student_roster")

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "student_invite_code" in cols:
        try:
            op.drop_index(op.f("ix_users_student_invite_code"), table_name="users")
        except Exception:
            pass
        op.drop_column("users", "student_invite_code")
    if "teacher_subjects_json" in cols:
        op.drop_column("users", "teacher_subjects_json")
