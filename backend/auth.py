"""Dashboard authentication helpers.

The dashboard is protected by a short Telegram login code and an HttpOnly
session cookie. Telegram remains the identity provider for the single-user
deployment, while an optional env password can be kept as a recovery path.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.config import TELEGRAM_BOT_TOKEN
from backend.db.repository import get_conn


AUTH_COOKIE_NAME = os.environ.get("AUTH_COOKIE_NAME", "stock_run_session")
AUTH_CODE_TTL_SECONDS = int(os.environ.get("AUTH_CODE_TTL_SECONDS", "300"))
AUTH_SESSION_DAYS = int(os.environ.get("AUTH_SESSION_DAYS", "7"))
AUTH_MAX_CODE_ATTEMPTS = int(os.environ.get("AUTH_MAX_CODE_ATTEMPTS", "5"))
AUTH_VERIFY_RATE_LIMIT = int(os.environ.get("AUTH_VERIFY_RATE_LIMIT", "20"))
AUTH_VERIFY_RATE_WINDOW_SECONDS = int(os.environ.get("AUTH_VERIFY_RATE_WINDOW_SECONDS", "600"))
AUTH_PASSWORD_RATE_LIMIT = int(os.environ.get("AUTH_PASSWORD_RATE_LIMIT", "10"))
AUTH_PASSWORD_RATE_WINDOW_SECONDS = int(os.environ.get("AUTH_PASSWORD_RATE_WINDOW_SECONDS", "600"))
_EPHEMERAL_AUTH_SECRET = secrets.token_urlsafe(32)
logger = logging.getLogger(__name__)

_PUBLIC_PREFIXES = (
    "/api/auth/",
    "/api/health",
)
_PROTECTED_DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}


class VerifyCodeRequest(BaseModel):
    code: str


class PasswordLoginRequest(BaseModel):
    password: str


def auth_enabled() -> bool:
    return os.environ.get("DASHBOARD_AUTH_ENABLED", "1") != "0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _secret() -> str:
    configured = os.environ.get("AUTH_SECRET") or TELEGRAM_BOT_TOKEN or os.environ.get("DASHBOARD_ADMIN_PASSWORD")
    if configured:
        return configured
    logger.warning("AUTH_SECRET/TELEGRAM_BOT_TOKEN is not configured; using ephemeral per-process auth secret")
    return _EPHEMERAL_AUTH_SECRET


def _hash(value: str) -> str:
    return hashlib.sha256(f"{_secret()}:{value}".encode("utf-8")).hexdigest()


def _split_ids(raw: str) -> list[str]:
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def _setting(conn, key: str) -> str:
    row = conn.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
    return str(row["value"] or "") if row else ""


def _set_setting(conn, key: str, value: str):
    conn.execute(
        """INSERT INTO system_settings (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
           value=excluded.value, updated_at=datetime('now')""",
        (key, value),
    )


def get_allowed_telegram_user_ids() -> list[str]:
    """Return Telegram user ids allowed to request dashboard login codes."""
    env_ids = _split_ids(os.environ.get("AUTH_ALLOWED_TELEGRAM_USERS", ""))
    if env_ids:
        return env_ids

    conn = get_conn()
    try:
        setting_ids = _split_ids(_setting(conn, "auth_allowed_telegram_users"))
        if setting_ids:
            return setting_ids

        rows = conn.execute(
            """SELECT DISTINCT chat_id FROM telegram_binding
               WHERE enabled=1 AND chat_id GLOB '[0-9]*'
               ORDER BY id LIMIT 5"""
        ).fetchall()
        seeded = [str(r["chat_id"]) for r in rows if str(r["chat_id"]).isdigit()]
        if seeded:
            _set_setting(conn, "auth_allowed_telegram_users", ",".join(seeded))
            conn.commit()
        return seeded
    finally:
        conn.close()


def is_allowed_telegram_user(user_id: str, username: str = "") -> bool:
    ids = set(get_allowed_telegram_user_ids())
    if str(user_id or "") in ids:
        return True
    allowed_names = {x.lower().lstrip("@") for x in _split_ids(os.environ.get("AUTH_ALLOWED_TELEGRAM_USERNAMES", ""))}
    return bool(username and username.lower().lstrip("@") in allowed_names)


def generate_login_code(telegram_user_id: str, chat_id: str = "", username: str = "") -> dict:
    if not is_allowed_telegram_user(telegram_user_id, username):
        return {"ok": False, "error": "当前 Telegram 用户没有看板登录权限。"}

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = _now() + timedelta(seconds=AUTH_CODE_TTL_SECONDS)
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO auth_login_code
               (code_hash, telegram_user_id, chat_id, username, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (_hash(code), str(telegram_user_id), str(chat_id or ""), username or "", _iso(expires_at)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "code": code, "expires_at": _iso(expires_at), "ttl_seconds": AUTH_CODE_TTL_SECONDS}


def format_login_code_message(result: dict, telegram_user_id: str) -> str:
    if not result.get("ok"):
        return f"登录验证码生成失败: {result.get('error', '')}\n你的 Telegram user_id: {telegram_user_id}"
    minutes = max(1, int(result.get("ttl_seconds") or AUTH_CODE_TTL_SECONDS) // 60)
    return (
        "看板登录验证码\n\n"
        f"{result['code']}\n\n"
        f"{minutes} 分钟内有效。验证码只能使用一次。"
    )


def _create_session(telegram_user_id: str, username: str = "") -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(days=AUTH_SESSION_DAYS)
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO auth_session
               (session_hash, telegram_user_id, username, expires_at)
               VALUES (?, ?, ?, ?)""",
            (_hash(token), str(telegram_user_id), username or "", _iso(expires_at)),
        )
        conn.commit()
    finally:
        conn.close()
    return token, _iso(expires_at)


