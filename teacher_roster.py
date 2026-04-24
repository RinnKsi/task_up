from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.teacher_student_roster import TeacherStudentRoster, TeacherStudentRosterStatus
from app.models.user import User, UserRole
from app.services.email_verification import send_plain_email
from app.services.telegram_sender import send_task_to_student

MAX_TEACHER_ROSTER = 10


def _confirmed_count(db: Session, teacher_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(TeacherStudentRoster)
            .where(
                TeacherStudentRoster.teacher_id == teacher_id,
                TeacherStudentRoster.status == TeacherStudentRosterStatus.confirmed,
            )
        )
        or 0
    )


def teacher_roster_count(db: Session, teacher_id: int) -> int:
    return _confirmed_count(db, teacher_id)


def teacher_roster_students(db: Session, teacher_id: int) -> list[User]:
    rows = db.scalars(
        select(TeacherStudentRoster)
        .where(
            TeacherStudentRoster.teacher_id == teacher_id,
            TeacherStudentRoster.status == TeacherStudentRosterStatus.confirmed,
        )
        .order_by(TeacherStudentRoster.created_at.asc())
    ).all()
    sids = [r.student_id for r in rows]
    if not sids:
        return []
    by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(sids))).all()}
    return [by_id[sid] for sid in sids if sid in by_id]


def teacher_roster_pending_outbound(db: Session, teacher_id: int) -> list[tuple[TeacherStudentRoster, User | None]]:
    rows = db.scalars(
        select(TeacherStudentRoster)
        .where(
            TeacherStudentRoster.teacher_id == teacher_id,
            TeacherStudentRoster.status == TeacherStudentRosterStatus.pending,
        )
        .order_by(TeacherStudentRoster.created_at.desc())
    ).all()
    sids = [r.student_id for r in rows]
    by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(sids))).all()} if sids else {}
    return [(r, by_id.get(r.student_id)) for r in rows]


def pending_teacher_invites_for_student(db: Session, student_id: int) -> list[TeacherStudentRoster]:
    return list(
        db.scalars(
            select(TeacherStudentRoster)
            .where(
                TeacherStudentRoster.student_id == student_id,
                TeacherStudentRoster.status == TeacherStudentRosterStatus.pending,
            )
            .order_by(TeacherStudentRoster.created_at.desc())
        ).all()
    )


def _notify_student_teacher_invite(student: User, teacher: User) -> None:
    tname = teacher.full_name or teacher.username
    send_task_to_student(
        student.telegram_chat_id,
        f"Учитель {tname} (@{teacher.username}) хочет добавить вас в свой список в Smart Tracker.\n"
        "Откройте «Настройки и безопасность» → «Запросы от учителей».",
    )
    send_plain_email(
        student.email,
        "Учитель приглашает вас в Smart Tracker",
        (
            f"{tname} (@{teacher.username}) отправил запрос на связь с вашим аккаунтом.\n"
            "Войдите на сайт → Настройки → блок «Запросы от учителей»."
        ),
    )


def request_teacher_roster_link(db: Session, *, teacher: User, student_username: str) -> tuple[bool, str]:
    if teacher.role != UserRole.teacher:
        return False, "not_teacher"
    uname = (student_username or "").strip()
    if not uname:
        return False, "empty_fields"
    if uname.lower() == (teacher.username or "").lower():
        return False, "self"
    student = db.scalars(select(User).where(User.username == uname, User.role == UserRole.student)).first()
    if not student:
        return False, "student_not_found"
    existing = db.scalars(
        select(TeacherStudentRoster).where(
            TeacherStudentRoster.teacher_id == teacher.id,
            TeacherStudentRoster.student_id == student.id,
        )
    ).first()
    if existing:
        if existing.status == TeacherStudentRosterStatus.confirmed:
            return False, "already_linked"
        if existing.status == TeacherStudentRosterStatus.pending:
            return False, "already_pending"
        db.delete(existing)
        db.commit()
    if _confirmed_count(db, teacher.id) >= MAX_TEACHER_ROSTER:
        return False, "roster_full"
    db.add(
        TeacherStudentRoster(
            teacher_id=teacher.id,
            student_id=student.id,
            status=TeacherStudentRosterStatus.pending,
            requested_by_user_id=teacher.id,
            confirmed_at=None,
        )
    )
    db.commit()
    _notify_student_teacher_invite(student, teacher)
    return True, "ok"


def student_decide_teacher_roster(
    db: Session, *, student: User, link_id: int, approve: bool
) -> tuple[bool, str]:
    if student.role != UserRole.student:
        return False, "not_student"
    link = db.get(TeacherStudentRoster, link_id)
    if not link or link.student_id != student.id or link.status != TeacherStudentRosterStatus.pending:
        return False, "bad_link"
    teacher = db.get(User, link.teacher_id)
    if not teacher or teacher.role != UserRole.teacher:
        return False, "bad_link"
    if approve:
        if _confirmed_count(db, teacher.id) >= MAX_TEACHER_ROSTER:
            return False, "teacher_full"
        link.status = TeacherStudentRosterStatus.confirmed
        link.confirmed_at = datetime.now(UTC).replace(tzinfo=None)
    else:
        link.status = TeacherStudentRosterStatus.rejected
        link.confirmed_at = None
    db.add(link)
    db.commit()
    return True, "ok"


def remove_teacher_roster_student(db: Session, *, teacher: User, student_id: int) -> tuple[bool, str]:
    if teacher.role != UserRole.teacher:
        return False, "not_teacher"
    row = db.scalars(
        select(TeacherStudentRoster).where(
            TeacherStudentRoster.teacher_id == teacher.id,
            TeacherStudentRoster.student_id == student_id,
        )
    ).first()
    if not row:
        return False, "not_found"
    db.delete(row)
    db.commit()
    return True, "ok"
