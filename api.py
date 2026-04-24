import json
from datetime import datetime, timedelta, UTC

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.progress import Progress
from app.models.reminder import Reminder
from app.models.task import Task, TaskStatus
from app.models.user import User, UserRole
from app.models.user_preference import UserPreference
from app.services.ai_gateway import (
    check_transcribe_health,
    decompose_task,
    generate_daily_digest,
    generate_focus_support,
    generate_parent_insight,
    generate_progress_insight,
    generate_progress_summary,
    generate_smart_plan,
    generate_task_help,
    get_transcribe_failure_reason,
    ingest_and_preview,
    ocr_task_image,
    parse_task_draft,
    teacher_assist,
    transcribe_voice,
)
from app.services.auth import get_current_user
from app.services.ai_urgency import classify_urgency
from app.services.telegram_auth import consume_telegram_link_code

router = APIRouter(prefix="/api", tags=["api"])

# --- Демо: только заглушки (без БД, без AI, без изменения данных) ---
_DEMO_PREFLIGHT = {
    "ok": True,
    "checks": {
        "database": {"ok": True, "details": "stub"},
        "scheduler": {"ok": True, "details": "stub"},
        "telegram": {"ok": True, "details": "stub"},
        "ai_provider": {"ok": True, "details": "stub:demo"},
    },
}

_DEMO_ACCEPTANCE_CHECKS = {
    "tasks_total": 3,
    "tasks_structured_by_ai": 2,
    "progress_events_total": 5,
    "reminders_total": 1,
    "telegram_linked_users": 2,
    "has_overdue": True,
    "has_done": True,
}


def _demo_stub_draft(raw_input: str, source_type: str) -> dict:
    preview = (raw_input or "").strip()[:200]
    due = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).isoformat()
    return {
        "source_type": source_type,
        "subject": "Алгебра (демо)",
        "title": "Демо: структурированное задание",
        "description": preview or "Пример текста задания для презентации.",
        "due_at": due,
        "priority": "medium",
        "estimated_time": 30,
        "complexity": "medium",
        "tags": ["демо", "stub"],
        "recommended_first_step": "Откройте учебник и найдите номера задач из условия.",
        "steps": [
            "Прочитать условие (демо-шаг)",
            "Выполнить первую часть",
            "Проверить ответы",
        ],
        "confidence": 0.42,
        "model": "demo-stub",
        "raw_input": raw_input or "",
    }


@router.get("/health")
def healthcheck():
    return {"status": "ok"}


@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.scalars(select(Task).order_by(Task.created_at.desc())).all()
    return [
        {
            "id": t.id,
            "subject": t.subject,
            "description": t.description,
            "status": t.status.value,
            "due_at": t.due_at.isoformat(),
            "student_id": t.student_id,
            "parent_id": t.parent_id,
        }
        for t in tasks
    ]


@router.get("/reminders")
def list_reminders(db: Session = Depends(get_db)):
    items = db.scalars(select(Reminder).order_by(Reminder.scheduled_at.desc())).all()
    return [
        {
            "id": r.id,
            "task_id": r.task_id,
            "user_id": r.user_id,
            "message": r.message,
            "sent": r.sent,
        }
        for r in items
    ]


@router.get("/auth/me")
def auth_me(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role.value,
        "is_email_verified": user.is_email_verified,
        "telegram_linked": bool(user.telegram_chat_id),
    }


@router.post("/auth/telegram/consume-code")
def auth_telegram_consume_code(code: str, chat_id: str, db: Session = Depends(get_db)):
    user = consume_telegram_link_code(db, code=code.strip(), chat_id=chat_id.strip())
    if not user:
        raise HTTPException(status_code=404, detail="Code invalid or expired")
    return {"ok": True, "user_id": user.id, "username": user.username}


@router.post("/ai/parse-draft")
def ai_parse_draft(raw_input: str, source_type: str = "text"):
    draft = parse_task_draft(raw_input=raw_input, source_type=source_type)
    return {"ok": True, "draft": draft}


@router.post("/ai/ingest-preview")
def ai_ingest_preview(raw_input: str, source_type: str = "text"):
    if source_type not in {"text", "photo", "voice"}:
        source_type = "text"
    draft = parse_task_draft(raw_input=raw_input, source_type=source_type)
    return {
        "ok": True,
        "source_type": source_type,
        "ingest_summary": f"{source_type.upper()} -> structured task draft",
        "draft": draft,
    }


