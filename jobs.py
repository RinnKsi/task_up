from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.task import TaskStatus
from app.services.notification_policy import normalize_now_utc, should_send_reminder
from app.services.notifier import create_parent_reminder, create_student_reminder
from app.services.task_workflow import get_unfinished_tasks, mark_task_overdue

scheduler = BackgroundScheduler()


def check_overdue_tasks() -> None:
    db = SessionLocal()
    try:
        now = normalize_now_utc()
        tasks = get_unfinished_tasks(db)
        for task in tasks:
            if not should_send_reminder(task, now):
                continue
            create_student_reminder(db, task)
            create_parent_reminder(db, task)
            if task.status != TaskStatus.overdue and task.due_at <= now:
                mark_task_overdue(db, task, comment="Авто-проверка просрочки")
    finally:
        db.close()


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.add_job(
            check_overdue_tasks,
            "interval",
            seconds=settings.scheduler_interval_seconds,
            id="overdue-check",
            replace_existing=True,
        )
        scheduler.start()
