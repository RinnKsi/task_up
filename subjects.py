"""Справочник школьных предметов для профиля учителя."""

from __future__ import annotations

# value → отображаемое имя
SUBJECT_CHOICES: tuple[tuple[str, str], ...] = (
    ("math", "Математика / алгебра"),
    ("geometry", "Геометрия"),
    ("russian", "Русский язык"),
    ("literature", "Литература"),
    ("physics", "Физика"),
    ("chemistry", "Химия"),
    ("biology", "Биология"),
    ("history", "История"),
    ("social", "Обществознание"),
    ("english", "Иностранный язык"),
    ("informatics", "Информатика"),
    ("geography", "География"),
    ("art", "ИЗО / МХК"),
    ("pe", "Физкультура"),
    ("other", "Другое"),
)

SUBJECT_LABEL_BY_KEY: dict[str, str] = dict(SUBJECT_CHOICES)
ALLOWED_SUBJECT_KEYS = frozenset(SUBJECT_LABEL_BY_KEY.keys())


def normalize_teacher_subject_keys(keys: list[str] | None) -> list[str]:
    if not keys:
        return []
    out: list[str] = []
    for k in keys:
        key = (k or "").strip().lower()
        if key in ALLOWED_SUBJECT_KEYS and key not in out:
            out.append(key)
    return out


def teacher_subject_keys_from_json(json_str: str | None) -> list[str]:
    """Порядок предметов учителя как в JSON профиля (ключи из справочника)."""
    import json

    if not json_str:
        return []
    try:
        data = json.loads(json_str)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str) and item in ALLOWED_SUBJECT_KEYS and item not in out:
            out.append(item)
    return out


def subjects_labels_json_list(json_str: str | None) -> list[str]:
    import json

    if not json_str:
        return []
    try:
        data = json.loads(json_str)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    labels: list[str] = []
    for item in data:
        if isinstance(item, str) and item in SUBJECT_LABEL_BY_KEY:
            labels.append(SUBJECT_LABEL_BY_KEY[item])
    return labels