def _cookie_secure() -> bool:
    return os.environ.get("AUTH_COOKIE_SECURE", "0") == "1"


def set_session_cookie(response: Response, token: str):
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=AUTH_SESSION_DAYS * 24 * 3600,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response):
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


def get_session_from_request(request: Request) -> dict | None:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    token_hash = _hash(token)
    now = _now()
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM auth_session
               WHERE session_hash=? AND revoked_at IS NULL
               ORDER BY id DESC LIMIT 1""",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        expires_at = _dt(row["expires_at"])
        if not expires_at or expires_at <= now:
            return None
        conn.execute("UPDATE auth_session SET last_seen_at=datetime('now') WHERE id=?", (row["id"],))
        conn.commit()
        return {
            "telegram_user_id": row["telegram_user_id"],
            "username": row["username"] or "",
            "expires_at": row["expires_at"],
        }
    finally:
        conn.close()


def revoke_session(request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE auth_session SET revoked_at=datetime('now') WHERE session_hash=? AND revoked_at IS NULL",
            (_hash(token),),
        )
        conn.commit()
    finally:
        conn.close()


def _verify_login_code(code: str) -> dict | None:
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM auth_login_code
               WHERE code_hash=? AND used_at IS NULL
               ORDER BY id DESC LIMIT 1""",
            (_hash(code),),
        ).fetchone()
        if not row:
            return None
        if int(row["attempt_count"] or 0) >= AUTH_MAX_CODE_ATTEMPTS:
            return None
        expires_at = _dt(row["expires_at"])
        if not expires_at or expires_at <= _now():
            return None
        conn.execute(
            "UPDATE auth_login_code SET used_at=datetime('now'), attempt_count=attempt_count+1 WHERE id=?",
            (row["id"],),
        )
        conn.commit()
        return {"telegram_user_id": row["telegram_user_id"], "username": row["username"] or ""}
    finally:
        conn.close()


def _password_enabled() -> bool:
    return bool(os.environ.get("DASHBOARD_ADMIN_PASSWORD") or os.environ.get("AUTH_ADMIN_PASSWORD"))


