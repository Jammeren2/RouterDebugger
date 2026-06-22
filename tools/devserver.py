"""Локальный дев-запуск для preview/отладки. НЕ для продакшена.

Задаёт безопасные дефолты окружения и поднимает uvicorn. В Coolify переменные
приходят из env, поэтому этот файл там не используется.
"""
import os
import sys
from pathlib import Path

# корень проекта в sys.path, чтобы пакет app импортировался при любом cwd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("APP_USERNAME", "admin")
os.environ.setdefault("APP_PASSWORD", "test12345")
os.environ.setdefault("SECRET_KEY", "devsecretdevsecretdevsecretdev01")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ROUTER_URL", "http://192.168.0.1:8080")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8021)
