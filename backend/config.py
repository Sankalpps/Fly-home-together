"""Application configuration via environment variables.

All integrations default to FREE simulators.
Set the env vars below only when you have real accounts.
"""
from __future__ import annotations
import os
from functools import lru_cache


def _load_dotenv() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as env_file:
        for raw in env_file:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()


class Settings:
    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-in-production-32chars!!")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DB_PATH: str = os.getenv("DB_PATH", "/tmp/fht.db")

    # --- Flight data (AeroDataBox via RapidAPI — free dev tier available) ---
    AERODATABOX_API_KEY: str = os.getenv("AERODATABOX_API_KEY", "")

    # --- Notifications (100% FREE options only) ---

    # Email via Gmail SMTP (free — needs Gmail App Password)
    # Setup: Google Account → Security → 2-Step Verification → App Passwords
    GMAIL_USER: str = os.getenv("GMAIL_USER", "")          # your_email@gmail.com
    GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "")  # 16-char app password

    # Telegram Bot API (100% free, no approval needed)
    # Setup: message @BotFather on Telegram → /newbot → get TOKEN
    # Users subscribe by messaging your bot; bot stores their chat_id
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    # Domain hints: email domain → auto role assignment on registration
    # Format: comma-separated "domain:ROLE" pairs
    # Roles: TRAVELER | DESK_STAFF | PLATFORM_ADMIN
    DOMAIN_ROLE_HINTS: str = os.getenv(
        "DOMAIN_ROLE_HINTS",
        "atc.gov:DESK_STAFF,dgca.gov.in:DESK_STAFF,aai.aero:DESK_STAFF"
    )

    def domain_hints(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in self.DOMAIN_ROLE_HINTS.split(","):
            item = item.strip()
            if ":" in item:
                domain, role = item.split(":", 1)
                result[domain.strip().lower()] = role.strip().upper()
        return result

    @property
    def email_enabled(self) -> bool:
        return bool(self.GMAIL_USER and self.GMAIL_APP_PASSWORD)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN)

    @property
    def flight_api_enabled(self) -> bool:
        return bool(self.AERODATABOX_API_KEY)


@lru_cache
def get_settings() -> Settings:
    return Settings()
