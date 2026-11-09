"""Cohort (pool) router — wraps engine.py's CohortEngine with FastAPI endpoints."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.auth.dependencies import get_current_user, optional_user
from backend.auth.models import User
from backend.db import get_lock

router = APIRouter(prefix="/api", tags=["cohorts"])


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _engine(request: Request):
    return request.app.state.engine


def _fgw(request: Request):
    return request.app.state.flight_gateway


def _member_user_id(db, email: str) -> Optional[str]:
    normalized = (email or "").strip().lower()
    if "@" not in normalized:
        return None
    local, domain = normalized.rsplit("@", 1)
    match = re.fullmatch(r"(.+)\+ticket\d+", local)
    owner_email = f"{match.group(1)}@{domain}" if match else normalized
    row = db.execute("SELECT id FROM users WHERE email=?", (owner_email,)).fetchone()
    return row["id"] if row else None


def _notify_members(engine, cohort_id: str, notification_type: str, payload: dict) -> None:
    from backend.notifications.service import dispatch_async
    rows = engine.db.execute(
        "SELECT id, email, pnr FROM members WHERE cohort_id=? AND status IN "
        "('JOINED','ACCEPTED','TICKETED')", (cohort_id,)
    ).fetchall()
    for row in rows:
        member_payload = dict(payload)
        if notification_type == "TICKET_ISSUED":
            member_payload["pnr"] = row["pnr"] or ""
        dispatch_async(notification_type, row["email"], member_payload,
                       cohort_id=cohort_id,
                       user_id=_member_user_id(engine.db, row["email"]))


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class JoinRequest(BaseModel):
    name: str
    email: str
    origin: str
    dest: str
    date: str
    flex_days: int = 0
    price_cap: Optional[int] = None
    tickets: int = 1


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@router.get("/airports")
def airports():
    from backend.gateway.flights import INDIA_AIRPORTS
    return INDIA_AIRPORTS


@router.get("/config")
def config(request: Request):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from engine import next_festival_start
    engine = _engine(request)
    today = engine.now().date()
    return {
        "default_date": (next_festival_start(today) + timedelta(days=1)).isoformat(),
        "min_date": (today + timedelta(days=1)).isoformat(),
    }


# ---------------------------------------------------------------------------
# Pool endpoints
# ---------------------------------------------------------------------------

@router.get("/cohorts")
def list_cohorts(request: Request,
                 origin: Optional[str] = Query(None),
                 dest: Optional[str] = Query(None),
                 status: Optional[str] = Query(None)):
    engine = _engine(request)
    engine.tick()
    return engine.list_cohorts(origin, dest, status)


@router.get("/cohorts/{cohort_id}")
def get_cohort(cohort_id: str, request: Request):
    engine = _engine(request)
    engine.tick()
    try:
        return engine.cohort_view(cohort_id, include_members=True)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/members/{member_id}")
def get_member(member_id: str, request: Request):
    engine = _engine(request)
    engine.tick()
    try:
        return engine.member_view(member_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/estimate")
def estimate(request: Request,
             origin: str = Query(...),
             dest: str = Query(...),
             date: str = Query(...)):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from engine import EngineError
    try:
        return _engine(request).estimate(origin, dest, date)
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/summary")
def summary(request: Request):
    return _engine(request).savings_summary()


@router.get("/my-tickets")
def my_tickets(request: Request, user: User = Depends(get_current_user)):
    """Return all permanently stored tickets belonging to the signed-in user."""
    email = user.email.strip().lower()
    local, domain = email.rsplit("@", 1)
    db = _engine(request).db
    rows = db.execute(
        "SELECT m.id, m.name, m.email, m.pnr, m.status, m.joined_at, "
        "c.id AS cohort_id, c.origin, c.dest, c.travel_date, c.quote_json "
        "FROM members m JOIN cohorts c ON c.id=m.cohort_id "
        "WHERE m.status='TICKETED' AND "
        "(m.email=? OR m.email LIKE ? ESCAPE '\\') "
        "ORDER BY c.travel_date, m.joined_at",
        (email, f"{local}+ticket%@{domain}"),
    ).fetchall()
    tickets = []
    for row in rows:
        ticket = dict(row)
        quote = json.loads(ticket.pop("quote_json") or "{}")
        ticket["airline"] = quote.get("airline")
        ticket["fare"] = quote.get("per_seat")
        ticket["service_fee"] = quote.get("service_fee")
        ticket["total"] = (
            (quote.get("per_seat") or 0) + (quote.get("service_fee") or 0)
            if quote.get("per_seat") is not None else None
        )
        tickets.append(ticket)
    return {"tickets": tickets, "count": len(tickets)}


@router.get("/my-pools")
def my_pools(request: Request, user: User = Depends(get_current_user)):
    """Return every pool membership for the signed-in account."""
    email = user.email.strip().lower()
    local, domain = email.rsplit("@", 1)
    rows = _engine(request).db.execute(
        "SELECT m.id, m.name, m.email, m.status, m.pnr, m.joined_at, "
        "c.id AS cohort_id, c.origin, c.dest, c.travel_date, c.status AS cohort_status, "
        "c.threshold, c.capacity "
        "FROM members m JOIN cohorts c ON c.id=m.cohort_id "
        "WHERE m.email=? OR m.email LIKE ? ESCAPE '\\' "
        "ORDER BY c.travel_date, m.joined_at",
        (email, f"{local}+ticket%@{domain}"),
    ).fetchall()
    return {"pools": [dict(row) for row in rows], "count": len(rows)}


@router.post("/join", status_code=201)
def join(req: JoinRequest, request: Request,
         user: Optional[User] = Depends(optional_user)):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from engine import EngineError
    if req.tickets < 1 or req.tickets > 10:
        raise HTTPException(status_code=400, detail="Tickets must be between 1 and 10")

    engine = _engine(request)
    try:
        # A browser retry after a network timeout must not turn into a
        # confusing duplicate-membership error. Return the existing single
        # ticket; genuine new bulk requests still create all requested tickets.
        if req.tickets == 1:
            existing = engine.find_active_member(
                req.email, req.origin, req.dest, req.date,
            )
            original_email = req.email.strip().lower()
            if existing and existing["email"] == original_email:
                result = dict(existing)
                result["already_joined"] = True
                result["members"] = [existing]
                result["ticket_count"] = 1
                return result

        members = []
        for ticket_number in range(1, req.tickets + 1):
            suffix = "" if req.tickets == 1 else f"+ticket{ticket_number}"
            member_name = req.name if req.tickets == 1 else f"{req.name} (Ticket {ticket_number})"
            member_email = req.email if req.tickets == 1 else _ticket_email(req.email, suffix)
            members.append(engine.join(
                member_name, member_email, req.origin, req.dest,
                req.date, req.flex_days, req.price_cap, trigger_rfq=False,
            ))
        cohort_id = members[0]["cohort"]["id"]
        user_id = _member_user_id(engine.db, req.email)
        if user and user.email.strip().lower() == req.email.strip().lower():
            user_id = user.id
        cohort = engine.cohort_view(cohort_id)
        if cohort["committed"] >= cohort["threshold"] and cohort["status"] == "OPEN":
            engine.db.execute(
                "UPDATE cohorts SET status='QUOTING' WHERE id=?", (cohort_id,)
            )
            engine._log(cohort_id, "RFQ requested: pool reached its passenger threshold")
            engine.db.commit()
        result = dict(engine.member_view(members[0]["id"]))
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result["members"] = members
    result["ticket_count"] = len(members)

    # Confirm the booking request once, even when it contains several tickets.
    from backend.notifications.service import dispatch_async
    dispatch_async(
        notification_type="JOIN_CONFIRM",
        recipient_email=req.email,
        payload={
            "cohort_id": result["cohort"]["id"],
            "status": result["status"],
            "ticket_count": len(members),
            "route": f"{req.origin} to {req.dest}",
            "date": req.date,
        },
        cohort_id=result["cohort"]["id"],
        user_id=user_id,
    )
    return result


def _ticket_email(email: str, suffix: str) -> str:
    local, domain = email.rsplit("@", 1)
    return f"{local}{suffix}@{domain}"


@router.post("/members/{member_id}/quote-alert")
def send_quote_alert(member_id: str, request: Request,
                     user: User = Depends(get_current_user)):
    engine = _engine(request)
    member = engine.db.execute(
        "SELECT email, status FROM members WHERE id=?", (member_id,),
    ).fetchone()
    if not member:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if _member_user_id(engine.db, member["email"]) != user.id:
        raise HTTPException(status_code=403, detail="This ticket does not belong to your account")
    if member["status"] not in ("JOINED", "ACCEPTED", "TICKETED"):
        raise HTTPException(status_code=400, detail="This ticket is not active in the quote")
    try:
        cohort = engine.member_view(member_id)["cohort"]
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if cohort["status"] not in ("QUOTED", "TICKETED") or not cohort["quote"]:
        raise HTTPException(status_code=400, detail="This pool has no active quote")
    if not cohort["quote_expires"]:
        raise HTTPException(status_code=400, detail="This quote has no alert deadline")

    from backend.auth.service import get_notification_preferences
    preferences = get_notification_preferences(user.id)
    if not preferences["email_enabled"] and not preferences["telegram_enabled"]:
        return {"ok": True, "sent": False, "message": "Enable at least one alert channel first"}

    with get_lock():
        inserted = engine.db.execute(
            "INSERT OR IGNORE INTO quote_alerts_sent(user_id, cohort_id, quote_expires, sent_at) "
            "VALUES (?,?,?,?)",
            (user.id, cohort["id"], cohort["quote_expires"], _iso()),
        ).rowcount
        engine.db.commit()
    if not inserted:
        return {"ok": True, "sent": False, "message": "This quote was already sent to your selected channels"}

    from backend.notifications.service import dispatch_async
    dispatch_async("QUOTE_READY", user.email, {
        "route": f"{cohort['origin']} to {cohort['dest']}",
        "date": cohort["travel_date"],
        "airline": cohort["quote"]["airline"],
        "per_seat": cohort["quote"]["per_seat"],
        "quote_expires": cohort["quote_expires"],
    }, cohort_id=cohort["id"], user_id=user.id)
    return {"ok": True, "sent": True}


@router.post("/members/{member_id}/accept")
def accept(member_id: str, request: Request):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from engine import EngineError
    try:
        result = _engine(request).accept(member_id)
        if result.get("status") == "TICKETED":
            _notify_members(
                _engine(request), result["cohort"]["id"], "TICKET_ISSUED",
                {"route": f"{result['cohort']['origin']} → {result['cohort']['dest']}",
                 "pnr": result.get("pnr", ""),
                 "message": "Your group booking has been ticketed."},
            )
        return result
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/members/{member_id}/decline")
def decline(member_id: str, request: Request):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from engine import EngineError
    try:
        return _engine(request).decline(member_id)
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Live updates — Server-Sent Events (no extra package needed)
# ---------------------------------------------------------------------------

@router.get("/live/cohorts/{cohort_id}")
def live_cohort(cohort_id: str, request: Request):
    """SSE stream: push cohort state every 3 s when it changes."""
    import time, json
    engine = _engine(request)

    def stream():
        last_snapshot: Optional[str] = None
        while True:
            try:
                view = engine.cohort_view(cohort_id)
                snapshot = json.dumps(view, sort_keys=True)
                if snapshot != last_snapshot:
                    last_snapshot = snapshot
                    yield f"data: {snapshot}\n\n"
            except Exception:
                break
            time.sleep(3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Real-time flight data (Indian domestic only)
# ---------------------------------------------------------------------------

@router.get("/flights/departures/{iata}")
def flight_departures(iata: str, request: Request,
                      date: Optional[str] = Query(None)):
    from datetime import date as dt_date
    return _fgw(request).get_airport_departures(
        iata.upper(), date or dt_date.today().isoformat()
    )


@router.get("/flights/status/{flight_number}")
def flight_status(flight_number: str, request: Request,
                  date: Optional[str] = Query(None)):
    from datetime import date as dt_date
    result = _fgw(request).get_flight_status(
        flight_number, date or dt_date.today().isoformat()
    )
    if not result:
        raise HTTPException(status_code=404, detail="Flight not found")
    return result


@router.get("/flights/routes/{origin}")
def flight_routes(origin: str, request: Request):
    from backend.gateway.flights import INDIA_AIRPORTS
    dests = _fgw(request).get_routes(origin.upper())
    return {
        "origin": origin.upper(),
        "destinations": [
            {"iata": d, "name": INDIA_AIRPORTS.get(d, d)} for d in dests
        ],
    }
