import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import telegram_bot_chat_url
from app.core.paths import TEMPLATES_ROOT
from app.core.subjects import subjects_labels_json_list, teacher_subject_keys_from_json
from app.db.session import get_db
from app.models.progress import Progress
from app.models.reminder import Reminder
from app.models.student_parent_link import StudentParentLink, StudentParentLinkStatus
from app.models.task import Task, TaskStatus
from app.models.user import User, UserRole
from app.services.ai_coach import build_student_next_action
from app.services.ai_gateway import (
    generate_daily_digest,
    generate_parent_insight,
    generate_progress_summary,
    generate_reminder,
    generate_smart_plan,
    teacher_homework_suggestions,
)
from app.services.ai_urgency import classify_urgency
from app.services.auth import get_current_user, require_email_verified, require_role
from app.services.gamification import calculate_task_points, complexity_to_ru_label
from app.services.parent_child_link import create_parent_initiated_link
from app.services.task_creator import create_student_self_task, create_teacher_tasks
from app.services.task_workflow import mark_task_overdue, set_task_status
from app.services.homework_suggest_limit import allow_homework_suggest
from app.services.teacher_roster import (
    MAX_TEACHER_ROSTER,
    remove_teacher_roster_student,
    request_teacher_roster_link,
    teacher_roster_pending_outbound,
    teacher_roster_students,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_ROOT))


def _roster_error_ru(code: str) -> str:
    return {
        "empty_fields": "Укажите логин ученика.",
        "student_not_found": "Ученик с таким логином не найден.",
        "roster_full": "Уже 10 подтверждённых учеников. Удалите кого-то из списка или дождитесь ответа по запросам.",
        "already_linked": "Этот ученик уже у вас в списке.",
        "already_pending": "Запрос этому ученику уже отправлен — ждите подтверждения в его настройках.",
        "self": "Нельзя отправить запрос самому себе.",
        "not_teacher": "Доступ только для учителя.",
        "not_found": "Такого ученика нет в вашем списке.",
    }.get(code, "Не удалось выполнить действие.")


