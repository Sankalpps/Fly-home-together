"""FastAPI dependency injection for authentication."""
from __future__ import annotations
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from backend.auth.models import User
from backend.auth.service import AuthError, decode_access_token, get_user_by_id


def _bearer_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    return authorization.split(" ", 1)[1]


def get_current_user(token: str = Depends(_bearer_token)) -> User:
    try:
        payload = decode_access_token(token)
        return get_user_by_id(payload["sub"])
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "PLATFORM_ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Platform admin access required")
    return user


def require_desk(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("DESK_STAFF", "PLATFORM_ADMIN"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Airline desk staff access required")
    return user


def optional_user(authorization: Optional[str] = Header(None)) -> Optional[User]:
    """Returns current user if authenticated, else None. Never raises."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        token = authorization.split(" ", 1)[1]
        payload = decode_access_token(token)
        return get_user_by_id(payload["sub"])
    except AuthError:
        return None
