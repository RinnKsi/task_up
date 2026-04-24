import json
from urllib import parse, request as urllib_request

from app.core.config import settings


def send_task_to_student(student_chat_id: str | None, message: str) -> bool:
    if not student_chat_id or not settings.telegram_bot_token:
        print(f"[Telegram] demo send skipped: {message}")
        return False
    try:
        payload = parse.urlencode({"chat_id": student_chat_id, "text": message}).encode("utf-8")
        req = urllib_request.Request(
            url=f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            data=payload,
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=10) as response:
            response_body = response.read().decode("utf-8")
        parsed = json.loads(response_body)
        if not parsed.get("ok"):
            print(f"[Telegram] send failed: {parsed}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[Telegram] send error: {exc}")
        return False
