"""Auth domain models (plain dataclasses — no ORM)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class User:
    id: str
    email: str
    name: str
    phone: Optional[str]
    hashed_pw: str
    role: str                   # TRAVELER | DESK_STAFF | PLATFORM_ADMIN
    department: Optional[str]   # For DESK_STAFF: OPS | PRICING | RAMP | …
    airline: Optional[str]      # For DESK_STAFF: which airline they represent
    telegram_chat_id: Optional[str]
    email_verified: bool
    created_at: str

    @classmethod
    def from_row(cls, row) -> "User":
        return cls(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            phone=row["phone"],
            hashed_pw=row["hashed_pw"],
            role=row["role"],
            department=row["department"],
            airline=row["airline"],
            telegram_chat_id=row["telegram_chat_id"],
            email_verified=bool(row["email_verified"]),
            created_at=row["created_at"],
        )
