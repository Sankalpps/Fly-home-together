"""Notification dispatcher — 100% free channels only.

Channels:
  EMAIL    → Gmail SMTP via Python stdlib smtplib (free, needs Gmail App Password)
  TELEGRAM → Telegram Bot API (free, no approval, instant setup)

WhatsApp Business API is NOT used (requires paid Meta account & approved templates).
SMS is NOT used (all carriers charge per message).

Setup (all free):
  Gmail: Account → Security → 2-Step Verification → App Passwords → generate one
         Set GMAIL_USER and GMAIL_APP_PASSWORD env vars.
  Telegram: Message @BotFather → /newbot → copy TOKEN → set TELEGRAM_BOT_TOKEN
            Users must start a chat with your bot; they get their chat_id by
            messaging it, then link it via POST /auth/link-telegram.
"""
from __future__ import annotations

import json
import smtplib
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from backend.db import get_db, get_lock


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Email subjects & bodies
# ---------------------------------------------------------------------------

_SUBJECTS = {
    "JOIN_CONFIRM":          "✈ You've joined a pool — Fly Home Together",
    "NEAR_THRESHOLD":        "🔥 Your pool is almost full!",
    "QUOTE_READY":           "💰 Airline quote received — accept now",
    "QUOTE_ACCEPTED":        "✅ You accepted the quote",
    "TICKET_ISSUED":         "🎟 You're booked! Here's your PNR",
    "POOL_CANCELLED":        "❌ Pool cancelled — your hold is released",
    "QUOTE_EXPIRY_REMINDER": "⏰ Quote expires in 6 hours — act now",
}

_TELEGRAM_ICONS = {
    "JOIN_CONFIRM":          "✈",
    "NEAR_THRESHOLD":        "🔥",
    "QUOTE_READY":           "💰",
    "QUOTE_ACCEPTED":        "✅",
    "TICKET_ISSUED":         "🎟",
    "POOL_CANCELLED":        "❌",
    "QUOTE_EXPIRY_REMINDER": "⏰",
}


def _build_email_body(ntype: str, payload: dict) -> str:
    lines = [_SUBJECTS.get(ntype, ntype), ""]
    for k, v in payload.items():
        lines.append(f"  {k}: {v}")
    lines += ["", "— Fly Home Together"]
    return "\n".join(lines)


def _build_telegram_msg(ntype: str, payload: dict) -> str:
    icon = _TELEGRAM_ICONS.get(ntype, "📢")
    subject = _SUBJECTS.get(ntype, ntype)
    lines = [f"{icon} *{subject}*", ""]
    for k, v in payload.items():
        lines.append(f"• *{k}*: {v}")
    lines.append("\n_Fly Home Together_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

def _send_email(to: str, ntype: str, payload: dict) -> str:
    """Send via Gmail SMTP (stdlib smtplib, 100% free).
    Returns 'SENT' | 'FAILED' | 'SIMULATED'.
    """
    from backend.config import get_settings
    s = get_settings()
    if not s.email_enabled:
        _log("EMAIL", to, ntype, payload, simulated=True)
        return "SIMULATED"
    try:
        msg = EmailMessage()
        msg["Subject"] = _SUBJECTS.get(ntype, "Fly Home Together")
        msg["From"] = s.GMAIL_USER
        msg["To"] = to
        msg.set_content(_build_email_body(ntype, payload))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as srv:
            srv.login(s.GMAIL_USER, s.GMAIL_APP_PASSWORD)
            srv.send_message(msg)
        return "SENT"
    except Exception as e:
        print(f"[EMAIL] Failed -> {to}: {e}")
        return "FAILED"


def _send_telegram(chat_id: str, ntype: str, payload: dict) -> str:
    """Send via Telegram Bot API (100% free, no approval needed).
    Returns 'SENT' | 'FAILED' | 'SIMULATED'.
    """
    from backend.config import get_settings
    s = get_settings()
    if not s.telegram_enabled:
        _log("TELEGRAM", chat_id, ntype, payload, simulated=True)
        return "SIMULATED"
    try:
        text = _build_telegram_msg(ntype, payload)
        url = f"https://api.telegram.org/bot{s.TELEGRAM_BOT_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": text,
                           "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8)
        return "SENT"
    except Exception as e:
        print(f"[TELEGRAM] Failed -> {chat_id}: {e}")
        return "FAILED"


def _log(channel: str, recipient: str, ntype: str, payload: dict,
         simulated: bool = False) -> None:
    tag = "SIMULATED" if simulated else "SENDING"
    print(f"[NOTIFY:{channel}:{tag}] -> {recipient} | {ntype} | "
          f"{json.dumps(payload, default=str)[:100]}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def dispatch(
    notification_type: str,
    recipient_email: str,
    payload: dict,
    cohort_id: Optional[str] = None,
    user_id: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
) -> None:
    """Dispatch a notification to all available free channels for this user."""
    db = get_db()
    lock = get_lock()

    email_enabled = True
    telegram_enabled = bool(telegram_chat_id)
    if user_id:
        with lock:
            account = db.execute(
                "SELECT u.email, u.telegram_chat_id, "
                "COALESCE(p.email_enabled,1) AS email_enabled, "
                "COALESCE(p.telegram_enabled,0) AS telegram_enabled "
                "FROM users u LEFT JOIN user_notification_preferences p ON p.user_id=u.id "
                "WHERE u.id=?", (user_id,),
            ).fetchone()
        if account:
            recipient_email = account["email"]
            email_enabled = bool(account["email_enabled"])
            telegram_enabled = bool(account["telegram_enabled"])
            telegram_chat_id = account["telegram_chat_id"] if telegram_enabled else None

    if email_enabled:
        email_status = _send_email(recipient_email, notification_type, payload)
        with lock:
            db.execute(
                "INSERT INTO notifications(user_id,cohort_id,type,channel,status,payload_json,sent_at,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (user_id, cohort_id, notification_type, "EMAIL", email_status,
                 json.dumps(payload), _iso(), _iso())
            )
            db.commit()

    if telegram_chat_id and telegram_enabled:
        tg_status = _send_telegram(telegram_chat_id, notification_type, payload)
        with lock:
            db.execute(
                "INSERT INTO notifications(user_id,cohort_id,type,channel,status,payload_json,sent_at,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (user_id, cohort_id, notification_type, "TELEGRAM", tg_status,
                 json.dumps(payload), _iso(), _iso())
            )
            db.commit()


def dispatch_async(
    notification_type: str,
    recipient_email: str,
    payload: dict,
    cohort_id: Optional[str] = None,
    user_id: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
) -> None:
    """Fire-and-forget wrapper (non-blocking)."""
    t = threading.Thread(
        target=dispatch,
        kwargs=dict(
            notification_type=notification_type,
            recipient_email=recipient_email,
            payload=payload,
            cohort_id=cohort_id,
            user_id=user_id,
            telegram_chat_id=telegram_chat_id,
        ),
        daemon=True,
    )
    t.start()


def get_notification_log(limit: int = 50,
                         user_id: Optional[str] = None) -> list[dict]:
    db = get_db()
    if user_id:
        rows = db.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
