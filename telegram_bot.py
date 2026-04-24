import asyncio
from datetime import UTC, datetime, timedelta
import re

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.task import Task
from app.models.user import User, UserRole
from app.services.ai_gateway import get_transcribe_failure_reason, parse_task_draft, transcribe_voice
from app.services.task_creator import create_student_self_task, create_teacher_tasks
from app.services.telegram_auth import consume_telegram_link_code


async def run_telegram_bot() -> None:
    if not settings.telegram_bot_token:
        print("[Telegram] BOT TOKEN is not set, bot started in dry-run mode.")
        await asyncio.sleep(0.1)
        return

    session = AiohttpSession()
    bot = Bot(token=settings.telegram_bot_token, session=session)
    dp = Dispatcher()
    flows: dict[int, dict] = {}

    @dp.message(Command("start"))
    async def cmd_start(message: Message):
        await message.answer(
            "Smart Tracker MVP: /tasks - задачи, /help - команды, /newtask - добавить свою задачу (ученик)."
        )

    @dp.message(Command("help"))
    async def cmd_help(message: Message):
        await message.answer(
            "Команды:\n"
            "/tasks - показать задачи\n"
            "/link <код> - привязать Telegram\n"
            "/newtask - создать личную задачу (ученик)\n\n"
            "Голосовой сценарий:\n"
            "- Учитель: отправляет голос с заданием, бот извлекает поля и уточняет только недостающее.\n"
            "- Ученик: отправляет голос, бот создает личную задачу (или спрашивает дедлайн)."
        )

    @dp.message(Command("tasks"))
    async def cmd_tasks(message: Message):
        db = SessionLocal()
        try:
            chat_id = str(message.chat.id)
            user = db.scalars(select(User).where(User.telegram_chat_id == chat_id)).first()
            if not user:
                await message.answer("Пользователь не привязан. Используйте /link <код>.")
                return
            tasks = db.scalars(select(Task).where(Task.student_id == user.id).order_by(Task.created_at.desc())).all()
            if not tasks:
                await message.answer("Активных задач нет.")
                return
            lines = [f"#{t.id} {t.subject}: {t.description} [{t.status.value}]" for t in tasks[:5]]
            await message.answer("Ваши задачи:\n" + "\n".join(lines))
        finally:
            db.close()

    @dp.message(Command("link"))
    async def cmd_link(message: Message):
        payload = message.text.split(maxsplit=1) if message.text else []
        if len(payload) < 2:
            await message.answer("Использование: /link 123456")
            return
        code = payload[1].strip()
        db = SessionLocal()
        try:
            linked_user = consume_telegram_link_code(db, code=code, chat_id=str(message.chat.id))
            if not linked_user:
                await message.answer("Код недействителен или истек.")
                return
            await message.answer(f"Готово. Telegram привязан к аккаунту {linked_user.username}.")
        finally:
            db.close()

    @dp.message(Command("newtask"))
    async def cmd_newtask(message: Message):
        db = SessionLocal()
        try:
            chat_id = str(message.chat.id)
            user = db.scalars(select(User).where(User.telegram_chat_id == chat_id)).first()
            if not user or user.role != UserRole.student:
                await message.answer("Команда доступна только привязанному ученику.")
                return
            flows[message.chat.id] = {"mode": "student_text_wait_task", "user_id": user.id}
            await message.answer("Отправьте текст вашей задачи. Затем я уточню дедлайн.")
        finally:
            db.close()

    @dp.callback_query()
    async def on_callback(callback: CallbackQuery):
        chat_id = callback.message.chat.id if callback.message else None
        if chat_id is None:
            await callback.answer()
            return
        flow = flows.get(chat_id)
        data = callback.data or ""
        if data == "teacher_accept":
            if not flow or flow.get("mode") != "teacher_voice_form":
                await callback.answer("Сессия не найдена", show_alert=False)
                return
            db = SessionLocal()
            try:
                await _create_teacher_from_flow(callback.message, db, flow, fast_accept=False)
                flows.pop(chat_id, None)
            finally:
                db.close()
            await callback.answer("Готово")
            return
        if data in {"teacher_mode_class", "teacher_mode_single"}:
            if flow and flow.get("mode") == "teacher_voice_form":
                flow["assignment_mode"] = "class" if data.endswith("class") else "single"
                await callback.message.answer(
                    "Режим принят: class." if flow["assignment_mode"] == "class" else "Режим принят: single. Укажите логин ученика."
                )
            await callback.answer()
            return
        if data in {"student_due_tomorrow_20", "student_due_today_20"}:
            if flow and flow.get("mode") in {"student_text_wait_due", "student_voice_wait_due"}:
                user_id = flow["user_id"]
                now = datetime.now(UTC).replace(tzinfo=None)
                due = now.replace(hour=20, minute=0, second=0, microsecond=0)
                if data == "student_due_tomorrow_20":
                    due = due + timedelta(days=1)
                db = SessionLocal()
                try:
                    user = db.get(User, user_id)
                    if user:
                        task = create_student_self_task(
                            db,
                            student=user,
                            text=flow["text"],
                            due_at=due,
                            source_type=flow["source_type"],
                        )
                        flows.pop(chat_id, None)
                        await callback.message.answer(f"Личная задача создана (ID {task.id}).")
                finally:
                    db.close()
            await callback.answer("Дедлайн выбран")
            return
        await callback.answer()

    @dp.message()
    async def universal_handler(message: Message):
        if message.voice:
            await _handle_voice(message, bot, flows)
            return
        await _handle_text(message, flows)

    print("[Telegram] Bot initialized with /start, /tasks, /link, /help, /newtask handlers.")
    try:
        await dp.start_polling(bot)
    except TelegramNetworkError as exc:
        print(f"\n[Telegram] Нет связи с api.telegram.org. Проверьте интернет/VPN на этом ПК.\n{exc}\n")
        raise
    finally:
        await bot.session.close()


