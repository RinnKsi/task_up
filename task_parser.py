"""Rule-based парсер задачи: используется когда AI недоступен (нет ключа,
403 региона, сеть недоступна). Извлекает предмет, дедлайн, описание даже
без "по <предмет>" — ищет по словарю школьных предметов.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta


# Канонические предметы + их ключевые слова / морфология
_SUBJECTS: list[tuple[str, list[str]]] = [
    ("Математика", ["математик", "алгебр", "геометр", "матем"]),
    ("Русский язык", ["русск", "русског"]),
    ("Литература", ["литератур", "чтени"]),
    ("Английский язык", ["английск", "english"]),
    ("Физика", ["физик"]),
    ("Химия", ["хими"]),
    ("Биология", ["биолог"]),
    ("География", ["географ"]),
    ("История", ["истори"]),
    ("Обществознание", ["обществ"]),
    ("Информатика", ["информат", "программирован"]),
    ("Окружающий мир", ["окружа"]),
    ("Физкультура", ["физкультур", "физ-ра", "физра"]),
    ("Музыка", ["музык"]),
    ("ИЗО", ["рисован", "изо"]),
    ("Технология", ["технолог"]),
    ("Немецкий язык", ["немецк"]),
    ("Французский язык", ["французск"]),
    ("Астрономия", ["астроном"]),
    ("Экономика", ["эконом"]),
]

# Подсказки «первого шага» в зависимости от предмета
_FIRST_STEP: dict[str, str] = {
    "Математика": "Выпишите номер задания и условие, начните с примера-образца.",
    "Русский язык": "Прочитайте правило по теме урока и разберите 1–2 примера.",
    "Литература": "Прочитайте первый фрагмент и выпишите ключевые мысли.",
    "Английский язык": "Повторите слова по теме и переведите первое предложение.",
    "Физика": "Запишите известные величины и нужную формулу.",
    "Химия": "Выпишите уравнение реакции и расставьте коэффициенты.",
    "Биология": "Прочитайте параграф и выпишите 3 ключевых термина.",
    "География": "Откройте карту/атлас и отметьте нужный объект.",
    "История": "Выпишите даты и действующих лиц события.",
    "Обществознание": "Сформулируйте тему своими словами в 1 предложение.",
    "Информатика": "Откройте задачу в редакторе и разберите пример входа-выхода.",
}


def _extract_subject(text: str) -> str:
    low = text.lower()
    m = re.search(r"по\s+([а-яё]+)", low)
    if m:
        word = m.group(1)
        for canon, keys in _SUBJECTS:
            if any(word.startswith(k) for k in keys):
                return canon
    for canon, keys in _SUBJECTS:
        if any(k in low for k in keys):
            return canon
    return "Предмет"


def _extract_due_at(text: str, now: datetime | None = None) -> datetime:
    low = text.lower()
    now = now or datetime.now()

    hour, minute = 20, 0
    hm = re.search(r"до\s+(\d{1,2})[:.](\d{2})", low)
    if hm:
        hour = max(0, min(23, int(hm.group(1))))
        minute = max(0, min(59, int(hm.group(2))))
    else:
        hm2 = re.search(r"до\s+(\d{1,2})\s*ч", low)
        if hm2:
            hour = max(0, min(23, int(hm2.group(1))))

    base_date = now.date()
    if "послезавтра" in low:
        base_date = (now + timedelta(days=2)).date()
    elif "завтра" in low:
        base_date = (now + timedelta(days=1)).date()
    elif "сегодня" in low or "сегодняшн" in low:
        base_date = now.date()
    elif not hm and not re.search(r"до\s+\d", low):
        base_date = (now + timedelta(days=1)).date()

    due = datetime.combine(base_date, datetime.min.time()).replace(hour=hour, minute=minute)
    if due <= now and "сегодня" in low:
        due = now + timedelta(hours=2)
    return due


def _extract_description(text: str, subject: str) -> str:
    cleaned = text
    cleaned = re.sub(r"^(задание|дз|домашка|д/з)[:\-\s]*", "", cleaned, flags=re.IGNORECASE).strip()
    if not cleaned:
        cleaned = text
    return cleaned[:500]


def _first_step(subject: str) -> str:
    return _FIRST_STEP.get(
        subject,
        "Откройте задание и прочитайте условие, чтобы ничего не упустить.",
    )


def parse_task_text(raw_text: str) -> dict:
    """Возвращает dict с полями для legacy fallback-пути.

    Новые потребители (ai_gateway._fallback_parse_task) используют `recommended_first_step`,
    поэтому он тоже тут возвращается.
    """
    text = (raw_text or "").strip()
    if not text:
        now = datetime.now()
        return {
            "subject": "Предмет",
            "description": "",
            "due_at": now + timedelta(days=1),
            "recommended_first_step": _first_step("Предмет"),
        }
    subject = _extract_subject(text)
    due_at = _extract_due_at(text)
    description = _extract_description(text, subject)
    return {
        "subject": subject,
        "description": description,
        "due_at": due_at,
        "recommended_first_step": _first_step(subject),
    }