@router.post("/ai/ingest")
async def ai_ingest(
    source_type: str = Form("text"),
    text: str = Form(""),
    due_at_hint: str = Form(""),
    class_name: str = Form(""),
    client_stt: str = Form(""),
    photo_mode: str = Form("auto"),
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
):
    """Unified Smart Ingest: text | photo | voice -> magical preview.

    - source_type=text: требуется `text`
    - source_type=photo: требуется `file` (image/*); запускается OCR -> parse
    - source_type=voice: требуется `file` (audio/*); запускается STT -> parse

    Возвращает единый preview-объект через ai_gateway.ingest_and_preview
    плюс `recognized_text` для прозрачности (видно, что именно распознал AI).
    """
    source_type = (source_type or "text").lower().strip()
    if source_type not in {"text", "photo", "voice"}:
        source_type = "text"

    recognized_text = (text or "").strip()
    extra: dict = {}
    ocr_snapshot: dict | None = None

    if source_type == "photo":
        chosen_files = [f for f in (files or []) if f is not None]
        if file:
            chosen_files.insert(0, file)
        if not chosen_files:
            return {"ok": False, "reason": "Нужно прикрепить фото."}
        first = chosen_files[0]
        content = await first.read()
        mime = first.content_type or "image/jpeg"
        if not mime.startswith("image/"):
            return {"ok": False, "reason": "Ожидается изображение (image/*)."}
        burst: list[bytes] = []
        for upl in chosen_files[1:3]:
            payload = await upl.read()
            if payload:
                burst.append(payload)
        ocr = ocr_task_image(content, mime_type=mime, mode=photo_mode, burst_images=burst)
        if not ocr.get("ok"):
            return {"ok": False, "reason": ocr.get("reason") or "Не удалось распознать текст с фото."}
        ocr_snapshot = ocr
        recognized_text = ocr.get("text") or ""
        extra["ocr_confidence"] = ocr.get("confidence", 0.0)
        extra["ocr_mode"] = ocr.get("mode") or photo_mode
        extra["ocr_quality"] = ocr.get("quality") or {}
        extra["ocr_warnings"] = ocr.get("warnings") or []

    elif source_type == "voice":
        # Браузерный STT-fallback: если клиент прислал уже распознанный текст
        # (Web Speech API), используем его без серверного STT.
        client_stt_flag = (client_stt or "").strip().lower() in {"1", "true", "yes", "on"}
        if client_stt_flag and recognized_text:
            extra["stt_source"] = "client"
        else:
            if not file:
                return {"ok": False, "reason": "Нужно прикрепить аудио."}
            content = await file.read()
            mime = file.content_type or "audio/ogg"
            stt_text = transcribe_voice(content, filename=file.filename or "voice.ogg", mime_type=mime)
            if not stt_text:
                return {
                    "ok": False,
                    "reason": get_transcribe_failure_reason(),
                    "allow_client_stt": True,
                }
            recognized_text = stt_text
            extra["stt_source"] = "server"

    if not recognized_text:
        return {"ok": False, "reason": "Пустой ввод. Напишите задание, загрузите фото или запишите голос."}

    preview = ingest_and_preview(
        raw_text=recognized_text,
        source_type=source_type,
        hinted_due_iso=(due_at_hint or None) if due_at_hint else None,
        class_name=(class_name or None) if class_name else None,
        initial_draft=(ocr_snapshot or {}).get("draft") if source_type == "photo" else None,
        fast_photo=bool(source_type == "photo" and settings.ocr_photo_fast),
    )
    preview["recognized_text"] = recognized_text
    if extra:
        preview.update(extra)
    return preview


@router.get("/ai/transcribe-health")
def ai_transcribe_health(probe: int = 0):
    """Диагностика STT: провайдер, ключ, модель, доступность сети."""
    return check_transcribe_health(probe=bool(probe))


@router.post("/ai/transcribe-voice")
async def ai_transcribe_voice(file: UploadFile = File(...)):
    content = await file.read()
    text = transcribe_voice(content, filename=file.filename or "voice.ogg", mime_type=file.content_type or "audio/ogg")
    if not text:
        return {"ok": False, "error": get_transcribe_failure_reason(), "text": "", "draft": None}
    draft = parse_task_draft(raw_input=text, source_type="voice")
    return {"ok": True, "text": text, "draft": draft}


@router.post("/ai/decompose-task")
def ai_decompose_task(
    subject: str = Form(...),
    description: str = Form(""),
    complexity: str | None = Form(None),
):
    result = decompose_task(subject=subject, description=description, complexity=complexity)
    return {"ok": True, "result": result}


@router.post("/ai/ocr-task")
async def ai_ocr_task(file: UploadFile = File(...)):
    content = await file.read()
    mime = file.content_type or "image/jpeg"
    if not mime.startswith("image/"):
        return {"ok": False, "error": "Ожидается изображение (image/*).", "text": "", "draft": None}
    result = ocr_task_image(content, mime_type=mime, mode="auto")
    return result


