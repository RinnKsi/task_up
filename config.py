from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.paths import APP_ROOT, PROJECT_ROOT


def _env_file_candidates() -> tuple[str, ...]:
    files: list[str] = []
    prj = PROJECT_ROOT / ".env"
    if prj.is_file():
        files.append(str(prj))
    files.append(".env")
    return tuple(files)


class Settings(BaseSettings):
    app_name: str = "Smart Tracker MVP"
    database_url: str = "sqlite:///./smart_tracker.db"
    telegram_bot_token: str = ""
    # Имя бота без @ — для ссылок t.me на сайте (например your_task_up_bot)
    telegram_bot_username: str = ""
    email_verification_mode: str = "smtp"  # dev | smtp
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_tls: bool = True
    require_email_verification: bool = False
    parent_notifications_enabled: bool = True
    scheduler_interval_seconds: int = 60
    ai_provider: str = "ollama"  # openai | ollama
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma3:4b"
    ollama_timeout: int = 40
    # Лимит токенов на ответ (меньше — быстрее, слишком мало — обрезанный JSON). 256–512.
    ollama_num_predict: int = 384
    # Жёсткий бюджет на «умное превью» задания (один запрос вместо трёх).
    ollama_ingest_timeout_sec: int = 3
    stt_provider: str = "local_whisper"  # local_whisper | openai
    local_whisper_model: str = "tiny"
    local_whisper_device: str = "cpu"
    local_whisper_compute_type: str = "int8"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_transcribe_model: str = "whisper-1"
    openai_timeout: int = 12
    openai_temperature: float = 0.2
    openai_max_output_tokens: int = 500
    openai_retry_count: int = 2
    openai_proxy_url: str = ""
    imageai_model_path: str = ""
    # Yandex Cloud Vision OCR (batchAnalyze TEXT_DETECTION) — быстрый OCR для фото заданий
    yandex_vision_api_key: str = ""
    yandex_vision_iam_token: str = ""
    yandex_cloud_folder_id: str = ""
    yandex_vision_timeout: int = 22
    yandex_vision_language_codes: str = "ru,en"
    yandex_vision_text_model: str = "page"  # page | line
    yandex_vision_max_image_bytes: int = 3_500_000
    # Быстрый пайплайн фото: без LLM-подчистки OCR после облака, rule-based draft, ingest без assist/steps
    ocr_photo_fast: bool = True

    model_config = SettingsConfigDict(
        env_file=_env_file_candidates(),
        env_file_encoding="utf-8",
    )

    @model_validator(mode="after")
    def _resolve_sqlite_relative_path(self) -> "Settings":
        """sqlite:///./file.db относится к каталогу проекта (родитель пакета app), не к cwd."""
        u = self.database_url
        if isinstance(u, str) and u.startswith("sqlite:///./"):
            name = u.removeprefix("sqlite:///./")
            abs_path = (APP_ROOT.parent / name).resolve()
            object.__setattr__(self, "database_url", f"sqlite:///{abs_path.as_posix()}")
        return self


settings = Settings()


def telegram_bot_chat_url() -> str | None:
    """Публичная ссылка на чат с ботом для UI, или None если username не задан."""
    u = (settings.telegram_bot_username or "").strip().lstrip("@")
    return f"https://t.me/{u}" if u else None
