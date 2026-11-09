"""Admin console — PLATFORM_ADMIN only.

NEVER shown in the public navigation. The React frontend only mounts
the admin panel when user.role === 'PLATFORM_ADMIN', and even then it
renders as a hidden overlay (accessible via /dashboard or Ctrl+Shift+A).
Regular users hit 403 on every endpoint here.
"""
from __future__ import annotations
import json
from typing import Optional
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.auth.dependencies import require_admin
from backend.auth.models import User
from backend.auth.service import list_users, update_role
from backend.db import get_db, get_lock
from backend.notifications.service import get_notification_log

router = APIRouter(prefix="/admin", tags=["admin"])


class RoleUpdate(BaseModel):
    role: str
    department: Optional[str] = None
    airline: Optional[str] = None


class ClockAdvance(BaseModel):
    hours: float = 25.0


class CohortEdit(BaseModel):
    origin: str
    dest: str
    travel_date: str
    threshold: int
    capacity: int


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------

@router.get("/stats")
def stats(request: Request, _: User = Depends(require_admin)):
    engine = request.app.state.engine
    summary = engine.savings_summary()
    db = get_db()
    return {
        **summary,
        "total_users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "total_notifications": db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0],
        "open_cohorts": len(engine.list_cohorts(status="OPEN")),
        "quoted_cohorts": len(engine.list_cohorts(status="QUOTED")),
        "ticketed_cohorts": len(engine.list_cohorts(status="TICKETED")),
    }


# ---------------------------------------------------------------------------
# Cohort management
# ---------------------------------------------------------------------------

@router.get("/cohorts")
def all_cohorts(request: Request,
                origin: Optional[str] = None,
                dest: Optional[str] = None,
                status: Optional[str] = None,
                _: User = Depends(require_admin)):
    return request.app.state.engine.list_cohorts(origin, dest, status)


@router.post("/cohorts/{cohort_id}/cancel")
def force_cancel(cohort_id: str, request: Request,
                 _: User = Depends(require_admin)):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from engine import EngineError
    engine = request.app.state.engine
    try:
        engine._cancel(cohort_id, "Admin force-cancelled")
        engine.db.commit()
        from backend.notifications.service import dispatch_async
        rows = engine.db.execute(
            "SELECT email FROM members WHERE cohort_id=?", (cohort_id,)
        ).fetchall()
        from backend.cohorts.router import _member_user_id
        for row in rows:
            dispatch_async(
                "POOL_CANCELLED", row["email"],
                {"cohort_id": cohort_id, "message": "Your pool was cancelled and holds were released."},
                cohort_id=cohort_id,
                user_id=_member_user_id(engine.db, row["email"]),
            )
        return engine.cohort_view(cohort_id)
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/cohorts/{cohort_id}/rfq")
def force_rfq(cohort_id: str, request: Request,
              _: User = Depends(require_admin)):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from engine import EngineError
    engine = request.app.state.engine
    try:
        cohort = engine.run_rfq(cohort_id)
        if cohort["status"] == "QUOTED":
            from backend.cohorts.router import _notify_members
            _notify_members(engine, cohort_id, "QUOTE_READY", {
                "route": f"{cohort['origin']} to {cohort['dest']}",
                "date": cohort["travel_date"],
                "airline": cohort["quote"]["airline"],
                "per_seat": cohort["quote"]["per_seat"],
                "quote_expires": cohort["quote_expires"],
            })
        return cohort
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/cohorts/{cohort_id}/members")
def cohort_members(cohort_id: str, request: Request,
                   _: User = Depends(require_admin)):
    row = request.app.state.engine.db.execute(
        "SELECT id, name, email, status, price_cap, hold_amount, joined_at "
        "FROM members WHERE cohort_id=? ORDER BY joined_at", (cohort_id,)
    ).fetchall()
    if not row:
        raise HTTPException(status_code=404, detail="Cohort not found or has no members")
    return [dict(member) for member in row]


@router.post("/cohorts/{cohort_id}/edit")
def edit_cohort(cohort_id: str, body: CohortEdit, request: Request,
                _: User = Depends(require_admin)):
    from engine import EngineError
    try:
        return request.app.state.engine.edit_cohort(
            cohort_id, body.origin, body.dest, body.travel_date,
            body.threshold, body.capacity,
        )
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/cohorts/{cohort_id}/reopen")
def reopen_cohort(cohort_id: str, request: Request,
                  _: User = Depends(require_admin)):
    from engine import EngineError
    try:
        return request.app.state.engine.reopen_cohort(cohort_id)
    except EngineError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

@router.get("/users")
def users(limit: int = 50, offset: int = 0, _: User = Depends(require_admin)):
    return list_users(limit, offset)


