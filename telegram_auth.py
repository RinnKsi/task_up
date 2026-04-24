from datetime import UTC, datetime, timedelta
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def generate_telegram_link_code(db: Session, user: User, ttl_minutes: int = 10) -> str:
    code = f"{secrets.randbelow(10**6):06d}"
    user.telegram_link_code = code
    user.telegram_link_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=ttl_minutes)
    db.commit()
    return code


def consume_telegram_link_code(db: Session, code: str, chat_id: str) -> User | None:
    user = db.scalars(select(User).where(User.telegram_link_code == code)).first()
    if not user:
        return None
    expires_at = user.telegram_link_expires_at
    if not expires_at or expires_at < datetime.now(UTC).replace(tzinfo=None):
        return None
    user.telegram_chat_id = chat_id
    user.telegram_link_code = None
    user.telegram_link_expires_at = None
    db.commit()
    db.refresh(user)
    return user


def unlink_telegram(db: Session, user: User) -> None:
    user.telegram_chat_id = None
    user.telegram_link_code = None
    user.telegram_link_expires_at = None
    db.commit()
