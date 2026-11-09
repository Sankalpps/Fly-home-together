"""Authentication service: register, login, JWT, password hashing."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from backend.auth.models import User
from backend.config import get_settings
from backend.db import get_db, get_lock

import bcrypt
if not hasattr(bcrypt, "__about__"):
    bcrypt.__about__ = type("about", (), {"__version__": getattr(bcrypt, "__version__", "4.2.1")})()

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
ROLES = {"TRAVELER", "DESK_STAFF", "PLATFORM_ADMIN"}


class AuthError(Exception):
    """Maps to HTTP 401/403."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(user_id: str, role: str,
                        department: Optional[str] = None) -> str:
    settings = get_settings()
    expire = _now() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": user_id, "role": role, "dept": department,
         "exp": expire, "type": "access"},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )


def create_refresh_token(user_id: str) -> str:
    """Store hashed token in DB; return raw token to caller."""
    settings = get_settings()
    raw = uuid.uuid4().hex + uuid.uuid4().hex
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = _now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    db = get_db()
    with get_lock():
        db.execute(
            "INSERT INTO refresh_tokens(token_hash, user_id, expires_at, created_at)"
            " VALUES (?,?,?,?)",
            (token_hash, user_id, _iso(expires), _iso())
        )
        db.commit()
    return raw


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.SECRET_KEY,
                             algorithms=[settings.ALGORITHM])
        if payload.get("type") != "access":
            raise AuthError("Not an access token")
        return payload
    except JWTError as e:
        raise AuthError(f"Invalid token: {e}")


def rotate_refresh_token(raw_token: str) -> tuple["User", str]:
    """Validate, delete old, issue new refresh token. Returns (user, new_raw)."""
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    db = get_db()
    with get_lock():
        row = db.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash=?", (token_hash,)
        ).fetchone()
        if not row:
            raise AuthError("Invalid or expired refresh token")
        if _now() > datetime.fromisoformat(row["expires_at"]):
            db.execute("DELETE FROM refresh_tokens WHERE token_hash=?", (token_hash,))
            db.commit()
            raise AuthError("Refresh token expired — please log in again")
        db.execute("DELETE FROM refresh_tokens WHERE token_hash=?", (token_hash,))
        db.commit()
    user = get_user_by_id(row["user_id"])
    new_raw = create_refresh_token(user.id)
    return user, new_raw


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def register(
    email: str, password: str, name: str,
    phone: Optional[str] = None,
    role: Optional[str] = None,
    department: Optional[str] = None,
    airline: Optional[str] = None,
) -> User:
    email = (email or "").strip().lower()
    name = (name or "").strip()
    if not email or "@" not in email:
        raise AuthError("A valid email address is required")
    if not name:
        raise AuthError("Name is required")
    if not password or len(password) < 6:
        raise AuthError("Password must be at least 6 characters")

    # Auto-assign role from email domain hint when not explicitly given
    if role is None:
        domain = email.split("@", 1)[1].lower()
        hints = get_settings().domain_hints()
        role = hints.get(domain, "TRAVELER")

    if role not in ROLES:
        raise AuthError(f"Unknown role '{role}'")

    db = get_db()
    with get_lock():
        if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            raise AuthError("That email is already registered")
        user_id = "u_" + uuid.uuid4().hex[:10]
        db.execute(
            "INSERT INTO users(id, email, name, phone, hashed_pw, role, department,"
            " airline, telegram_chat_id, email_verified, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,NULL,0,?)",
            (user_id, email, name, phone, hash_password(password),
             role, department, airline, _iso())
        )
        db.commit()
    return get_user_by_id(user_id)


def login(email: str, password: str) -> tuple["User", str, str]:
    """Returns (user, access_token, refresh_token_raw)."""
    email = (email or "").strip().lower()
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not verify_password(password, row["hashed_pw"]):
        raise AuthError("Invalid email or password")
    user = User.from_row(row)
    access = create_access_token(user.id, user.role, user.department)
    refresh_raw = create_refresh_token(user.id)
    return user, access, refresh_raw


def get_user_by_id(user_id: str) -> User:
    row = get_db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise AuthError("User not found")
    return User.from_row(row)


