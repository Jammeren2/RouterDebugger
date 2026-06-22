"""Конфигурация приложения. Всё берётся из переменных окружения (env).

В Coolify эти переменные задаются в разделе Environment Variables сервиса
(или в docker-compose). Ни один секрет не хранится в коде.
"""
from __future__ import annotations

import logging
import os
import secrets

log = logging.getLogger("routerdebugger.config")


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        # --- Доступ к самой панели (логин, который вводишь ТЫ) ---
        self.app_username: str = os.getenv("APP_USERNAME", "admin")
        self.app_password: str = os.getenv("APP_PASSWORD", "")

        # Ключ для подписи cookie-сессий. ОБЯЗАТЕЛЬНО задать в проде и держать
        # постоянным, иначе при каждом рестарте все сессии инвалидируются.
        self.secret_key: str = os.getenv("SECRET_KEY", "")
        if not self.secret_key:
            self.secret_key = secrets.token_hex(32)
            log.warning(
                "SECRET_KEY не задан — сгенерирован временный. Сессии не переживут "
                "рестарт. Задай SECRET_KEY в env для продакшена."
            )

        # --- Доступ к роутеру (логин, которым СЕРВЕР ходит на роутер) ---
        self.router_url: str = os.getenv("ROUTER_URL", "http://192.168.0.1:8080").rstrip("/")
        self.router_username: str = os.getenv("ROUTER_USERNAME", "admin")
        self.router_password: str = os.getenv("ROUTER_PASSWORD", "admin")
        self.router_timeout: float = float(os.getenv("ROUTER_TIMEOUT", "10"))

        # --- Сессии ---
        # Время жизни сессии в секундах (по умолчанию 8 часов).
        self.session_max_age: int = int(os.getenv("SESSION_MAX_AGE", str(8 * 3600)))
        # Ставить Secure-флаг на cookie. За TLS-прокси Coolify должно быть True.
        self.cookie_secure: bool = _bool("COOKIE_SECURE", True)

        # --- Анти-брутфорс на форму логина ---
        self.rl_max_attempts: int = int(os.getenv("RATE_LIMIT_ATTEMPTS", "5"))
        self.rl_window: int = int(os.getenv("RATE_LIMIT_WINDOW", "300"))      # окно подсчёта, сек
        self.rl_lockout: int = int(os.getenv("RATE_LIMIT_LOCKOUT", "900"))    # блокировка, сек

        # Разрешить «продвинутую» вкладку с сырыми GET-запросами к /userRpm/*.
        self.enable_raw_console: bool = _bool("ENABLE_RAW_CONSOLE", True)

    def validate(self) -> list[str]:
        """Возвращает список проблем конфигурации (для предупреждений в логе)."""
        problems: list[str] = []
        if not self.app_password:
            problems.append(
                "APP_PASSWORD пуст — вход в панель будет невозможен. Задай APP_PASSWORD."
            )
        if len(self.app_password) < 8 and self.app_password:
            problems.append("APP_PASSWORD короче 8 символов — используй длинный пароль.")
        return problems


settings = Settings()
