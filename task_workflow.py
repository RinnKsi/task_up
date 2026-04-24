import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.progress import Progress
from app.models.reminder import Reminder
from app.models.task import Task, TaskStatus
from app.services.gamification import calculate_task_points, complexity_to_ru_label
from app.services.notifier import create_parent_reminder, create_student_reminder


def set_task_status(db: Session, task: Task, status: TaskStatus, comment: str) -> None:
    if task.status == status:
        return
    task.status = status
    final_comment = comment
    if status == TaskStatus.done:
        points, difficulty = _task_points_and_difficulty(task)
        final_comment = f"{comment}. Отличная работа! +{points} баллов, задача {difficulty}."
    db.add(Progress(task_id=task.id, status=status.value, comment=final_comment))
    db.commit()


def mark_task_overdue(db: Session, task: Task, comment: str = "Задача стала просроченной") -> None:
    set_task_status(db, task, TaskStatus.overdue, comment)
    create_student_reminder(db, task)
    create_parent_reminder(db, task)


def get_overdue_candidates(db: Session) -> list[Task]:
    now = datetime.utcnow()
    return db.scalars(
        select(Task).where(Task.status != TaskStatus.done).where(Task.due_at < now)
    ).all()


def get_unfinished_tasks(db: Session) -> list[Task]:
    return db.scalars(select(Task).where(Task.status != TaskStatus.done)).all()


def reminder_exists(db: Session, task_id: int, user_id: int) -> bool:
    existing = db.scalars(
        select(Reminder)
        .where(Reminder.task_id == task_id)
        .where(Reminder.user_id == user_id)
        .where(Reminder.sent.is_(False))
    ).first()
    return existing is not None


def _task_points_and_difficulty(task: Task) -> tuple[int, str]:
    complexity = "medium"
    estimated_time = 25
    try:
        payload = json.loads(task.ai_suggestions_json or "{}")
        complexity = payload.get("complexity") or complexity
        estimated_time = int(payload.get("estimated_time") or estimated_time)
    except Exception:
        pass
    return calculate_task_points(complexity, estimated_time), complexity_to_ru_label(complexity)