def update_role(user_id: str, new_role: str,
                department: Optional[str] = None,
                airline: Optional[str] = None) -> User:
    if new_role not in ROLES:
        raise AuthError(f"Unknown role '{new_role}'")
    db = get_db()
    with get_lock():
        db.execute(
            "UPDATE users SET role=?, department=?, airline=? WHERE id=?",
            (new_role, department, airline, user_id)
        )
        db.commit()
    return get_user_by_id(user_id)


def link_telegram(user_id: str, chat_id: str) -> User:
    """Store a user's Telegram chat_id so the bot can notify them."""
    db = get_db()
    with get_lock():
        db.execute("UPDATE users SET telegram_chat_id=? WHERE id=?", (chat_id, user_id))
        db.commit()
    return get_user_by_id(user_id)


def get_notification_preferences(user_id: str) -> dict:
    db = get_db()
    with get_lock():
        db.execute(
            "INSERT OR IGNORE INTO user_notification_preferences"
            "(user_id, email_enabled, telegram_enabled, updated_at) VALUES (?,1,0,?)",
            (user_id, _iso()),
        )
        db.commit()
    row = db.execute(
        "SELECT p.email_enabled, p.telegram_enabled, u.telegram_chat_id, u.email "
        "FROM user_notification_preferences p JOIN users u ON u.id=p.user_id "
        "WHERE p.user_id=?", (user_id,),
    ).fetchone()
    from backend.config import get_settings
    settings = get_settings()
    return {
        "email": row["email"],
        "email_enabled": bool(row["email_enabled"]),
        "telegram_enabled": bool(row["telegram_enabled"]),
        "telegram_linked": bool(row["telegram_chat_id"]),
        "gmail_configured": settings.email_enabled,
        "telegram_bot_configured": settings.telegram_enabled,
    }


def update_notification_preferences(
    user_id: str,
    email_enabled: bool,
    telegram_enabled: bool,
    telegram_chat_id: Optional[str] = None,
) -> dict:
    db = get_db()
    chat_id = (telegram_chat_id or "").strip()
    with get_lock():
        current = db.execute(
            "SELECT telegram_chat_id FROM users WHERE id=?", (user_id,),
        ).fetchone()
        if not current:
            raise AuthError("User not found")
        if chat_id:
            db.execute("UPDATE users SET telegram_chat_id=? WHERE id=?", (chat_id, user_id))
        elif telegram_enabled and not current["telegram_chat_id"]:
            raise AuthError("Link your Telegram chat before enabling Telegram alerts")
        db.execute(
            "INSERT INTO user_notification_preferences"
            "(user_id, email_enabled, telegram_enabled, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET email_enabled=excluded.email_enabled, "
            "telegram_enabled=excluded.telegram_enabled, updated_at=excluded.updated_at",
            (user_id, int(email_enabled), int(telegram_enabled), _iso()),
        )
        db.commit()
    return get_notification_preferences(user_id)


def list_users(limit: int = 50, offset: int = 0) -> list[dict]:
    rows = get_db().execute(
        "SELECT id, email, name, role, department, airline, telegram_chat_id,"
        " email_verified, created_at FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    return [dict(r) for r in rows]


def seed_default_users() -> None:
    """Pre-seed standard demonstration users so immediate single-login works.
    - user1@gmail.com -> TRAVELER
    - ragu@atc.gov     -> DESK_STAFF (Flight Dept)
    - guru@atc.gov     -> PLATFORM_ADMIN (ATC Head / Admin)
    """
    defaults = [
        ("user1@gmail.com", "password123", "User One", "TRAVELER", None, None),
        ("ragu@atc.gov", "password123", "Ragu (Flight Dept)", "DESK_STAFF", "FLIGHT_OPERATIONS", "IndiGo"),
        ("guru@atc.gov", "password123", "Guru (Admin)", "PLATFORM_ADMIN", "ATC_HEADQUARTERS", None),
    ]
    db = get_db()
    with get_lock():
        for email, pw, name, role, dept, airline in defaults:
            row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if not row:
                uid = "u_" + uuid.uuid4().hex[:10]
                db.execute(
                    "INSERT INTO users(id, email, name, phone, hashed_pw, role, department,"
                    " airline, telegram_chat_id, email_verified, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,NULL,1,?)",
                    (uid, email, name, "+919876543210", hash_password(pw), role, dept, airline, _iso())
                )
        db.commit()

