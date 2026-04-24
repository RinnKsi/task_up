from datetime import UTC, datetime, timedelta
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import telegram_bot_chat_url
from app.core.paths import TEMPLATES_ROOT
from app.core.subjects import SUBJECT_CHOICES, normalize_teacher_subject_keys, subjects_labels_json_list
from app.db.session import get_db
from app.models.student_parent_link import StudentParentLink, StudentParentLinkStatus
from app.models.user import User, UserRole
from app.models.user_preference import UserPreference
from app.services.auth import (
    authenticate_user,
    consume_reset_token_and_set_password,
    create_password_reset_token,
    create_user,
    find_valid_reset_token,
    get_current_user,
    rate_limit_check,
    rate_limit_reset,
    validate_password_strength,
    verify_email_token,
)
from app.services.email_verification import (
    build_password_reset_link,
    build_verification_link,
    generate_email_verification_token,
    send_password_reset_email,
    send_plain_email,
    send_verification_email,
)
from app.services.parent_child_link import create_parent_initiated_link, parent_has_confirmed_child
from app.services.student_invite import ensure_student_invite_code
from app.services.teacher_roster import pending_teacher_invites_for_student, student_decide_teacher_roster
from app.services.telegram_auth import generate_telegram_link_code, unlink_telegram
from app.services.telegram_sender import send_task_to_student

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_ROOT))


def _profile_security_template_context(
    db: Session,
    current_user: User,
    *,
    generated_code: str | None = None,
    message: str | None = None,
) -> dict:
    pending_links_parent_action: list[StudentParentLink] = []
    pending_links_wait_student: list[StudentParentLink] = []
    pending_parent_invites: list[StudentParentLink] = []
    if current_user.role == UserRole.parent:
        pend = db.scalars(
            select(StudentParentLink)
            .where(
                StudentParentLink.parent_id == current_user.id,
                StudentParentLink.status == StudentParentLinkStatus.pending,
            )
            .order_by(StudentParentLink.created_at.desc())
        ).all()
        for link in pend:
            if link.requested_by_user_id == link.student_id:
                pending_links_parent_action.append(link)
            elif link.requested_by_user_id == link.parent_id:
                pending_links_wait_student.append(link)
    if current_user.role == UserRole.student:
        pending_parent_invites = db.scalars(
            select(StudentParentLink)
            .where(
                StudentParentLink.student_id == current_user.id,
                StudentParentLink.status == StudentParentLinkStatus.pending,
                StudentParentLink.requested_by_user_id == StudentParentLink.parent_id,
            )
            .order_by(StudentParentLink.created_at.desc())
        ).all()
    sid = {l.student_id for l in pending_links_parent_action + pending_links_wait_student}
    pid = {l.parent_id for l in pending_parent_invites}
    students_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(sid))).all()} if sid else {}
    parents_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_(pid))).all()} if pid else {}
    rv = current_user.role.value if isinstance(current_user.role, UserRole) else str(current_user.role)
    role_label = {"teacher": "Учитель", "student": "Ученик", "parent": "Родитель"}.get(rv, rv)
    teacher_subject_labels: list[str] = []
    if current_user.role == UserRole.teacher:
        teacher_subject_labels = subjects_labels_json_list(current_user.teacher_subjects_json)
    pending_teacher_invites: list = []
    link_teachers: dict[int, User] = {}
    if current_user.role == UserRole.student:
        pending_teacher_invites = pending_teacher_invites_for_student(db, current_user.id)
        _tids = {l.teacher_id for l in pending_teacher_invites}
        link_teachers = (
            {u.id: u for u in db.scalars(select(User).where(User.id.in_(_tids))).all()} if _tids else {}
        )
    return {
        "current_user": current_user,
        "generated_code": generated_code,
        "message": message,
        "telegram_bot_url": telegram_bot_chat_url(),
        "pending_links": pending_links_parent_action,
        "pending_links_wait_student": pending_links_wait_student,
        "pending_parent_invites": pending_parent_invites,
        "link_students": students_by_id,
        "link_parents": parents_by_id,
        "role_label": role_label,
        "teacher_subject_labels": teacher_subject_labels,
        "pending_teacher_invites": pending_teacher_invites,
        "link_teachers": link_teachers,
    }