@router.post("/users/{user_id}/role")
def set_role(user_id: str, body: RoleUpdate, _: User = Depends(require_admin)):
    try:
        u = update_role(user_id, body.role, body.department, body.airline)
        return {
            "id": u.id, "email": u.email, "role": u.role,
            "department": u.department, "airline": u.airline,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Notification log
# ---------------------------------------------------------------------------

@router.get("/notifications")
def notifications(limit: int = 50, _: User = Depends(require_admin)):
    return get_notification_log(limit)


@router.post("/rfq-submissions/{submission_id}/complete")
def complete_rfq_submission(submission_id: str, request: Request,
                            _: User = Depends(require_admin)):
    """Approve a desk quote and publish it to the pool members."""
    db = get_db()
    engine = request.app.state.engine
    row = db.execute(
        "SELECT id, cohort_id, status, airline, per_seat, seats_avail, deposit_pct, notes "
        "FROM rfq_submissions WHERE id=?",
        (submission_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="RFQ submission not found")
    if row["status"] != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=f"RFQ submission is {row['status']}; only PENDING submissions can be completed",
        )
    cohort = engine.cohort_view(row["cohort_id"])
    if cohort["status"] != "QUOTING":
        raise HTTPException(status_code=400, detail="This pool is no longer waiting for a quote")
    if row["seats_avail"] < cohort["committed"]:
        raise HTTPException(status_code=400, detail="Quote does not cover all joined tickets")
    if row["per_seat"] >= cohort["market_fare"]:
        raise HTTPException(status_code=400, detail="The quote must be below the current market fare")

    from engine import QUOTE_VALIDITY_HOURS, SERVICE_FEE
    quote = {
        "airline": row["airline"],
        "per_seat": row["per_seat"],
        "seats_available": row["seats_avail"],
        "passengers": cohort["committed"],
        "service_fee": SERVICE_FEE,
        "saving_per_seat": cohort["market_fare"] - row["per_seat"],
        "deposit_pct": row["deposit_pct"],
        "notes": row["notes"],
        "all_quotes": [{"airline": row["airline"], "per_seat": row["per_seat"]}],
    }
    expires = (engine.now() + timedelta(hours=QUOTE_VALIDITY_HOURS)).isoformat(timespec="seconds")
    with get_lock():
        db.execute(
            "UPDATE rfq_submissions SET status='COMPLETED' WHERE id=?", (submission_id,),
        )
        db.execute(
            "UPDATE rfq_submissions SET status='NOT_SELECTED' "
            "WHERE cohort_id=? AND id<>? AND status='PENDING'",
            (row["cohort_id"], submission_id),
        )
        db.execute(
            "UPDATE cohorts SET status='QUOTED', quote_json=?, quote_expires=? WHERE id=?",
            (json.dumps(quote), expires, row["cohort_id"]),
        )
        db.commit()
    engine._log(row["cohort_id"],
                f"Approved quote from {row['airline']}: Rs {row['per_seat']} per seat")
    engine.db.commit()

    from backend.cohorts.router import _notify_members
    _notify_members(engine, row["cohort_id"], "QUOTE_READY", {
        "route": f"{cohort['origin']} to {cohort['dest']}",
        "date": cohort["travel_date"],
        "airline": row["airline"],
        "per_seat": row["per_seat"],
        "quote_expires": expires,
    })
    return {"id": submission_id, "cohort_id": row["cohort_id"],
            "status": "COMPLETED", "cohort_status": "QUOTED"}


# ---------------------------------------------------------------------------
# Demo / test controls (admin-only in production)
# ---------------------------------------------------------------------------

@router.post("/demo/advance")
def demo_advance(body: ClockAdvance, request: Request,
                 _: User = Depends(require_admin)):
    """Advance the engine clock (useful for demos and testing)."""
    engine = request.app.state.engine
    engine.advance_clock(body.hours)
    engine.tick()
    return {"ok": True, "advanced_hours": body.hours}


@router.post("/demo/fill/{cohort_id}")
def demo_fill(cohort_id: str, request: Request,
              _: User = Depends(require_admin)):
    """Fill a cohort to threshold with synthetic travelers."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from engine import EngineError
    engine = request.app.state.engine
    c = engine.cohort_view(cohort_id)
    need = max(0, c["threshold"] - c["committed"])
    for i in range(need):
        try:
            engine.join(
                f"Demo {i + 1}", f"demo{i}.{cohort_id[:6]}@example.com",
                c["origin"], c["dest"], c["travel_date"], 0
            )
        except EngineError:
            pass
    return engine.cohort_view(cohort_id)


@router.post("/demo/accept-all/{cohort_id}")
def demo_accept_all(cohort_id: str, request: Request,
                    _: User = Depends(require_admin)):
    """Make all JOINED members accept the quote."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from engine import EngineError
    engine = request.app.state.engine
    rows = engine.db.execute(
        "SELECT id FROM members WHERE cohort_id=? AND status='JOINED'", (cohort_id,)
    ).fetchall()
    for r in rows:
        try:
            engine.accept(r["id"])
        except EngineError:
            pass
    return engine.cohort_view(cohort_id)
