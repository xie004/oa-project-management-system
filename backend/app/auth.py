from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
from typing import Any

from fastapi import HTTPException, Request

from app.database import get_connection, get_setting, set_setting


ADMIN_USERNAME = "admin"
INITIAL_ADMIN_PASSWORD = "admin"
SESSION_COOKIE_NAME = "oa_admin_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
PASSWORD_ITERATIONS = 260_000


def _password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    )
    encoded = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${encoded}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(iterations),
    )
    encoded = base64.urlsafe_b64encode(digest).decode("ascii")
    return hmac.compare_digest(encoded, expected)


def ensure_auth_defaults(conn: sqlite3.Connection | None = None) -> None:
    owns_connection = conn is None
    conn = conn or get_connection()
    try:
        changed = False
        if not get_setting(conn, "admin_password_hash", ""):
            set_setting(conn, "admin_password_hash", _password_hash(INITIAL_ADMIN_PASSWORD))
            changed = True
        if not get_setting(conn, "admin_session_secret", ""):
            set_setting(conn, "admin_session_secret", secrets.token_urlsafe(32))
            changed = True
        if changed:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()


def _session_secret(conn: sqlite3.Connection) -> str:
    ensure_auth_defaults(conn)
    return str(get_setting(conn, "admin_session_secret", ""))


def authenticate_admin(username: str, password: str) -> bool:
    if username != ADMIN_USERNAME:
        return False
    conn = get_connection()
    try:
        ensure_auth_defaults(conn)
        stored_hash = str(get_setting(conn, "admin_password_hash", ""))
        return _verify_password(password, stored_hash)
    finally:
        conn.close()


def set_admin_password(current_password: str, new_password: str) -> None:
    new_password = new_password.strip()
    if len(new_password) < 4:
        raise HTTPException(status_code=400, detail="新密码至少需要 4 位")
    if not authenticate_admin(ADMIN_USERNAME, current_password):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    conn = get_connection()
    try:
        set_setting(conn, "admin_password_hash", _password_hash(new_password))
        conn.commit()
    finally:
        conn.close()


def create_session_token(username: str = ADMIN_USERNAME) -> str:
    conn = get_connection()
    try:
        secret = _session_secret(conn)
    finally:
        conn.close()
    expires_at = str(int(time.time()) + SESSION_TTL_SECONDS)
    nonce = secrets.token_urlsafe(16)
    payload = f"{username}|{expires_at}|{nonce}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{signature}"


def verify_session_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        username, expires_at, nonce, signature = token.split("|", 3)
    except ValueError:
        return False
    if username != ADMIN_USERNAME:
        return False
    try:
        expired = int(expires_at) < int(time.time())
    except ValueError:
        return False
    if expired:
        return False
    payload = f"{username}|{expires_at}|{nonce}"
    conn = get_connection()
    try:
        secret = _session_secret(conn)
    finally:
        conn.close()
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def admin_from_request(request: Request) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not verify_session_token(token):
        raise HTTPException(status_code=401, detail="请先管理员登录")
    return {"isAdmin": True, "username": ADMIN_USERNAME}


def current_auth(request: Request) -> dict[str, Any]:
    is_admin = verify_session_token(request.cookies.get(SESSION_COOKIE_NAME))
    return {"isAdmin": is_admin, "username": ADMIN_USERNAME if is_admin else ""}