def _redirect_after_login(request: Request, db: Session, user: User) -> RedirectResponse:
    if user.role == UserRole.teacher:
        return RedirectResponse("/teacher", status_code=303)
    if user.role == UserRole.student:
        return RedirectResponse(f"/student/{user.id}", status_code=303)
    if user.role == UserRole.parent:
        reg_child = request.session.pop("register_parent_child_username", None)
        if reg_child:
            request.session["parent_onboarding_student_username"] = reg_child
        if not parent_has_confirmed_child(db, user.id):
            return RedirectResponse(f"/parent/{user.id}/connect", status_code=303)
    return RedirectResponse(f"/parent/{user.id}", status_code=303)


def _normalize_otp(code: str) -> str:
    """Только цифры — чтобы «2 7 0 6 2 4» и «270624» совпадали с кодом из сессии."""
    return "".join(c for c in (code or "") if c.isdigit())


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    current_user = db.get(User, user_id) if user_id else None
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": None, "current_user": current_user},
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    remember_me: str = Form(""),
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "anon"
    uname = username.strip()
    ip_key = f"login:ip:{client_ip}"
    user_key = f"login:user:{uname.lower()}"
    allowed_ip, retry_ip = rate_limit_check(ip_key, max_per_minute=10, max_per_hour=40)
    allowed_user, retry_user = rate_limit_check(user_key, max_per_minute=5, max_per_hour=15)
    if not allowed_ip or not allowed_user:
        retry = max(retry_ip, retry_user)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "error": f"Слишком много попыток входа. Подождите {retry} секунд.",
                "current_user": None,
            },
            status_code=429,
        )
    user = authenticate_user(db, username=uname, password=password)
    if not user:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"request": request, "error": "Неверные учетные данные", "current_user": None},
            status_code=401,
        )
    # успешный вход сбрасывает бакеты
    rate_limit_reset(user_key)
    remember = remember_me.strip().lower() in {"1", "on", "true", "yes"}
    normalized_chat_id = (user.telegram_chat_id or "").strip()
    is_numeric_chat_id = normalized_chat_id.lstrip("-").isdigit()
    if normalized_chat_id and is_numeric_chat_id:
        # 2FA cooldown: если недавно послали код — переиспользуем, не спамим Telegram.
        now = datetime.now(UTC)
        pending_prev = request.session.get("pending_2fa") or {}
        reuse = False
        if pending_prev.get("user_id") == user.id:
            sent_at_raw = pending_prev.get("sent_at")
            try:
                sent_at = datetime.fromisoformat(sent_at_raw) if sent_at_raw else None
            except (TypeError, ValueError):
                sent_at = None
            if sent_at and (now - sent_at) < timedelta(seconds=60):
                reuse = True
        if reuse:
            request.session["pending_2fa"]["remember"] = remember
            return RedirectResponse("/login/2fa", status_code=303)
        code = f"{secrets.randbelow(10**6):06d}"
        request.session["pending_2fa"] = {
            "user_id": user.id,
            "code": code,
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "sent_at": now.isoformat(),
            "remember": remember,
        }
        delivered = send_task_to_student(
            normalized_chat_id,
            f"Код входа в Smart Tracker: {code}. Срок действия 5 минут.",
        )
        if not delivered:
            request.session.pop("pending_2fa", None)
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "request": request,
                    "error": "Не удалось отправить код в Telegram. Проверьте привязку и повторите вход.",
                    "current_user": None,
                },
                status_code=503,
            )
        return RedirectResponse("/login/2fa", status_code=303)

    request.session["user_id"] = user.id
    request.session["login_at"] = datetime.now(UTC).isoformat()
    request.session["remember"] = remember
    return _redirect_after_login(request, db, user)


@router.get("/login/2fa", response_class=HTMLResponse)
def login_2fa_page(request: Request, db: Session = Depends(get_db)):
    pending = request.session.get("pending_2fa")
    if not pending:
        return RedirectResponse("/login", status_code=303)
    user = db.get(User, pending.get("user_id"))
    return templates.TemplateResponse(
        request,
        "login_2fa.html",
        {"request": request, "error": None, "username": user.username if user else "пользователь", "current_user": None},
    )