async def _handle_voice(message: Message, bot: Bot, flows: dict[int, dict]) -> None:
    db = SessionLocal()
    try:
        chat_id = str(message.chat.id)
        user = db.scalars(select(User).where(User.telegram_chat_id == chat_id)).first()
        if not user:
            await message.answer("Сначала привяжите аккаунт: /link <код>.")
            return

        try:
            file_info = await bot.get_file(message.voice.file_id)
            file_data = await bot.download_file(file_info.file_path)
            recognized = transcribe_voice(file_data.read(), filename="telegram_voice.ogg", mime_type="audio/ogg")
        except Exception:
            await message.answer("Не удалось скачать/распознать голос. Повторите попытку или отправьте текст.")
            return
        if not recognized:
            await message.answer(f"Голос не распознан: {get_transcribe_failure_reason()}")
            return
        draft = parse_task_draft(recognized, source_type="voice")

        if user.role == UserRole.teacher:
            extracted = _extract_teacher_fields(recognized, draft)
            flow = {"mode": "teacher_voice_form", "user_id": user.id, "text": recognized, "draft": draft, **extracted}
            flows[message.chat.id] = flow
            await message.answer(
                f"Распознано: {recognized}\n"
                f"Предмет: {draft.get('subject', '—')}, дедлайн: {draft.get('due_at', 'не найден')}\n"
                "Нажмите 'Принято' для создания. Если нужно уточнить — отправьте класс (7Б), "
                "или дедлайн YYYY-MM-DD HH:MM."
            )
            await message.answer(
                "Быстрые действия:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Принято", callback_data="teacher_accept")],
                        [
                            InlineKeyboardButton(text="Режим: class", callback_data="teacher_mode_class"),
                            InlineKeyboardButton(text="Режим: single", callback_data="teacher_mode_single"),
                        ],
                    ]
                ),
            )
            if flow.get("class_name") and flow.get("assignment_mode") == "class" and flow.get("due_at"):
                await _create_teacher_from_flow(message, db, flow, fast_accept=True)
                flows.pop(message.chat.id, None)
            return

        if user.role == UserRole.student:
            due = _parse_due(draft.get("due_at"))
            if due:
                task = create_student_self_task(db, student=user, text=recognized, due_at=due, source_type="voice")
                await message.answer(f"Готово. Личная задача создана (ID {task.id}).")
                return
            flows[message.chat.id] = {
                "mode": "student_voice_wait_due",
                "user_id": user.id,
                "text": recognized,
                "source_type": "voice",
            }
            await message.answer(
                f"Распознанный текст: {recognized}\nУкажите дедлайн в формате YYYY-MM-DD HH:MM."
            )
            await message.answer(
                "Или выберите быстрый дедлайн:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Сегодня 20:00", callback_data="student_due_today_20")],
                        [InlineKeyboardButton(text="Завтра 20:00", callback_data="student_due_tomorrow_20")],
                    ]
                ),
            )
            return

        await message.answer(f"Распознанный текст: {recognized}")
    finally:
        db.close()


