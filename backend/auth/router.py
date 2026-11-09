"""Auth endpoints — single login page, role auto-detected from email domain."""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel

from backend.auth.dependencies import get_current_user
from backend.auth.models import User
from backend.auth.service import (
    AuthError, create_access_token, link_telegram,
    get_notification_preferences, login, register, rotate_refresh_token,
    update_notification_preferences,
)
from backend.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])
_COOKIE = "fht_refresh"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    phone: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class TelegramLinkRequest(BaseModel):
    chat_id: str


class NotificationPreferencesRequest(BaseModel):
    email_enabled: bool = True
    telegram_enabled: bool = False
    telegram_chat_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_resp(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "phone": user.phone,
        "role": user.role,              # TRAVELER | DESK_STAFF | PLATFORM_ADMIN
        "department": user.department,
        "airline": user.airline,
        "telegram_linked": bool(user.telegram_chat_id),
        "email_verified": user.email_verified,
    }


def _set_refresh_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=_COOKIE, value=raw,
        httponly=True, secure=False,  # set secure=True + HTTPS in production
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(req: RegisterRequest, response: Response):
    """Register a new account.

    Role is auto-detected from the email domain — no separate signup pages.
    Example domains → roles (configurable via DOMAIN_ROLE_HINTS env var):
      • @atc.gov, @aai.aero  → DESK_STAFF
      • everything else       → TRAVELER
    PLATFORM_ADMIN is only assigned manually by another admin via /admin/users/{id}/role.
    """
    try:
        user = register(req.email, req.password, req.name, req.phone)
    except AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Simulated email verification — log token to console (no cost, no config needed)
    print(f"[AUTH] Verification email -> {user.email} | token=SIMULATED (auto-verified in dev)")
    _, access, refresh_raw = login(req.email, req.password)
    _set_refresh_cookie(response, refresh_raw)
    return {"access_token": access, "token_type": "bearer",
            "user": _user_resp(user),
            "message": "Welcome! Check your email to verify your account (simulated in dev)."}


@router.post("/login")
def login_user(req: LoginRequest, response: Response):
    """Single login for all roles. The returned `user.role` tells the frontend
    which UI to render — no separate admin/desk login pages exist."""
    try:
        user, access, refresh_raw = login(req.email, req.password)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    _set_refresh_cookie(response, refresh_raw)
    return {"access_token": access, "token_type": "bearer", "user": _user_resp(user)}


@router.post("/refresh")
def refresh_token(response: Response,
                  fht_refresh: Optional[str] = Cookie(default=None)):
    """Issue a new access token using the HttpOnly refresh cookie."""
    if not fht_refresh:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        user, new_raw = rotate_refresh_token(fht_refresh)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    access = create_access_token(user.id, user.role, user.department)
    _set_refresh_cookie(response, new_raw)
    return {"access_token": access, "token_type": "bearer", "user": _user_resp(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    """Return the authenticated user's profile and role."""
    return _user_resp(user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(_COOKIE)
    return {"ok": True}


@router.post("/link-telegram")
def link_telegram_account(req: TelegramLinkRequest,
                           user: User = Depends(get_current_user)):
    """Link user's Telegram chat_id so the bot can send them free notifications.

    How-to: User messages the bot on Telegram, bot replies with their chat_id,
    user pastes it here. See backend/notifications/service.py for bot setup.
    """
    updated = link_telegram(user.id, req.chat_id)
    return {"ok": True, "telegram_linked": bool(updated.telegram_chat_id)}


@router.get("/notification-preferences")
def notification_preferences(user: User = Depends(get_current_user)):
    return get_notification_preferences(user.id)


@router.put("/notification-preferences")
def save_notification_preferences(
    req: NotificationPreferencesRequest,
    user: User = Depends(get_current_user),
):
    try:
        return update_notification_preferences(
            user.id, req.email_enabled, req.telegram_enabled, req.telegram_chat_id,
        )
    except AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