@router.post("/login/2fa", response_class=HTMLResponse)
def login_2fa_submit(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    pending = request.session.get("pending_2fa")
    if not pending:
        return RedirectResponse("/login", status_code=303)

    client_ip = request.client.host if request.client else "anon"
    allowed, retry = rate_limit_check(f"2fa:{client_ip}:{pending.get('user_id')}", max_per_minute=6, max_per_hour=20)
    if not allowed:
        return templates.TemplateResponse(
            request,
            "login_2fa.html",
            {
                "request": request,
                "error": f"Слишком много попыток. Подождите {retry} секунд.",
                "username": "пользователь",
                "current_user": None,
            },
            status_code=429,
        )

    expires_at = datetime.fromisoformat(pending["expires_at"])
    if datetime.now(UTC) > expires_at:
        request.session.pop("pending_2fa", None)
        return templates.TemplateResponse(
            request,
            "login_2fa.html",
            {"request": request, "error": "Код истек. Войдите заново.", "username": "пользователь", "current_user": None},
            status_code=401,
        )

    user = db.get(User, pending["user_id"])
    if not user:
        request.session.pop("pending_2fa", None)
        return RedirectResponse("/login", status_code=303)

    if _normalize_otp(code) != pending["code"]:
        return templates.TemplateResponse(
            request,
            "login_2fa.html",
            {"request": request, "error": "Неверный код подтверждения.", "username": user.username, "current_user": None},
            status_code=401,
        )

    remember = bool(pending.get("remember"))
    request.session.pop("pending_2fa", None)
    request.session["user_id"] = user.id
    request.session["login_at"] = datetime.now(UTC).isoformat()
    request.session["remember"] = remember
    return _redirect_after_login(request, db, user)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/password/forgot", response_class=HTMLResponse)
def password_forgot_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    current_user = db.get(User, user_id) if user_id else None
    return templates.TemplateResponse(
        request,
        "password_forgot.html",
        {"request": request, "error": None, "success": None, "current_user": current_user},
    )


@router.post("/password/forgot", response_class=HTMLResponse)
def password_forgot_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "anon"
    allowed, retry = rate_limit_check(f"pw-forgot:{client_ip}", max_per_minute=3, max_per_hour=10)
    if not allowed:
        return templates.TemplateResponse(
            request,
            "password_forgot.html",
            {
                "request": request,
                "error": f"Слишком много запросов. Подождите {retry} сек.",
                "success": None,
                "current_user": None,
            },
            status_code=429,
        )
    normalized = email.strip().lower()
    # Для безопасности ответ одинаковый независимо от того, существует email или нет.
    generic_success = (
        "Если email зарегистрирован, мы отправили письмо со ссылкой для сброса пароля. "
        "Проверьте входящие и папку 'Спам'."
    )
    user = db.scalars(select(User).where(User.email == normalized)).first()
    if user and user.is_active:
        raw_token = create_password_reset_token(db, user)
        reset_link = build_password_reset_link(str(request.base_url).rstrip("/"), raw_token)
        sent, _info = send_password_reset_email(user.email, reset_link)
        if not sent:
            # В dev-режиме SMTP не настроен — покажем ссылку напрямую, чтобы UX
            # не блокировался.
            return templates.TemplateResponse(
                request,
                "password_forgot.html",
                {
                    "request": request,
                    "error": None,
                    "success": generic_success,
                    "dev_reset_link": reset_link,
                    "current_user": None,
                },
            )
    return templates.TemplateResponse(
        request,
        "password_forgot.html",
        {
            "request": request,
            "error": None,
            "success": generic_success,
            "current_user": None,
        },
    )


@router.get("/password/reset/{token}", response_class=HTMLResponse)
def password_reset_page(token: str, request: Request, db: Session = Depends(get_db)):
    record = find_valid_reset_token(db, token)
    if not record:
        return templates.TemplateResponse(
            request,
            "password_reset.html",
            {
                "request": request,
                "error": "Ссылка недействительна или истекла. Запросите сброс заново.",
                "success": None,
                "token": token,
                "can_reset": False,
                "current_user": None,
            },
            status_code=400,
        )
    return templates.TemplateResponse(
        request,
        "password_reset.html",
        {
            "request": request,
            "error": None,
            "success": None,
            "token": token,
            "can_reset": True,
            "current_user": None,
        },
    )


@router.post("/password/reset/{token}", response_class=HTMLResponse)
def password_reset_submit(
    token: str,
    request: Request,
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    record = find_valid_reset_token(db, token)
    if not record:
        return templates.TemplateResponse(
            request,
            "password_reset.html",
            {
                "request": request,
                "error": "Ссылка недействительна или истекла. Запросите сброс заново.",
                "success": None,
                "token": token,
                "can_reset": False,
                "current_user": None,
            },
            status_code=400,
        )
    if password != password_confirm:
        return templates.TemplateResponse(
            request,
            "password_reset.html",
            {
                "request": request,
                "error": "Пароли не совпадают.",
                "success": None,
                "token": token,
                "can_reset": True,
                "current_user": None,
            },
            status_code=400,
        )
    weak = validate_password_strength(password)
    if weak:
        return templates.TemplateResponse(
            request,
            "password_reset.html",
            {
                "request": request,
                "error": weak,
                "success": None,
                "token": token,
                "can_reset": True,
                "current_user": None,
            },
            status_code=400,
        )
    user = consume_reset_token_and_set_password(db, record, password)
    if not user:
        return templates.TemplateResponse(
            request,
            "password_reset.html",
            {
                "request": request,
                "error": "Не удалось сбросить пароль. Попробуйте ещё раз.",
                "success": None,
                "token": token,
                "can_reset": False,
                "current_user": None,
            },
            status_code=500,
        )
    # Сбросим rate-limit бакет для этого пользователя, чтобы он мог сразу войти.
    rate_limit_reset(f"login:{user.username}")
    return templates.TemplateResponse(
        request,
        "password_reset.html",
        {
            "request": request,
            "error": None,
            "success": "Пароль обновлён. Теперь вы можете войти с новым паролем.",
            "token": token,
            "can_reset": False,
            "current_user": None,
        },
    )


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    current_user = db.get(User, user_id) if user_id else None
    return templates.TemplateResponse(
        request,
        "register.html",
        {
            "request": request,
            "error": None,
            "success": None,
            "verify_link": None,
            "current_user": current_user,
            "subject_choices": SUBJECT_CHOICES,
        },
    )


@router.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    role: str = Form(...),
    child_username: str = Form(""),
    teacher_subjects: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    child_u = (child_username or "").strip()
    if child_u:
        role = "parent"
    try:
        role_enum = UserRole(role)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "request": request,
                "error": "Некорректная роль",
                "success": None,
                "verify_link": None,
                "current_user": None,
                "subject_choices": SUBJECT_CHOICES,
            },
            status_code=400,
        )
    if role_enum == UserRole.teacher:
        t_subj = normalize_teacher_subject_keys(teacher_subjects)
        if not t_subj:
            return templates.TemplateResponse(
                request,
                "register.html",
                {
                    "request": request,
                    "error": "Выберите хотя бы один предмет, который вы преподаёте.",
                    "success": None,
                    "verify_link": None,
                    "current_user": None,
                    "subject_choices": SUBJECT_CHOICES,
                },
                status_code=400,
            )
    if password != password_confirm:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "request": request,
                "error": "Пароли не совпадают",
                "success": None,
                "verify_link": None,
                "current_user": None,
                "subject_choices": SUBJECT_CHOICES,
            },
            status_code=400,
        )
    weak = validate_password_strength(password)
    if weak:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "request": request,
                "error": weak,
                "success": None,
                "verify_link": None,
                "current_user": None,
                "subject_choices": SUBJECT_CHOICES,
            },
            status_code=400,
        )

    existing = db.scalars(
        select(User).where(or_(User.username == username.strip(), User.email == email.strip().lower()))
    ).first()
    if existing:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "request": request,
                "error": "Логин или email уже заняты",
                "success": None,
                "verify_link": None,
                "current_user": None,
                "subject_choices": SUBJECT_CHOICES,
            },
            status_code=409,
        )

    token = generate_email_verification_token()
    user = create_user(
        db,
        full_name=full_name.strip(),
        username=username.strip(),
        email=email.strip(),
        password=password,
        role=role_enum,
        email_verification_token=token,
        registered_child_username=child_u if role_enum == UserRole.parent else None,
        teacher_subject_keys=normalize_teacher_subject_keys(teacher_subjects)
        if role_enum == UserRole.teacher
        else None,
    )
    if role_enum == UserRole.parent and child_u:
        request.session["register_parent_child_username"] = child_u
    verify_link = build_verification_link(str(request.base_url).rstrip("/"), token)
    email_sent, email_info = send_verification_email(user.email, verify_link)
    if not email_sent:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "request": request,
                "error": None,
                "success": f"Аккаунт создан для {user.username}. Письмо не отправлено: {email_info}",
                "verify_link": verify_link,
                "current_user": None,
                "subject_choices": SUBJECT_CHOICES,
            },
            status_code=200,
        )
    return templates.TemplateResponse(
        request,
        "register.html",
        {
            "request": request,
            "error": None,
            "success": f"Аккаунт создан для {user.username}. Письмо подтверждения отправлено на {user.email}.",
            "verify_link": None,
            "current_user": None,
            "subject_choices": SUBJECT_CHOICES,
        },
    )


