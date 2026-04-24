from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


# timeout: ждать освобождения SQLite при параллельном доступе (uvicorn --reload, фоновые задачи)
_engine_connect_args = {"check_same_thread": False, "timeout": 30}
if settings.database_url.startswith("sqlite"):
    engine = create_engine(settings.database_url, connect_args=_engine_connect_args)
else:
    engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
