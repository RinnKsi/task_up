from datetime import datetime, timedelta, UTC
import json
from pathlib import Path
import subprocess

from sqlalchemy import delete

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.progress import Progress
from app.models.reminder import Reminder
from app.models.student_parent_link import StudentParentLink, StudentParentLinkStatus
from app.models.teacher_student_roster import TeacherStudentRoster, TeacherStudentRosterStatus
from app.models.task import Task, TaskStatus
from app.models.user import User, UserRole
from app.models.user_preference import UserPreference
from app.services.auth import hash_password


def seed() -> None:
    db_file = Path("smart_tracker.db")
    if db_file.exists():
        db_file.unlink()
    init_db()
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    db = SessionLocal()
    try:
        db.execute(delete(Reminder))
        db.execute(delete(Progress))
        db.execute(delete(StudentParentLink))
        db.execute(delete(Task))
        db.execute(delete(UserPreference))
        db.execute(delete(User))
        db.commit()

        teacher = User(
            full_name="Мария Иванова",
            username="teacher",
            email="teacher@example.com",
            password_hash=hash_password("1234"),
            is_email_verified=True,
            email_verification_token=None,
            role=UserRole.teacher,
            teacher_subjects_json=json.dumps(["math", "russian"], ensure_ascii=False),
        )
        student = User(
            full_name="Илья Петров",
            username="student",
            email="student@example.com",
            password_hash=hash_password("1234"),
            is_email_verified=True,
            email_verification_token=None,
            role=UserRole.student,
            telegram_chat_id="demo_student",
            student_invite_code="DEMOST",
        )
        parent = User(
            full_name="Ольга Петрова",
            username="parent",
            email="parent@example.com",
            password_hash=hash_password("1234"),
            is_email_verified=True,
            email_verification_token=None,
            role=UserRole.parent,
            telegram_chat_id="demo_parent",
        )
        db.add_all([teacher, student, parent])
        db.commit()
        for u in (teacher, student, parent):
            db.refresh(u)
        db.add_all(
            [
                UserPreference(user_id=teacher.id),
                UserPreference(user_id=student.id, theme="dark", accessibility_mode=True),
                UserPreference(user_id=parent.id),
                StudentParentLink(
                    student_id=student.id,
                    parent_id=parent.id,
                    requested_by_user_id=student.id,
                    status=StudentParentLinkStatus.confirmed,
                    confirmed_at=datetime.now(UTC).replace(tzinfo=None),
                ),
                TeacherStudentRoster(
                    teacher_id=teacher.id,
                    student_id=student.id,
                    status=TeacherStudentRosterStatus.confirmed,
                    requested_by_user_id=teacher.id,
                    confirmed_at=datetime.now(UTC).replace(tzinfo=None),
                ),
            ]
        )
        db.commit()

        task = Task(
            source_text="По алгебре решить №123 и 124 до завтра",
            subject="Алгебре",
            description="решить №123 и 124",
            due_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1),
            status=TaskStatus.new,
            teacher_id=teacher.id,
            student_id=student.id,
            parent_id=parent.id,
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        db.add(Progress(task_id=task.id, status=TaskStatus.new.value, comment="Демо задача создана"))
        db.commit()

        print("Seed complete.")
        print(f"Teacher ID: {teacher.id}, Student ID: {student.id}, Parent ID: {parent.id}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
