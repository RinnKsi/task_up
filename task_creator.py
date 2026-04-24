from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.progress import Progress
from app.models.student_parent_link import StudentParentLink, StudentParentLinkStatus
from app.models.task import Task, TaskStatus
from app.models.user import User, UserRole
from app.services.gamification import calculate_task_points, complexity_to_ru_label
from app.services.ai_gateway import parse_task_draft, rule_reminder_for_notify
from app.services.ai_urgency import classify_urgency
from app.services.task_parser import parse_task_text
from app.services.telegram_sender import send_task_to_student


_MAX_CLIENT_DRAFT_BYTES = 200_000


def _draft_from_client_json(
    payload: str | None,
    *,
    fallback_text: str,
    source_type: str,
) -> dict | None:
    if not payload or not str(payload).strip():
        return None
    raw = str(payload).strip()
    if len(raw.encode("utf-8")) > _MAX_CLIENT_DRAFT_BYTES:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    subj = str(data.get("subject") or "").strip()
    title = str(data.get("title") or "").strip()
    desc = str(data.get("description") or "").strip()
    if not subj and not title and not desc:
        return None
    st = str(data.get("source_type") or source_type or "text").strip() or source_type
    raw_input = str(data.get("raw_input") or "").strip() or (fallback_text or "").strip()
    draft: dict = {
        "source_type": st,
        "subject": subj or title,
        "title": title or subj,
        "description": desc,
        "raw_input": raw_input,
    }
    due_at = data.get("due_at")
    if due_at is not None and str(due_at).strip():
        draft["due_at"] = str(due_at).strip()
    pr = data.get("priority")
    if pr is not None and str(pr).strip():
        draft["priority"] = str(pr).strip()
    cx = data.get("complexity")
    if cx is not None and str(cx).strip():
        draft["complexity"] = str(cx).strip().lower()
    try:
        et = int(data.get("estimated_time", 25) or 25)
        draft["estimated_time"] = max(1, min(1000, et))
    except (TypeError, ValueError):
        draft["estimated_time"] = 25
    try:
        conf = float(data.get("confidence", 0.0) or 0.0)
        draft["confidence"] = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        draft["confidence"] = 0.0
    tags = data.get("tags")
    if isinstance(tags, list):
        draft["tags"] = [str(t).strip() for t in tags if str(t).strip()][:32]
    steps = data.get("steps")
    if isinstance(steps, list):
        clean = [str(s).strip() for s in steps if str(s).strip()][:20]
        draft["steps"] = [s[:500] for s in clean]
    fst = data.get("recommended_first_step")
    if fst is not None and str(fst).strip():
        draft["recommended_first_step"] = str(fst).strip()[:2000]
    model = data.get("model")
    if model is not None and str(model).strip():
        draft["model"] = str(model).strip()[:120]
    return draft


