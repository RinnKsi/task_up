"""Абсолютные пути к ресурсам приложения (не зависят от cwd при запуске uvicorn)."""

from pathlib import Path

# app/core/paths.py -> каталог пакета app; родитель — корень проекта (hakaton/)
APP_ROOT: Path = Path(__file__).resolve().parent.parent
PROJECT_ROOT: Path = APP_ROOT.parent
STATIC_ROOT: Path = APP_ROOT / "static"
TEMPLATES_ROOT: Path = APP_ROOT / "templates"
