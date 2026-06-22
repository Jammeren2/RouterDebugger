"""Аутентификация, защита от перебора и CSRF.

Хранилище попыток входа — в памяти процесса. Для одного инстанса в Coolify
этого достаточно. При горизонтальном масштабировании нужно вынести в Redis.
"""
from __future__ import annotations

import hmac
import secrets
import time

from fastapi import HTTPException, Request, status

from .config import settings


# ---------------------------------------------------------------------------
# Anti-bruteforce
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self) -> None:
        # ip -> {"fails": [timestamps], "locked_until": float}
        self._state: dict[str, dict] = {}

    def _now(self) -> float:
        return time.time()

    def check(self, key: str) -> float:
        """Возвращает 0, если можно пробовать, иначе — сколько секунд ждать."""
        rec = self._state.get(key)
        if not rec:
            return 0.0
        locked = rec.get("locked_until", 0)
        if locked and locked > self._now():
            return round(locked - self._now())
        return 0.0

    def register_failure(self, key: str) -> None:
        now = self._now()
        rec = self._state.setdefault(key, {"fails": [], "locked_until": 0})
        rec["fails"] = [t for t in rec["fails"] if now - t < settings.rl_window]
        rec["fails"].append(now)
        if len(rec["fails"]) >= settings.rl_max_attempts:
            rec["locked_until"] = now + settings.rl_lockout
            rec["fails"] = []

    def reset(self, key: str) -> None:
        self._state.pop(key, None)


rate_limiter = RateLimiter()


def client_key(request: Request) -> str:
    """Идентификатор клиента для рейт-лимита.

    За реверс-прокси Coolify реальный IP приходит в X-Forwarded-For.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
def verify_credentials(username: str, password: str) -> bool:
    u_ok = hmac.compare_digest(username or "", settings.app_username)
    p_ok = hmac.compare_digest(password or "", settings.app_password)
    # пустой пароль в конфиге => вход запрещён в любом случае
    return bool(settings.app_password) and u_ok and p_ok


# ---------------------------------------------------------------------------
# Session / CSRF
# ---------------------------------------------------------------------------
def login_session(request: Request, username: str) -> None:
    request.session["user"] = username
    request.session["ts"] = int(time.time())
    request.session.setdefault("csrf", secrets.token_urlsafe(32))


def logout_session(request: Request) -> None:
    request.session.clear()


def current_user(request: Request) -> str | None:
    user = request.session.get("user")
    if not user:
        return None
    ts = request.session.get("ts", 0)
    if time.time() - ts > settings.session_max_age:
        request.session.clear()
        return None
    return user


def get_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не авторизован")
    return user


def require_csrf(request: Request) -> None:
    sent = request.headers.get("x-csrf-token", "")
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF-токен недействителен")