@router.post("/ai/teacher-assist")
def ai_teacher_assist(raw_text: str, due_at: str | None = None, class_name: str | None = None):
    result = teacher_assist(raw_text=raw_text, due_iso=due_at, class_name=class_name)
    return {"ok": True, "assist": result}


@router.post("/ai/focus-support")
def ai_focus_support(task_id: int, stage: str = "pre", db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    minutes = 25
    user = db.get(User, task.student_id)
    if user and user.preferences:
        minutes = user.preferences.pomodoro_focus_minutes or 25
    result = generate_focus_support(
        stage=stage,
        subject=task.subject,
        description=task.description,
        minutes=minutes,
    )
    return {"ok": True, "focus": result}


@router.post("/ai/progress-summary")
def ai_progress_summary(user_id: int, period: str = "week", db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    now = datetime.now(UTC).replace(tzinfo=None)
    if period == "day":
        since = now - timedelta(days=1)
    elif period == "month":
        since = now - timedelta(days=30)
    else:
        since = now - timedelta(days=7)
        period = "week"
    if user.role == UserRole.student:
        tasks = db.scalars(select(Task).where(Task.student_id == user.id, Task.created_at >= since)).all()
    elif user.role == UserRole.teacher:
        tasks = db.scalars(select(Task).where(Task.teacher_id == user.id, Task.created_at >= since)).all()
    else:
        tasks = db.scalars(select(Task).where(Task.parent_id == user.id, Task.created_at >= since)).all()
    total = len(tasks)
    done = len([t for t in tasks if t.status == TaskStatus.done])
    overdue = len([t for t in tasks if t.status == TaskStatus.overdue])
    by_subject: dict[str, int] = {}
    overdue_by_subject: dict[str, int] = {}
    for t in tasks:
        by_subject[t.subject] = by_subject.get(t.subject, 0) + 1
        if t.status == TaskStatus.overdue:
            overdue_by_subject[t.subject] = overdue_by_subject.get(t.subject, 0) + 1
    weak_subject = max(overdue_by_subject.items(), key=lambda kv: kv[1])[0] if overdue_by_subject else None
    best_subject = max(by_subject.items(), key=lambda kv: kv[1])[0] if by_subject else None
    stats = {
        "total": total,
        "done": done,
        "overdue": overdue,
        "weak_subject": weak_subject,
        "best_subject": best_subject,
        "by_subject": by_subject,
    }
    summary = generate_progress_summary(period=period, stats=stats)
    return {"ok": True, "period": period, "stats": stats, "summary": summary}


@router.post("/ai/smart-plan")
def ai_smart_plan(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    prefs = user.preferences
    free_window = f"{prefs.free_time_start}-{prefs.free_time_end}" if prefs else "16:00-20:00"
    quiet_hours = f"{prefs.quiet_hours_start}-{prefs.quiet_hours_end}" if prefs else "22:00-07:00"
    tasks = db.scalars(
        select(Task)
        .where(Task.student_id == user.id)
        .where(Task.status.in_([TaskStatus.new, TaskStatus.in_progress, TaskStatus.overdue]))
        .order_by(Task.due_at.asc())
    ).all()
    task_payload = [
        {
            "id": t.id,
            "subject": t.subject,
            "description": t.description[:120],
            "due_at": t.due_at.isoformat(),
            "status": t.status.value,
        }
        for t in tasks[:8]
    ]
    plan = generate_smart_plan(free_window=free_window, quiet_hours=quiet_hours, tasks=task_payload)
    return {"ok": True, "plan": plan, "tasks_considered": len(task_payload)}


@router.get("/ai/parent-insight/{parent_id}")
def ai_parent_insight(parent_id: int, db: Session = Depends(get_db)):
    parent = db.get(User, parent_id)
    if not parent or parent.role != UserRole.parent:
        raise HTTPException(status_code=404, detail="Parent not found")
    tasks = db.scalars(select(Task).where(Task.parent_id == parent_id)).all()
    total = len(tasks)
    done = len([t for t in tasks if t.status == TaskStatus.done])
    overdue = len([t for t in tasks if t.status == TaskStatus.overdue])
    subjects = {t.subject for t in tasks}
    stats = {
        "total": total,
        "done": done,
        "overdue": overdue,
        "subjects": list(subjects)[:6],
    }
    insight = generate_parent_insight(stats)
    return {"ok": True, "stats": stats, "insight": insight}


@router.get("/ai/daily-digest")
def ai_daily_digest(request: Request, db: Session = Depends(get_db)):
    """3 приоритетных задачи на сегодня + лучший слот, по предпочтениям пользователя."""
    user = get_current_user(request, db)
    if user.role == UserRole.student:
        query = select(Task).where(Task.student_id == user.id)
    elif user.role == UserRole.parent:
        query = select(Task).where(Task.parent_id == user.id)
    else:
        query = select(Task).where(Task.teacher_id == user.id)
    tasks = db.scalars(query.order_by(Task.due_at.asc())).all()
    task_dicts = [
        {
            "id": t.id,
            "subject": t.subject,
            "title": _task_meta(t).get("title") or t.subject,
            "description": t.description,
            "priority": _task_meta(t).get("priority") or "medium",
            "status": t.status.value,
            "due_at": t.due_at.isoformat() if t.due_at else None,
        }
        for t in tasks
    ]
    pref = db.scalars(select(UserPreference).where(UserPreference.user_id == user.id)).first()
    best_focus = (pref.best_focus_time if pref else "evening") or "evening"
    free_window = None
    if pref and pref.free_time_start and pref.free_time_end:
        free_window = f"{pref.free_time_start}-{pref.free_time_end}"
    digest = generate_daily_digest(tasks=task_dicts, best_focus_time=best_focus, free_window=free_window)
    return {"ok": True, "digest": digest}


@router.post("/ai/task-help")
def ai_task_help(request: Request, task_id: int = Form(...), question: str = Form(""), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if user.role == UserRole.student and task.student_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if user.role == UserRole.parent and task.parent_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    help_payload = generate_task_help(subject=task.subject, description=task.description, question=question)
    if help_payload.get("model") in {"rule-fallback", "unavailable"} or not (help_payload.get("answer") or "").strip():
        return {
            "ok": False,
            "reason": help_payload.get("error") or "Ollama временно недоступна. Повторите запрос через несколько секунд.",
            "help": help_payload,
        }
    return {"ok": True, "help": help_payload}


def _task_meta(task: Task) -> dict:
    try:
        return json.loads(task.ai_suggestions_json or "{}")
    except Exception:
        return {}


@router.get("/ai/urgency/{task_id}")
def ai_urgency(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    u = classify_urgency(task.due_at, task.status)
    return {
        "ok": True,
        "level": u.level,
        "label": u.label,
        "color": u.color,
        "hours_left": u.hours_left,
        "minutes_left": u.minutes_left,
        "progress_ratio": u.progress_ratio,
    }


@router.post("/ai/progress-insight")
def ai_progress_insight(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    progress = db.scalars(select(Progress).where(Progress.task_id == task_id).order_by(Progress.updated_at.asc())).all()
    events = [f"{p.status}:{p.comment or ''}" for p in progress[-8:]]
    insight = generate_progress_insight(task.subject, task.status.value, events)
    return {"ok": True, "task_id": task_id, "insight": insight}


@router.get("/demo/preflight")
def demo_preflight():
    return dict(_DEMO_PREFLIGHT)


@router.post("/demo/run")
def run_demo_flow():
    return {
        "ok": True,
        "stub": True,
        "message": "Демо-заглушка: сценарий не выполняется, данные в БД не меняются.",
        "preflight": dict(_DEMO_PREFLIGHT),
        "fallback_mode": False,
        "task_id": 9001,
        "student_id": 9002,
        "parent_id": 9003,
        "reminder_created": True,
        "next_steps": [
            "Войдите под учётной записью для реального сценария",
            "Откройте /demo для интерактивных заглушек",
        ],
    }


@router.post("/demo/ingest-preview")
def demo_ingest_preview(raw_input: str, source_type: str = "text"):
    if source_type not in {"text", "photo", "voice"}:
        source_type = "text"
    draft = _demo_stub_draft(raw_input, source_type)
    return {
        "ok": True,
        "stub": True,
        "source_type": source_type,
        "ingest_summary": f"{source_type.upper()} → демо-черновик (без AI)",
        "draft": draft,
    }


@router.get("/demo/acceptance")
def demo_acceptance():
    return {"ok": True, "stub": True, "checks": dict(_DEMO_ACCEPTANCE_CHECKS)}


@router.get("/demo/board")
def demo_board():
    jury_kpi = {
        "teacher_time_saved_minutes_estimate": 12,
        "student_first_action_events": 4,
        "overdue_tasks": 1,
        "avg_ai_confidence": 0.88,
    }
    return {
        "ok": True,
        "stub": True,
        "preflight": dict(_DEMO_PREFLIGHT),
        "jury_kpi": jury_kpi,
        "acceptance": dict(_DEMO_ACCEPTANCE_CHECKS),
        "evidence": {
            "latest_ai_task": {
                "id": 9001,
                "subject": "Алгебра (демо)",
                "confidence": 0.88,
                "raw_input": "По алгебре решить №123 и 124 до завтра",
            },
            "latest_reminder": {"task_id": 9001, "user_id": 9002, "sent": True},
            "latest_progress": {"task_id": 9001, "status": "overdue"},
        },
    }