def _teacher_rating_rows(db: Session, teacher: User, student_ids: list[int]) -> list[dict]:
    rows: list[dict] = []
    for sid in student_ids:
        student = db.get(User, sid)
        if not student:
            continue
        st_tasks = db.scalars(
            select(Task).where(Task.teacher_id == teacher.id, Task.student_id == sid)
        ).all()
        prog = _student_progress_snapshot(st_tasks)
        gam = _student_gamification(st_tasks)
        points = int(gam.get("points") or 0)
        rating_score = points * 2 + prog["completion_percent"] * 3 + prog["streak_days"] * 4 - prog["overdue"] * 12
        rows.append(
            {
                "student": student,
                "progress": prog,
                "gamification": gam,
                "rating_score": rating_score,
                "points": points,
            }
        )
    rows.sort(key=lambda r: r["rating_score"], reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def _completion_percent(tasks: list[Task]) -> int:
    if not tasks:
        return 0
    done = len([t for t in tasks if t.status == TaskStatus.done])
    return int((done / len(tasks)) * 100)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    current_user = db.get(User, user_id) if user_id else None
    tasks = db.scalars(select(Task).order_by(Task.created_at.desc())).all()
    if current_user and current_user.role == UserRole.student:
        tasks = [t for t in tasks if t.student_id == current_user.id]
    if current_user and current_user.role == UserRole.parent:
        tasks = [t for t in tasks if t.parent_id == current_user.id]
    if current_user and current_user.role == UserRole.teacher:
        tasks = []
    stats = {
        "total": len(tasks),
        "done": len([t for t in tasks if t.status == TaskStatus.done]),
        "overdue": len([t for t in tasks if t.status == TaskStatus.overdue]),
    }
    roster_students: list[User] = []
    roster_pending_out: list = []
    roster_flash_ok: str | None = None
    roster_flash_err: str | None = None
    roster_rm_ok = False
    if current_user and current_user.role == UserRole.teacher:
        roster_students = teacher_roster_students(db, current_user.id)
        roster_pending_out = teacher_roster_pending_outbound(db, current_user.id)
        rc = request.query_params.get("roster")
        if rc == "ok":
            roster_flash_ok = "Запрос отправлен ученику. Он подтвердит в «Настройки» → «Запросы от учителей»."
        elif rc:
            roster_flash_err = _roster_error_ru(rc)
        rm = request.query_params.get("roster_rm")
        if rm == "ok":
            roster_rm_ok = True
        elif rm:
            roster_flash_err = _roster_error_ru(rm)
    index_ctx = {
        "request": request,
        "stats": stats,
        "tasks": tasks,
        "current_user": current_user,
        "roster_students": roster_students,
        "roster_pending_out": roster_pending_out,
        "roster_max": MAX_TEACHER_ROSTER,
        "roster_flash_ok": roster_flash_ok,
        "roster_flash_err": roster_flash_err,
        "roster_rm_ok": roster_rm_ok,
        "telegram_bot_url": telegram_bot_chat_url(),
    }
    return templates.TemplateResponse(request, "index.html", index_ctx)


@router.post("/teacher/roster/add")
def teacher_roster_add(
    request: Request,
    student_username: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    require_role(current_user, [UserRole.teacher])
    require_email_verified(current_user)
    ok, err = request_teacher_roster_link(db, teacher=current_user, student_username=student_username)
    q = "roster=ok" if ok else f"roster={err}"
    return RedirectResponse(f"/?{q}", status_code=303)


@router.post("/teacher/suggest-homework")
def teacher_suggest_homework_post(
    request: Request,
    grade: int = Form(5),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    require_role(current_user, [UserRole.teacher])
    require_email_verified(current_user)
    if not allow_homework_suggest(current_user.id):
        request.session["teacher_hw_error"] = (
            "Слишком частый запрос. Подождите около минуты — так мы не перегружаем Ollama и сайт."
        )
        return RedirectResponse("/teacher#hw-reco", status_code=303)
    keys = teacher_subject_keys_from_json(current_user.teacher_subjects_json)
    labels = subjects_labels_json_list(current_user.teacher_subjects_json)
    if not labels:
        request.session["teacher_hw_error"] = (
            "Укажите в профиле предметы, которые вы преподаёте (при регистрации или в настройках) — тогда подбор ДЗ будет только по ним."
        )
        return RedirectResponse("/teacher#hw-reco", status_code=303)
    payload = teacher_homework_suggestions(grade=grade, subject_keys=keys, subject_labels=labels)
    request.session["teacher_hw_suggestions"] = {**payload, "grade": max(1, min(11, int(grade))), "ts": time.time()}
    request.session.pop("teacher_hw_error", None)
    return RedirectResponse("/teacher#hw-reco", status_code=303)


@router.post("/teacher/roster/remove")
def teacher_roster_remove_row(
    request: Request,
    student_id: int = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    require_role(current_user, [UserRole.teacher])
    require_email_verified(current_user)
    ok, err = remove_teacher_roster_student(db, teacher=current_user, student_id=student_id)
    q = "roster_rm=ok" if ok else f"roster_rm={err}"
    return RedirectResponse(f"/?{q}", status_code=303)


@router.get("/teacher", response_class=HTMLResponse)
def teacher_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if current_user.role != UserRole.teacher:
        if current_user.role == UserRole.student:
            return RedirectResponse(f"/student/{current_user.id}", status_code=303)
        if current_user.role == UserRole.parent:
            return RedirectResponse(f"/parent/{current_user.id}", status_code=303)
        return RedirectResponse("/login", status_code=303)
    require_role(current_user, [UserRole.teacher])
    require_email_verified(current_user)
    students = db.scalars(select(User).where(User.role == UserRole.student)).all()
    parents = db.scalars(select(User).where(User.role == UserRole.parent)).all()
    class_names = sorted({s.class_name.strip() for s in students if s.class_name and s.class_name.strip()})
    tasks = db.scalars(select(Task).where(Task.teacher_id == current_user.id).order_by(Task.created_at.desc())).all()
    status_filter = request.query_params.get("status")
    created_count_raw = request.query_params.get("created")
    created_count = int(created_count_raw) if created_count_raw and created_count_raw.isdigit() else 0
    bulk_changed_raw = request.query_params.get("bulk_changed")
    bulk_changed = int(bulk_changed_raw) if bulk_changed_raw and bulk_changed_raw.isdigit() else 0
    if status_filter:
        tasks = [t for t in tasks if t.status.value == status_filter]
    completion_percent = _completion_percent(tasks)
    complexity_percents = _complexity_percent_map(tasks)
    bulk_stats = _teacher_bulk_stats(tasks)
    hw_suggestions = request.session.get("teacher_hw_suggestions")
    hw_error = request.session.pop("teacher_hw_error", None)
    if hw_suggestions and isinstance(hw_suggestions, dict):
        if time.time() - float(hw_suggestions.get("ts") or 0) > 1800:
            request.session.pop("teacher_hw_suggestions", None)
            hw_suggestions = None
    teacher_subject_labels = subjects_labels_json_list(current_user.teacher_subjects_json)
    return templates.TemplateResponse(
        request,
        "teacher_form.html",
        {
            "request": request,
            "students": students,
            "parents": parents,
            "class_names": class_names,
            "tasks": tasks,
            "status_filter": status_filter or "",
            "created_count": created_count,
            "bulk_changed": bulk_changed,
            "completion_percent": completion_percent,
            "complexity_percents": complexity_percents,
            "bulk_stats": bulk_stats,
            "current_user": current_user,
            "hw_suggestions": hw_suggestions,
            "hw_error": hw_error,
            "teacher_subject_labels": teacher_subject_labels,
        },
    )


@router.get("/teacher/ratings", response_class=HTMLResponse)
def teacher_ratings_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if current_user.role != UserRole.teacher:
        if current_user.role == UserRole.student:
            return RedirectResponse(f"/student/{current_user.id}", status_code=303)
        if current_user.role == UserRole.parent:
            return RedirectResponse(f"/parent/{current_user.id}", status_code=303)
        return RedirectResponse("/login", status_code=303)
    require_email_verified(current_user)
    roster = teacher_roster_students(db, current_user.id)
    sids = [s.id for s in roster]
    rating_rows = _teacher_rating_rows(db, current_user, sids)
    return templates.TemplateResponse(
        request,
        "teacher_ratings.html",
        {
            "request": request,
            "current_user": current_user,
            "rating_rows": rating_rows,
            "roster_count": len(roster),
            "roster_max": MAX_TEACHER_ROSTER,
            "telegram_bot_url": telegram_bot_chat_url(),
        },
    )


@router.post("/teacher/tasks")
def create_task(
    request: Request,
    text: str = Form(...),
    due_at: str = Form(...),
    assignment_mode: str = Form("single"),
    class_name: str = Form(""),
    student_id: int | None = Form(default=None),
    parent_id: int | None = Form(default=None),
    source_type: str = Form("text"),
    client_draft_json: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    require_role(current_user, [UserRole.teacher])
    parsed_due = datetime.fromisoformat(due_at) if due_at else None
    if not parsed_due:
        return RedirectResponse(url="/teacher?error=bad_due", status_code=303)
    created_count = create_teacher_tasks(
        db,
        teacher=current_user,
        text=text,
        due_at=parsed_due,
        assignment_mode=assignment_mode,
        class_name=class_name,
        student_id=student_id,
        parent_id=parent_id,
        source_type=source_type or "text",
        progress_comment="Задача создана",
        client_draft_json=client_draft_json.strip() or None,
    )

    return RedirectResponse(url=f"/teacher?created={created_count}", status_code=303)


@router.post("/teacher/tasks/bulk")
def teacher_bulk_action(
    request: Request,
    action: str = Form(...),
    class_name: str = Form(""),
    status_filter: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    require_role(current_user, [UserRole.teacher])
    tasks = db.scalars(select(Task).where(Task.teacher_id == current_user.id)).all()
    if class_name:
        student_map = {u.id: u for u in db.scalars(select(User).where(User.role == UserRole.student)).all()}
        tasks = [t for t in tasks if (student_map.get(t.student_id) and (student_map[t.student_id].class_name or "") == class_name)]
    if status_filter:
        tasks = [t for t in tasks if t.status.value == status_filter]
    changed = 0
    if action == "mark_overdue":
        for t in tasks:
            if t.status != TaskStatus.done:
                mark_task_overdue(db, t, comment="Bulk: эскалация учителя")
                changed += 1
    elif action == "set_in_progress":
        for t in tasks:
            if t.status == TaskStatus.new:
                set_task_status(db, t, TaskStatus.in_progress, "Bulk: взято в работу")
                changed += 1
    return RedirectResponse(url=f"/teacher?bulk_changed={changed}", status_code=303)


@router.post("/student/tasks/self")
def create_student_task_self(
    request: Request,
    text: str = Form(...),
    due_at: str = Form(""),
    source_type: str = Form("text"),
    client_draft_json: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    require_role(current_user, [UserRole.student])
    parsed_due = None
    if due_at:
        try:
            parsed_due = datetime.fromisoformat(due_at)
        except ValueError:
            parsed_due = None
    create_student_self_task(
        db,
        student=current_user,
        text=text,
        due_at=parsed_due,
        source_type=source_type,
        client_draft_json=client_draft_json.strip() or None,
    )
    return RedirectResponse(url=f"/student/{current_user.id}", status_code=303)


@router.get("/student/{student_id}", response_class=HTMLResponse)
def student_dashboard(student_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    require_role(current_user, [UserRole.student, UserRole.teacher])
    if current_user.role == UserRole.student:
        require_email_verified(current_user)
    if current_user.role == UserRole.student and current_user.id != student_id:
        return RedirectResponse(f"/student/{current_user.id}", status_code=303)
    student = db.get(User, student_id)
    status_filter = request.query_params.get("status")
    all_tasks = db.scalars(select(Task).where(Task.student_id == student_id).order_by(Task.due_at.asc())).all()
    archived_tasks = [t for t in all_tasks if t.status == TaskStatus.done]
    active_visible_tasks = [t for t in all_tasks if t.status != TaskStatus.done]
    tasks = active_visible_tasks
    if status_filter:
        tasks = [t for t in tasks if t.status.value == status_filter]
        if status_filter == TaskStatus.done.value:
            tasks = archived_tasks
    completion_percent = _completion_percent(tasks)
    active_tasks = len([t for t in tasks if t.status in (TaskStatus.new, TaskStatus.in_progress)])
    overdue_tasks = len([t for t in tasks if t.status == TaskStatus.overdue])

    active_open_tasks = [t for t in all_tasks if t.status in (TaskStatus.new, TaskStatus.in_progress, TaskStatus.overdue)]
    featured_task = active_open_tasks[0] if active_open_tasks else (all_tasks[0] if all_tasks else None)

    next_action = build_student_next_action(featured_task)
    prefs = current_user.preferences if current_user else None
    anti_mode = prefs.anti_procrastination_mode if prefs else "balanced"
    tone_pref = getattr(prefs, "reminder_tone", None) if prefs else None
    quiet = f"{prefs.quiet_hours_start}-{prefs.quiet_hours_end}" if prefs else None

    featured_urgency = classify_urgency(featured_task.due_at, featured_task.status) if featured_task else None
    task_urgencies: dict[int, dict] = {}
    for t in tasks:
        u = classify_urgency(t.due_at, t.status)
        task_urgencies[t.id] = {
            "level": u.level,
            "label": u.label,
            "color": u.color,
            "hours_left": u.hours_left,
            "progress_ratio": u.progress_ratio,
        }
    complexity_percents = _complexity_percent_map(all_tasks)
    gamification = _student_gamification(all_tasks)

    student_ai_reminder_payload = None
    first_step = None
    ai_steps: list[str] = []
    ai_summary = None
    ai_summary_month = None
    smart_plan = None
    personal_motivation = None
    if featured_task:
        parsed_suggestions = _parse_ai_suggestions(featured_task.ai_suggestions_json)
        first_step = parsed_suggestions.get("recommended_first_step") or ""
        ai_steps = parsed_suggestions.get("steps") or []

    total_points = _calculate_total_points(all_tasks)
    open_tasks_for_plan = [
        {
            "id": t.id,
            "subject": t.subject,
            "description": t.description[:120],
            "due_at": t.due_at.isoformat(),
            "status": t.status.value,
        }
        for t in active_open_tasks[:6]
    ]
    free_window = f"{prefs.free_time_start}-{prefs.free_time_end}" if prefs else "16:00-20:00"
    student_progress = _student_progress_snapshot(all_tasks)
    # Критично для UX: не блокируем загрузку страницы внешними AI-вызовами.
    # Показываем быстрый локальный контент, а AI-функции доступны в отдельных действиях/страницах.
    student_ai_reminder_payload = None
    if featured_task and featured_urgency:
        student_ai_reminder_payload = {
            "message": f"Сфокусируйтесь на задаче «{featured_task.subject}» и начните с первого шага.",
            "tone": "soft",
        }
    # Шаги только из сохранённых подсказок; разбор через AI — по действию пользователя (API / задача).
    ai_summary = None
    ai_summary_month = None
    smart_plan = None
    personal_motivation = {
        "message": "Идёте в хорошем темпе. Сделайте маленький шаг по текущей задаче прямо сейчас.",
        "tone": "supportive",
    }
    digest = generate_daily_digest(
        tasks=open_tasks_for_plan,
        best_focus_time=(prefs.best_focus_time if prefs else "evening"),
        free_window=free_window,
    )
    return templates.TemplateResponse(
        request,
        "student_dashboard.html",
        {
            "request": request,
            "student": student,
            "tasks": tasks,
            "archived_tasks": archived_tasks,
            "status_filter": status_filter or "",
            "completion_percent": completion_percent,
            "active_tasks": active_tasks,
            "overdue_tasks": overdue_tasks,
            "featured_task": featured_task,
            "featured_urgency": featured_urgency,
            "next_action": next_action,
            "student_ai_reminder": student_ai_reminder_payload["message"] if student_ai_reminder_payload else "Как только появится задача, AI подскажет мягкий первый шаг.",
            "student_ai_tone": student_ai_reminder_payload["tone"] if student_ai_reminder_payload else "soft",
            "first_step": first_step,
            "ai_steps": ai_steps,
            "task_urgencies": task_urgencies,
            "complexity_percents": complexity_percents,
            "ai_summary": ai_summary,
            "ai_summary_month": ai_summary_month,
            "smart_plan": smart_plan,
            "personal_motivation": personal_motivation,
            "daily_digest": digest,
            "student_progress": student_progress,
            "total_points": total_points,
            "gamification": gamification,
            "now": datetime.now(UTC).replace(tzinfo=None),
            "current_user": current_user,
        },
    )


def _parse_ai_suggestions(raw_json: str | None) -> dict:
    if not raw_json:
        return {}
    try:
        return json.loads(raw_json)
    except Exception:
        return {}


def _summary_stats(tasks: list[Task]) -> dict:
    total = len(tasks)
    done = len([t for t in tasks if t.status == TaskStatus.done])
    overdue = len([t for t in tasks if t.status == TaskStatus.overdue])
    overdue_by_subject: dict[str, int] = {}
    for t in tasks:
        if t.status == TaskStatus.overdue:
            overdue_by_subject[t.subject] = overdue_by_subject.get(t.subject, 0) + 1
    weak_subject = max(overdue_by_subject.items(), key=lambda kv: kv[1])[0] if overdue_by_subject else None
    return {"total": total, "done": done, "overdue": overdue, "weak_subject": weak_subject}


@router.post("/tasks/{task_id}/done")
def mark_task_done(task_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    require_role(current_user, [UserRole.student, UserRole.teacher])
    task = db.get(Task, task_id)
    if not task:
        return RedirectResponse(url="/", status_code=303)
    set_task_status(db, task, TaskStatus.done, "Отмечено выполненным")
    return RedirectResponse(url=f"/student/{task.student_id}", status_code=303)


@router.post("/tasks/{task_id}/in-progress")
def mark_task_in_progress(task_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    require_role(current_user, [UserRole.student, UserRole.teacher])
    task = db.get(Task, task_id)
    if not task:
        return RedirectResponse(url="/", status_code=303)
    set_task_status(db, task, TaskStatus.in_progress, "Взято в работу")
    return RedirectResponse(url=f"/student/{task.student_id}", status_code=303)


@router.get("/parent/{parent_id}", response_class=HTMLResponse)
def parent_page(parent_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if current_user.role == UserRole.student:
        return RedirectResponse(f"/student/{current_user.id}", status_code=303)
    require_role(current_user, [UserRole.parent, UserRole.teacher])
    if current_user.role == UserRole.parent:
        require_email_verified(current_user)
    if current_user.role == UserRole.parent and current_user.id != parent_id:
        return RedirectResponse(f"/parent/{current_user.id}", status_code=303)

    parent_account = db.get(User, parent_id)
    registered_child_login = (
        (parent_account.registered_child_username or "").strip() or None if parent_account else None
    )
    linked_children: list[User] = []

    if current_user.role == UserRole.parent:
        links = db.scalars(
            select(StudentParentLink)
            .where(
                StudentParentLink.parent_id == parent_id,
                StudentParentLink.status == StudentParentLinkStatus.confirmed,
            )
            .order_by(StudentParentLink.updated_at.desc())
        ).all()
        student_ids = [ln.student_id for ln in links]
        if student_ids:
            by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(student_ids))).all()}
            linked_children = [by_id[sid] for sid in student_ids if sid in by_id]
            tasks = db.scalars(
                select(Task).where(Task.student_id.in_(student_ids)).order_by(Task.created_at.desc())
            ).all()
        else:
            tasks = []
    else:
        tasks = db.scalars(select(Task).where(Task.parent_id == parent_id).order_by(Task.created_at.desc())).all()
    status_filter = request.query_params.get("status")
    if status_filter:
        tasks = [t for t in tasks if t.status.value == status_filter]
    tasks_done = [t for t in tasks if t.status == TaskStatus.done]
    tasks_not_done = [t for t in tasks if t.status != TaskStatus.done]
    tasks_not_done.sort(key=lambda t: (t.due_at, t.id))
    overdue_count = len([t for t in tasks if t.status == TaskStatus.overdue])
    done_count = len([t for t in tasks if t.status == TaskStatus.done])
    attention_tasks = [t for t in tasks if t.status in (TaskStatus.overdue, TaskStatus.new)]
    stats = {
        "total": len(tasks),
        "done": done_count,
        "overdue": overdue_count,
        "subjects": list({t.subject for t in tasks})[:6],
    }
    parent_insight = generate_parent_insight(stats)
    summary_stats = _summary_stats(tasks)
    parent_summary = generate_progress_summary(period="week", stats=summary_stats)
    day_cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    day_tasks = [t for t in tasks if t.updated_at >= day_cutoff]
    parent_day_stats = _summary_stats(day_tasks)
    parent_week_stats = summary_stats
    parent_digest_day = generate_progress_summary(period="day", stats=parent_day_stats)
    parent_digest_week = generate_progress_summary(period="week", stats=parent_week_stats)
    task_urgencies: dict[int, dict] = {}
    for t in tasks:
        u = classify_urgency(t.due_at, t.status)
        task_urgencies[t.id] = {"level": u.level, "label": u.label, "color": u.color, "hours_left": u.hours_left, "progress_ratio": u.progress_ratio}
    return templates.TemplateResponse(
        request,
        "parent_alerts.html",
        {
            "request": request,
            "tasks": tasks,
            "tasks_done": tasks_done,
            "tasks_not_done": tasks_not_done,
            "status_filter": status_filter or "",
            "overdue_count": overdue_count,
            "done_count": done_count,
            "attention_tasks": attention_tasks,
            "current_user": current_user,
            "parent_insight": parent_insight,
            "parent_summary": parent_summary,
            "parent_day_stats": parent_day_stats,
            "parent_week_stats": parent_week_stats,
            "parent_digest_day": parent_digest_day,
            "parent_digest_week": parent_digest_week,
            "task_urgencies": task_urgencies,
            "registered_child_login": registered_child_login,
            "linked_children": linked_children,
            "parent_uses_student_tasks": current_user.role == UserRole.parent,
        },
    )


@router.get("/parent/{parent_id}/connect", response_class=HTMLResponse)
def parent_connect_page(parent_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if current_user.role != UserRole.parent:
        if current_user.role == UserRole.student:
            return RedirectResponse(f"/student/{current_user.id}", status_code=303)
        return RedirectResponse("/teacher", status_code=303)
    require_email_verified(current_user)
    if current_user.id != parent_id:
        return RedirectResponse(f"/parent/{current_user.id}/connect", status_code=303)
    prefill = request.session.pop("parent_onboarding_student_username", None)
    flash_ok = request.query_params.get("success")
    flash_err = request.query_params.get("error")

    confirmed = db.scalars(
        select(StudentParentLink)
        .where(
            StudentParentLink.parent_id == parent_id,
            StudentParentLink.status == StudentParentLinkStatus.confirmed,
        )
        .order_by(StudentParentLink.updated_at.desc())
    ).all()

    children_snapshots: list[dict] = []
    for link in confirmed:
        student = db.get(User, link.student_id)
        if not student:
            continue
        st_tasks = db.scalars(
            select(Task).where(Task.student_id == student.id).order_by(Task.due_at.asc())
        ).all()
        progress = _student_progress_snapshot(st_tasks)
        gamif = _student_gamification(st_tasks)
        overdue_list = [t for t in st_tasks if t.status == TaskStatus.overdue][:8]
        open_list = [t for t in st_tasks if t.status in (TaskStatus.new, TaskStatus.in_progress)][:5]
        children_snapshots.append(
            {
                "student": student,
                "progress": progress,
                "gamification": gamif,
                "overdue_tasks": overdue_list,
                "open_tasks": open_list,
            }
        )

    outgoing_pending = db.scalars(
        select(StudentParentLink)
        .where(
            StudentParentLink.parent_id == parent_id,
            StudentParentLink.status == StudentParentLinkStatus.pending,
            StudentParentLink.requested_by_user_id == StudentParentLink.parent_id,
        )
        .order_by(StudentParentLink.created_at.desc())
    ).all()
    _wids = {l.student_id for l in outgoing_pending}
    wait_students = (
        {u.id: u for u in db.scalars(select(User).where(User.id.in_(_wids))).all()} if _wids else {}
    )

    return templates.TemplateResponse(
        request,
        "parent_connect.html",
        {
            "request": request,
            "current_user": current_user,
            "prefill_username": prefill or "",
            "children_snapshots": children_snapshots,
            "outgoing_pending": outgoing_pending,
            "wait_students": wait_students,
            "flash_ok": flash_ok,
            "flash_err": flash_err,
        },
    )


@router.post("/parent/{parent_id}/connect")
def parent_connect_submit(
    parent_id: int,
    request: Request,
    student_username: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if current_user.role != UserRole.parent or current_user.id != parent_id:
        return RedirectResponse("/", status_code=303)
    require_email_verified(current_user)
    ok, code = create_parent_initiated_link(db, parent=current_user, student_username=student_username)
    q = "success=link_sent" if ok else f"error={code}"
    return RedirectResponse(f"/parent/{parent_id}/connect?{q}", status_code=303)


@router.get("/notifications", response_class=HTMLResponse)
def notifications_center(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    role = current_user.role
    query = select(Reminder).order_by(Reminder.scheduled_at.desc())
    reminders = db.scalars(query).all()
    if role == UserRole.student:
        reminders = [r for r in reminders if r.user_id == current_user.id]
    elif role == UserRole.parent:
        reminders = [r for r in reminders if r.user_id == current_user.id]
    elif role == UserRole.teacher:
        teacher_task_ids = {t.id for t in db.scalars(select(Task).where(Task.teacher_id == current_user.id)).all()}
        reminders = [r for r in reminders if r.task_id in teacher_task_ids]
    status_filter = request.query_params.get("sent", "")
    if status_filter == "yes":
        reminders = [r for r in reminders if r.sent]
    elif status_filter == "no":
        reminders = [r for r in reminders if not r.sent]
    return templates.TemplateResponse(
        request,
        "notifications_center.html",
        {"request": request, "current_user": current_user, "reminders": reminders, "sent_filter": status_filter},
    )


@router.post("/notifications/clear-read")
def notifications_clear_read(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    role = current_user.role
    query = select(Reminder).where(Reminder.sent.is_(True))
    reminders = db.scalars(query).all()
    if role in {UserRole.student, UserRole.parent}:
        reminders = [r for r in reminders if r.user_id == current_user.id]
    elif role == UserRole.teacher:
        teacher_task_ids = {t.id for t in db.scalars(select(Task).where(Task.teacher_id == current_user.id)).all()}
        reminders = [r for r in reminders if r.task_id in teacher_task_ids]
    for r in reminders:
        db.delete(r)
    db.commit()
    return RedirectResponse(url="/notifications?sent=yes", status_code=303)


@router.post("/notifications/{reminder_id}/delete")
def notifications_delete(reminder_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    reminder = db.get(Reminder, reminder_id)
    if not reminder:
        return RedirectResponse(url="/notifications", status_code=303)
    if current_user.role in {UserRole.student, UserRole.parent} and reminder.user_id != current_user.id:
        return RedirectResponse(url="/notifications", status_code=303)
    if current_user.role == UserRole.teacher:
        task = db.get(Task, reminder.task_id)
        if not task or task.teacher_id != current_user.id:
            return RedirectResponse(url="/notifications", status_code=303)
    db.delete(reminder)
    db.commit()
    return RedirectResponse(url="/notifications", status_code=303)


@router.post("/tasks/{task_id}/force-overdue")
def force_overdue(task_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    require_role(current_user, [UserRole.teacher])
    task = db.get(Task, task_id)
    if not task:
        return RedirectResponse(url="/", status_code=303)
    mark_task_overdue(db, task, comment="Проверка просрочки")
    return RedirectResponse(url=f"/parent/{task.parent_id}", status_code=303)


@router.get("/demo", response_class=HTMLResponse)
def demo_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    current_user = db.get(User, user_id) if user_id else None
    return templates.TemplateResponse(request, "demo.html", {"request": request, "current_user": current_user})


@router.get("/demo/open/{target}", response_class=HTMLResponse)
def demo_open_target(target: str, request: Request, db: Session = Depends(get_db)):
    """Демо-заглушка: не открывает реальные кабинеты."""
    user_id = request.session.get("user_id")
    current_user = db.get(User, user_id) if user_id else None
    labels = {"teacher": "учителя", "student": "ученика", "parent": "родителя"}
    portal_label = labels.get(target, "роли")
    return templates.TemplateResponse(
        request,
        "demo_portal_stub.html",
        {
            "request": request,
            "current_user": current_user,
            "portal_label": portal_label,
        },
    )


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(task_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    task = db.get(Task, task_id)
    if not task:
        return RedirectResponse("/", status_code=303)
    if current_user.role == UserRole.student and task.student_id != current_user.id:
        return RedirectResponse(f"/student/{current_user.id}", status_code=303)
    if current_user.role == UserRole.parent:
        if task.parent_id == current_user.id:
            pass
        else:
            link_ok = db.scalars(
                select(StudentParentLink).where(
                    StudentParentLink.parent_id == current_user.id,
                    StudentParentLink.student_id == task.student_id,
                    StudentParentLink.status == StudentParentLinkStatus.confirmed,
                )
            ).first()
            if not link_ok:
                return RedirectResponse(f"/parent/{current_user.id}", status_code=303)
    progress_items = db.scalars(select(Progress).where(Progress.task_id == task_id).order_by(Progress.updated_at.desc())).all()
    next_action = build_student_next_action(task if task.status != TaskStatus.done else None)
    suggestions = _parse_ai_suggestions(task.ai_suggestions_json)
    ai_steps = suggestions.get("steps") or []
    urgency = classify_urgency(task.due_at, task.status)
    points = calculate_task_points(suggestions.get("complexity"), suggestions.get("estimated_time"))
    return templates.TemplateResponse(
        request,
        "task_detail.html",
        {
            "request": request,
            "task": task,
            "progress_items": progress_items,
            "current_user": current_user,
            "next_action": next_action,
            "ai_steps": ai_steps,
            "first_step": suggestions.get("recommended_first_step") or "",
            "priority": suggestions.get("priority") or "medium",
            "estimated_time": suggestions.get("estimated_time") or 0,
            "complexity": suggestions.get("complexity") or "medium",
            "difficulty_label": complexity_to_ru_label(suggestions.get("complexity")),
            "task_points": points,
            "tags": suggestions.get("tags") or [],
            "urgency": urgency,
        },
    )


def _calculate_total_points(tasks: list[Task]) -> int:
    total = 0
    for t in tasks:
        if t.status != TaskStatus.done:
            continue
        parsed = _parse_ai_suggestions(t.ai_suggestions_json)
        total += calculate_task_points(parsed.get("complexity"), parsed.get("estimated_time"))
    return total


def _complexity_percent_map(tasks: list[Task]) -> dict[int, int]:
    result: dict[int, int] = {}
    for t in tasks:
        parsed = _parse_ai_suggestions(t.ai_suggestions_json)
        complexity = (parsed.get("complexity") or "medium").lower()
        est = int(parsed.get("estimated_time") or 25)
        base = {"easy": 35, "medium": 62, "hard": 85}.get(complexity, 62)
        # +0..15 by time load
        extra = max(0, min(15, (est - 20) // 10))
        result[t.id] = max(5, min(100, base + extra))
    return result


def _teacher_bulk_stats(tasks: list[Task]) -> dict[str, int]:
    return {
        "total": len(tasks),
        "new": len([t for t in tasks if t.status == TaskStatus.new]),
        "in_progress": len([t for t in tasks if t.status == TaskStatus.in_progress]),
        "overdue": len([t for t in tasks if t.status == TaskStatus.overdue]),
    }


def _student_gamification(tasks: list[Task]) -> dict[str, object]:
    done_tasks = [t for t in tasks if t.status == TaskStatus.done]
    total_points = _calculate_total_points(tasks)
    level = max(1, total_points // 120 + 1)
    badges: list[str] = []
    if total_points >= 300:
        badges.append("Стабильный прогресс")
    if len(done_tasks) >= 10:
        badges.append("10 выполненных задач")
    if len([t for t in tasks if t.status == TaskStatus.overdue]) == 0 and len(tasks) >= 5:
        badges.append("Без просрочек")

    days = sorted({t.updated_at.date() for t in done_tasks if t.updated_at})
    streak = 0
    if days:
        cur = datetime.now(UTC).replace(tzinfo=None).date()
        idx = len(days) - 1
        while idx >= 0:
            if days[idx] == cur or days[idx] == (cur - timedelta(days=1)):
                streak += 1
                cur = days[idx] - timedelta(days=1)
                idx -= 1
                continue
            break
    return {"points": total_points, "level": level, "streak_days": streak, "badges": badges}


def _student_progress_snapshot(tasks: list[Task]) -> dict[str, int]:
    total = len(tasks)
    done = len([t for t in tasks if t.status == TaskStatus.done])
    active = len([t for t in tasks if t.status in (TaskStatus.new, TaskStatus.in_progress)])
    overdue = len([t for t in tasks if t.status == TaskStatus.overdue])
    streak_days = int(_student_gamification(tasks).get("streak_days") or 0)
    completion_percent = int((done / total) * 100) if total else 0
    return {
        "total": total,
        "done": done,
        "active": active,
        "overdue": overdue,
        "streak_days": streak_days,
        "completion_percent": completion_percent,
    }