async def _handle_text(message: Message, flows: dict[int, dict]) -> None:
    flow = flows.get(message.chat.id)
    if not flow or not message.text:
        return
    db = SessionLocal()
    try:
        if flow["mode"] == "student_text_wait_task":
            flows[message.chat.id] = {
                "mode": "student_text_wait_due",
                "user_id": flow["user_id"],
                "text": message.text.strip(),
                "source_type": "text",
            }
            await message.answer("Укажите дедлайн в формате YYYY-MM-DD HH:MM.")
            await message.answer(
                "Или быстрый выбор:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Сегодня 20:00", callback_data="student_due_today_20")],
                        [InlineKeyboardButton(text="Завтра 20:00", callback_data="student_due_tomorrow_20")],
                    ]
                ),
            )
            return

        if flow["mode"] in {"student_text_wait_due", "student_voice_wait_due"}:
            due = _parse_due(message.text.strip())
            if not due:
                await message.answer("Не понял дату. Формат: YYYY-MM-DD HH:MM")
                return
            user = db.get(User, flow["user_id"])
            if not user:
                flows.pop(message.chat.id, None)
                await message.answer("Пользователь не найден.")
                return
            task = create_student_self_task(db, student=user, text=flow["text"], due_at=due, source_type=flow["source_type"])
            flows.pop(message.chat.id, None)
            await message.answer(f"Личная задача создана (ID {task.id}).")
            return

        if flow["mode"] == "teacher_voice_form":
            text = message.text.strip()
            lowered = text.lower()
            if lowered == "принято":
                await _create_teacher_from_flow(message, db, flow, fast_accept=False)
                flows.pop(message.chat.id, None)
                return
            if lowered in {"class", "single"}:
                flow["assignment_mode"] = lowered
                await message.answer("Режим принят.")
                return
            if _looks_like_class(text):
                flow["class_name"] = text.upper()
                await message.answer("Класс принят.")
                return
            maybe_due = _parse_due(text)
            if maybe_due:
                flow["due_at"] = maybe_due
                await message.answer("Дедлайн принят. Напишите 'принято'.")
                return
            if flow.get("assignment_mode") == "single":
                student = db.scalars(select(User).where(User.username == text, User.role == UserRole.student)).first()
                if not student:
                    await message.answer("Ученик не найден. Повторите логин.")
                    return
                flow["student_id"] = student.id
                await message.answer("Ученик принят. Напишите 'принято'.")
                return
            await message.answer("Не понял ввод. Напишите 'принято', класс (7Б), режим class/single или дедлайн.")
    finally:
        db.close()


async def _create_teacher_from_flow(message: Message, db: Session, flow: dict, fast_accept: bool) -> None:
    teacher = db.get(User, flow["user_id"])
    if not teacher:
        await message.answer("Учитель не найден.")
        return
    due_value = flow.get("due_at") or _parse_due((flow.get("draft") or {}).get("due_at"))
    if not due_value:
        due_value = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1)
    created = create_teacher_tasks(
        db,
        teacher=teacher,
        text=flow["text"],
        due_at=due_value,
        assignment_mode=flow.get("assignment_mode", "class"),
        class_name=flow.get("class_name", ""),
        student_id=flow.get("student_id"),
        parent_id=None,
        source_type="voice",
        progress_comment="Создано из голоса в Telegram",
    )
    if created > 0:
        await message.answer(f"Принято. Создано задач: {created}. Данные загружены на сайт.")
    else:
        suffix = " (авто-режим)" if fast_accept else ""
        await message.answer(f"Недостаточно данных для создания{suffix}. Уточните класс/ученика и дедлайн.")


def _extract_teacher_fields(text: str, draft: dict) -> dict:
    extracted: dict = {"assignment_mode": "class", "class_name": "", "student_id": None, "due_at": None}
    due = _parse_due(draft.get("due_at"))
    if due:
        extracted["due_at"] = due
    class_match = re.search(r"\b(\d{1,2}[А-ЯA-Z]?)\b", text.upper())
    if class_match:
        extracted["class_name"] = class_match.group(1)
    lowered = text.lower()
    if "конкрет" in lowered or "ученик" in lowered:
        extracted["assignment_mode"] = "single"
    if "всем" in lowered or "классу" in lowered:
        extracted["assignment_mode"] = "class"
    return extracted


def _parse_due(value: str | None) -> datetime | None:
    if not value:
        return None
    if "T" in value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    text = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _looks_like_class(text: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}[а-яА-Яa-zA-Z]?", text.strip()))
