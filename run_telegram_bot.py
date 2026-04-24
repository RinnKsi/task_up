import asyncio
import sys
from pathlib import Path

# Запуск через `python scripts/run_telegram_bot.py` (cwd = корень проекта hakaton)
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from app.bots.telegram_bot import run_telegram_bot


if __name__ == "__main__":
    asyncio.run(run_telegram_bot())