@router.get("/verify-email", response_class=HTMLResponse)
def verify_email(request: Request, token: str, db: Session = Depends(get_db)):
    user = verify_email_token(db, token)
    return templates.TemplateResponse(
        request,
        "verify_email.html",
        {"request": request, "success": bool(user), "user": user, "current_user": user},
    )


@router.get("/profile/security", response_class=HTMLResponse)
def profile_security(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if not current_user.preferences:
        db.add(UserPreference(user_id=current_user.id))
        db.commit()
        db.refresh(current_user)
    ensure_student_invite_code(db, current_user)
    db.refresh(current_user)
    ctx = _profile_security_template_context(db, current_user)
    return templates.TemplateResponse(
        request,
        "profile_security.html",
        {"request": request, **ctx},
    )


@router.post("/profile/security/telegram/link", response_class=HTMLResponse)
def profile_generate_tg_link_code(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    code = generate_telegram_link_code(db, current_user)
    db.refresh(current_user)
    ctx = _profile_security_template_context(
        db,
        current_user,
        generated_code=code,
        message="Отправьте боту команду: /link <код>",
    )
    return templates.TemplateResponse(request, "profile_security.html", {"request": request, **ctx})


@router.post("/profile/security/teacher-roster/{link_id}/decision")
def decide_teacher_roster_as_student(
    link_id: int,
    request: Request,
    decision: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if current_user.role != UserRole.student:
        return RedirectResponse("/profile/security", status_code=303)
    ok, code = student_decide_teacher_roster(
        db, student=current_user, link_id=link_id, approve=decision == "approve"
    )
    if not ok:
        err = "teacher_full" if code == "teacher_full" else "bad_invite"
        return RedirectResponse(f"/profile/security?error={err}", status_code=303)
    return RedirectResponse("/profile/security?success=teacher_invite_decided", status_code=303)


@router.post("/profile/security/telegram/unlink")
def profile_unlink_tg(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    unlink_telegram(db, current_user)
    return RedirectResponse("/profile/security", status_code=303)


@router.post("/profile/security/student-parent/parent-invite/{link_id}/decision")
def decide_parent_invite_as_student(
    link_id: int,
    request: Request,
    decision: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if current_user.role != UserRole.student:
        return RedirectResponse("/profile/security", status_code=303)
    link = db.get(StudentParentLink, link_id)
    if (
        not link
        or link.student_id != current_user.id
        or link.requested_by_user_id != link.parent_id
        or link.status != StudentParentLinkStatus.pending
    ):
        return RedirectResponse("/profile/security?error=bad_invite", status_code=303)
    parent = db.get(User, link.parent_id)
    if decision == "approve":
        link.status = StudentParentLinkStatus.confirmed
        link.confirmed_at = datetime.now(UTC).replace(tzinfo=None)
        msg = f"Ученик @{current_user.username} подтвердил вашу привязку в Smart Tracker."
    else:
        link.status = StudentParentLinkStatus.rejected
        msg = f"Ученик @{current_user.username} отклонил запрос на привязку в Smart Tracker."
    db.commit()
    if parent:
        send_task_to_student(parent.telegram_chat_id, msg)
        send_plain_email(
            parent.email,
            "Привязка к ученику в Smart Tracker",
            msg + "\nОткройте раздел «Мой ребёнок» в кабинете родителя.",
        )
    return RedirectResponse("/profile/security?success=invite_decided", status_code=303)


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"on", "true", "1", "yes"}


def _ensure_preference(db: Session, user: User) -> UserPreference:
    if user.preferences:
        return user.preferences
    pref = UserPreference(user_id=user.id)
    db.add(pref)
    db.commit()
    db.refresh(user)
    return user.preferences


@router.post("/profile/security/preferences")
def profile_update_preferences(
    request: Request,
    # Appearance
    theme: str = Form("light"),
    accessibility_mode: str | None = Form(default=None),
    # AI
    ai_enabled: str | None = Form(default=None),
    reminder_tone: str = Form("medium"),
    anti_procrastination_mode: str = Form("balanced"),
    planning_mode: str = Form("light"),
    ai_verbosity: str = Form("short"),
    # Notifications
    notify_telegram: str | None = Form(default=None),
    notify_email: str | None = Form(default=None),
    notify_web: str | None = Form(default=None),
    notify_frequency: str = Form("normal"),
    urgent_override_quiet: str | None = Form(default=None),
    # Time / quiet
    quiet_hours_start: str = Form("22:00"),
    quiet_hours_end: str = Form("07:00"),
    free_time_start: str = Form("16:00"),
    free_time_end: str = Form("20:00"),
    school_time_start: str = Form("08:00"),
    school_time_end: str = Form("14:30"),
    tutor_time_start: str = Form(""),
    tutor_time_end: str = Form(""),
    sleep_time_start: str = Form("22:30"),
    sleep_time_end: str = Form("07:00"),
    best_focus_time: str = Form("evening"),
    # Focus
    pomodoro_focus_minutes: int = Form(25),
    pomodoro_break_minutes: int = Form(5),
    pomodoro_auto_repeat: str | None = Form(default=None),
    # UI / a11y extras
    ui_large_buttons: str | None = Form(default=None),
    ui_simplified: str | None = Form(default=None),
    ui_high_contrast: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    preference = _ensure_preference(db, current_user)

    # Appearance
    preference.theme = theme if theme in {"light", "dark", "a11y", "system"} else "light"
    preference.accessibility_mode = _flag(accessibility_mode)

    # AI
    preference.ai_enabled = _flag(ai_enabled) if ai_enabled is not None else True
    preference.reminder_tone = reminder_tone if reminder_tone in {"soft", "medium", "urgent", "supportive", "motivating"} else "medium"
    preference.anti_procrastination_mode = (
        anti_procrastination_mode if anti_procrastination_mode in {"soft", "balanced", "strict"} else "balanced"
    )
    preference.planning_mode = planning_mode if planning_mode in {"light", "balanced", "deep"} else "light"
    preference.ai_verbosity = ai_verbosity if ai_verbosity in {"short", "normal", "detailed"} else "short"

    # Notifications
    preference.notify_telegram = _flag(notify_telegram) if notify_telegram is not None else True
    preference.notify_email = _flag(notify_email) if notify_email is not None else True
    preference.notify_web = _flag(notify_web) if notify_web is not None else True
    preference.notify_frequency = notify_frequency if notify_frequency in {"low", "normal", "high"} else "normal"
    preference.urgent_override_quiet = _flag(urgent_override_quiet) if urgent_override_quiet is not None else True

    # Time-of-day
    preference.quiet_hours_start = quiet_hours_start or "22:00"
    preference.quiet_hours_end = quiet_hours_end or "07:00"
    preference.free_time_start = free_time_start or "16:00"
    preference.free_time_end = free_time_end or "20:00"
    preference.school_time_start = school_time_start or "08:00"
    preference.school_time_end = school_time_end or "14:30"
    preference.tutor_time_start = tutor_time_start or ""
    preference.tutor_time_end = tutor_time_end or ""
    preference.sleep_time_start = sleep_time_start or "22:30"
    preference.sleep_time_end = sleep_time_end or "07:00"
    preference.best_focus_time = best_focus_time if best_focus_time in {"morning", "afternoon", "evening"} else "evening"

    # Focus
    preference.pomodoro_focus_minutes = max(15, min(60, pomodoro_focus_minutes))
    preference.pomodoro_break_minutes = max(3, min(20, pomodoro_break_minutes))
    preference.pomodoro_auto_repeat = _flag(pomodoro_auto_repeat)

    # UI
    preference.ui_large_buttons = _flag(ui_large_buttons)
    preference.ui_simplified = _flag(ui_simplified)
    preference.ui_high_contrast = _flag(ui_high_contrast)

    db.commit()
    return RedirectResponse("/profile/security?saved=1", status_code=303)


@router.post("/profile/security/class")
def profile_update_class(
    request: Request,
    class_name: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if current_user.role != UserRole.student:
        return RedirectResponse("/profile/security", status_code=303)
    current_user.class_name = class_name.strip() or None
    db.commit()
    return RedirectResponse("/profile/security", status_code=303)


@router.post("/profile/security/email/change")
def profile_change_email(
    request: Request,
    new_email: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    normalized = new_email.strip().lower()
    existing = db.scalars(select(User).where(and_(User.email == normalized, User.id != current_user.id))).first()
    if existing:
        return RedirectResponse("/profile/security?error=email_taken", status_code=303)
    token = generate_email_verification_token()
    current_user.email = normalized
    current_user.is_email_verified = False
    current_user.email_verification_token = token
    db.commit()
    verify_link = build_verification_link(str(request.base_url).rstrip("/"), token)
    send_verification_email(current_user.email, verify_link)
    return RedirectResponse("/profile/security?success=email_changed", status_code=303)


@router.post("/profile/security/delete-account")
def profile_delete_account(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    db.delete(current_user)
    db.commit()
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.post("/profile/security/student-parent/request")
def request_parent_link(
    request: Request,
    parent_username: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    if current_user.role != UserRole.student:
        return RedirectResponse("/profile/security", status_code=303)
    parent = db.scalars(select(User).where(User.username == parent_username.strip(), User.role == UserRole.parent)).first()
    if not parent:
        return RedirectResponse("/profile/security?error=parent_not_found", status_code=303)
    existing = db.scalars(
        select(StudentParentLink).where(
            StudentParentLink.student_id == current_user.id,
            StudentParentLink.parent_id == parent.id,
            StudentParentLink.status.in_([StudentParentLinkStatus.pending, StudentParentLinkStatus.confirmed]),
        )
    ).first()
    if existing:
        return RedirectResponse("/profile/security?error=link_exists", status_code=303)
    db.add(
        StudentParentLink(
            student_id=current_user.id,
            parent_id=parent.id,
            requested_by_user_id=current_user.id,
            status=StudentParentLinkStatus.pending,
        )
    )
    db.commit()
    student_name = current_user.full_name or current_user.username
    tg_message = (
        f"Новый запрос на привязку в Smart Tracker.\n"
        f"Ученик: {student_name} (@{current_user.username}).\n"
        "Проверьте раздел 'Профиль и безопасность' и подтвердите запрос."
    )
    send_task_to_student(parent.telegram_chat_id, tg_message)
    send_plain_email(
        parent.email,
        "Запрос на привязку ученика в Smart Tracker",
        (
            f"Ученик {student_name} (@{current_user.username}) отправил вам запрос на привязку.\n"
            "Откройте профиль безопасности в Smart Tracker и подтвердите или отклоните запрос."
        ),
    )
    return RedirectResponse("/profile/security?success=link_requested", status_code=303)


@router.post("/profile/security/student-parent/{link_id}/decision")
def decide_parent_link(link_id: int, request: Request, decision: str = Form(...), db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    link = db.get(StudentParentLink, link_id)
    if not link or current_user.role != UserRole.parent or link.parent_id != current_user.id:
        return RedirectResponse("/profile/security", status_code=303)
    if decision == "approve":
        link.status = StudentParentLinkStatus.confirmed
        link.confirmed_at = datetime.now(UTC).replace(tzinfo=None)
        student_message = (
            f"Родитель @{current_user.username} подтвердил вашу привязку в Smart Tracker."
        )
    else:
        link.status = StudentParentLinkStatus.rejected
        student_message = (
            f"Родитель @{current_user.username} отклонил запрос на привязку в Smart Tracker."
        )
    db.commit()
    student = db.get(User, link.student_id)
    if student:
        send_task_to_student(student.telegram_chat_id, student_message)
        send_plain_email(
            student.email,
            "Статус привязки родителя в Smart Tracker",
            student_message + "\nПроверьте раздел профиля для актуального статуса.",
        )
    return RedirectResponse("/profile/security", status_code=303)