def create_teacher_tasks(
    db: Session,
    *,
    teacher: User,
    text: str,
    due_at: datetime | None,
    assignment_mode: str,
    class_name: str,
    student_id: int | None,
    parent_id: int | None,
    source_type: str = "text",
    progress_comment: str = "Задача создана",
    client_draft_json: str | None = None,
) -> int:
    draft = _draft_from_client_json(client_draft_json, fallback_text=text, source_type=source_type)
    if not draft:
        draft = parse_task_draft(raw_input=text, source_type=source_type)
    parsed = parse_task_text(text)
    due_value = _resolve_due_at(draft_due=draft.get("due_at"), fallback=due_at)
    target_students = _resolve_target_students(db, assignment_mode, class_name, student_id)
    if not target_students:
        return 0

    created = 0
    for student in target_students:
        resolved_parent_id = parent_id or _get_confirmed_parent_id(db, student.id)
        task = Task(
            source_text=text,
            source_type=draft.get("source_type", source_type),
            subject=draft.get("title") or draft.get("subject") or parsed["subject"],
            description=draft.get("description") or parsed["description"],
            parse_confidence=float(draft.get("confidence", 0.0)),
            parse_raw_input=draft.get("raw_input", text),
            ai_suggestions_json=_ai_suggestions_payload(draft),
            due_at=due_value,
            status=TaskStatus.new,
            teacher_id=teacher.id,
            student_id=student.id,
            parent_id=resolved_parent_id,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        db.add(Progress(task_id=task.id, status=TaskStatus.new.value, comment=progress_comment))
        db.commit()
        _notify_student_new_task(student, task, draft)
        created += 1
    return created


def create_student_self_task(
    db: Session,
    *,
    student: User,
    text: str,
    due_at: datetime | None,
    source_type: str = "text",
    client_draft_json: str | None = None,
) -> Task:
    draft = _draft_from_client_json(client_draft_json, fallback_text=text, source_type=source_type)
    if not draft:
        draft = parse_task_draft(raw_input=text, source_type=source_type)
    parsed = parse_task_text(text)
    due_value = _resolve_due_at(draft_due=draft.get("due_at"), fallback=due_at)
    parent_id = _get_confirmed_parent_id(db, student.id)
    task = Task(
        source_text=text,
        source_type=draft.get("source_type", source_type),
        subject=draft.get("title") or draft.get("subject") or parsed["subject"],
        description=draft.get("description") or parsed["description"],
        parse_confidence=float(draft.get("confidence", 0.0)),
        parse_raw_input=draft.get("raw_input", text),
        ai_suggestions_json=_ai_suggestions_payload(draft),
        due_at=due_value,
        status=TaskStatus.new,
        teacher_id=student.id,
        student_id=student.id,
        parent_id=parent_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    db.add(Progress(task_id=task.id, status=TaskStatus.new.value, comment="Личная задача создана учеником"))
    db.commit()
    _notify_student_new_task(student, task, draft)
    return task


def _ai_suggestions_payload(draft: dict) -> str:
    """Persist enriched AI fields in ai_suggestions_json for UI consumption."""
    complexity = draft.get("complexity", "medium")
    estimated_time = int(draft.get("estimated_time", 25) or 25)
    points = calculate_task_points(complexity, estimated_time)
    payload = {
        "title": draft.get("title") or draft.get("subject"),
        "priority": draft.get("priority", "medium"),
        "estimated_time": estimated_time,
        "complexity": complexity,
        "difficulty_label": complexity_to_ru_label(complexity),
        "points": points,
        "tags": draft.get("tags", []) or [],
        "recommended_first_step": draft.get("recommended_first_step", ""),
        "steps": draft.get("steps", []) or [],
        "model": draft.get("model"),
    }
    return json.dumps(payload, ensure_ascii=False)


def _resolve_due_at(*, draft_due: str | None, fallback: datetime | None) -> datetime:
    if draft_due:
        try:
            return datetime.fromisoformat(draft_due)
        except ValueError:
            pass
    if fallback:
        return fallback
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1)


def _resolve_target_students(db: Session, assignment_mode: str, class_name: str, student_id: int | None) -> list[User]:
    if assignment_mode == "class":
        if not class_name.strip():
            return []
        return db.scalars(select(User).where(User.role == UserRole.student, User.class_name == class_name.strip())).all()
    if assignment_mode == "single":
        if not student_id:
            return []
        student = db.get(User, student_id)
        return [student] if student and student.role.value == "student" else []
    return []


def _get_confirmed_parent_id(db: Session, student_id: int) -> int | None:
    link = db.scalars(
        select(StudentParentLink)
        .where(StudentParentLink.student_id == student_id, StudentParentLink.status == StudentParentLinkStatus.confirmed)
        .order_by(StudentParentLink.updated_at.desc())
    ).first()
    return link.parent_id if link else None


def _notify_student_new_task(student: User, task: Task, draft: dict) -> None:
    urgency_obj = classify_urgency(task.due_at, task.status)
    reminder = rule_reminder_for_notify(
        subject=task.subject,
        urgency=urgency_obj.level,
        hours_left=urgency_obj.hours_left,
        role="student",
    )
    first_step = draft.get("recommended_first_step") or ""
    msg_tail = f"\n{reminder['message']}"
    if first_step:
        msg_tail += f"\nПервый шаг: {first_step}"
    send_task_to_student(
        student.telegram_chat_id,
        f"Новая задача: {task.subject} — {task.description}. Дедлайн: {task.due_at}{msg_tail}",
    )