def _verify_password(password: str) -> bool:
    configured = os.environ.get("DASHBOARD_ADMIN_PASSWORD") or os.environ.get("AUTH_ADMIN_PASSWORD") or ""
    return bool(configured) and secrets.compare_digest(password or "", configured)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    now = _now()
    key_hash = _hash(f"rate:{key}")
    conn = get_conn()
    try:
        row = conn.execute("SELECT count, reset_at FROM auth_rate_limit WHERE key_hash=?", (key_hash,)).fetchone()
        reset_at = _dt(row["reset_at"]) if row else None
        if not row or not reset_at or reset_at <= now:
            conn.execute(
                """INSERT INTO auth_rate_limit (key_hash, count, reset_at, updated_at)
                   VALUES (?, 1, ?, datetime('now'))
                   ON CONFLICT(key_hash) DO UPDATE SET
                   count=1, reset_at=excluded.reset_at, updated_at=datetime('now')""",
                (key_hash, _iso(now + timedelta(seconds=window_seconds))),
            )
            conn.commit()
            return True
        count = int(row["count"] or 0)
        if count >= limit:
            return False
        conn.execute(
            "UPDATE auth_rate_limit SET count=count+1, updated_at=datetime('now') WHERE key_hash=?",
            (key_hash,),
        )
        conn.commit()
        return True
    finally:
        conn.close()


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def auth_me(request: Request):
    if not auth_enabled():
        return {"authenticated": True, "auth_enabled": False, "password_enabled": _password_enabled()}
    session = get_session_from_request(request)
    return {
        "authenticated": bool(session),
        "auth_enabled": True,
        "password_enabled": _password_enabled(),
        "user": session,
    }


@router.get("/status")
async def auth_status():
    allowed = get_allowed_telegram_user_ids()
    return {
        "auth_enabled": auth_enabled(),
        "password_enabled": _password_enabled(),
        "telegram_login_enabled": bool(allowed),
        "allowed_count": len(allowed),
        "code_ttl_seconds": AUTH_CODE_TTL_SECONDS,
    }


@router.post("/verify-code")
async def auth_verify_code(req: VerifyCodeRequest, request: Request, response: Response):
    if not _check_rate_limit(f"verify:{_client_ip(request)}", AUTH_VERIFY_RATE_LIMIT, AUTH_VERIFY_RATE_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="验证码尝试过于频繁，请稍后再试")
    identity = _verify_login_code(req.code)
    if not identity:
        raise HTTPException(status_code=401, detail="验证码无效或已过期")
    token, expires_at = _create_session(identity["telegram_user_id"], identity.get("username", ""))
    set_session_cookie(response, token)
    return {"ok": True, "expires_at": expires_at, "user": identity}


@router.post("/login-password")
async def auth_login_password(req: PasswordLoginRequest, request: Request, response: Response):
    if not _check_rate_limit(f"password:{_client_ip(request)}", AUTH_PASSWORD_RATE_LIMIT, AUTH_PASSWORD_RATE_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="密码尝试过于频繁，请稍后再试")
    if not _verify_password(req.password):
        raise HTTPException(status_code=401, detail="密码错误")
    token, expires_at = _create_session("password-admin", "admin")
    set_session_cookie(response, token)
    return {"ok": True, "expires_at": expires_at, "user": {"telegram_user_id": "password-admin", "username": "admin"}}


@router.post("/logout")
async def auth_logout(request: Request, response: Response):
    revoke_session(request)
    clear_session_cookie(response)
    return {"ok": True}


class DashboardAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not auth_enabled():
            return await call_next(request)
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)
        protected = path.startswith("/api/") or path in _PROTECTED_DOC_PATHS
        if not protected or any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
            return await call_next(request)
        if get_session_from_request(request):
            return await call_next(request)
        return JSONResponse({"detail": "未登录"}, status_code=401)
