"""Airline desk portal — visible only to DESK_STAFF and PLATFORM_ADMIN roles."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.auth.dependencies import require_desk
from backend.auth.models import User
from backend.db import get_db, get_lock

router = APIRouter(prefix="/desk", tags=["desk"])


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class QuoteSubmission(BaseModel):
    per_seat: int = Field(ge=500)
    seats_available: int = Field(ge=1, le=45)
    deposit_pct: int = Field(default=25, ge=10, le=100)
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/rfqs")
def list_rfqs(request: Request, user: User = Depends(require_desk)):
    """Open cohorts waiting for a quote (status=QUOTING)."""
    engine = request.app.state.engine
    # Airline-scoped: show QUOTING cohorts; admins see all
    return engine.list_cohorts(status="QUOTING")


@router.post("/rfqs/{cohort_id}/quote", status_code=201)
def submit_quote(cohort_id: str, body: QuoteSubmission,
                 request: Request, user: User = Depends(require_desk)):
    """Submit a manual quote for platform review."""
    db = get_db()
    cohort = request.app.state.engine.cohort_view(cohort_id)
    if cohort["status"] != "QUOTING":
        raise HTTPException(status_code=400, detail="This pool is not waiting for a quote")
    if body.seats_available < cohort["committed"]:
        raise HTTPException(status_code=400, detail="Seats offered must cover all joined tickets")
    sub_id = "rfq_" + uuid.uuid4().hex[:10]
    with get_lock():
        db.execute(
            "INSERT INTO rfq_submissions"
            "(id, cohort_id, airline, submitted_by, per_seat, seats_avail,"
            " deposit_pct, status, submitted_at, notes)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sub_id, cohort_id, user.airline or "Unknown", user.id,
             body.per_seat, body.seats_available, body.deposit_pct,
             "PENDING", _iso(), body.notes)
        )
        db.commit()
    return {"id": sub_id, "status": "PENDING",
            "message": "Quote submitted — pending platform review"}


@router.post("/rfqs/{cohort_id}/withdraw")
def withdraw_quote(cohort_id: str, request: Request,
                   user: User = Depends(require_desk)):
    """Withdraw a pending quote for a cohort."""
    db = get_db()
    with get_lock():
        db.execute(
            "UPDATE rfq_submissions SET status='WITHDRAWN'"
            " WHERE cohort_id=? AND submitted_by=? AND status='PENDING'",
            (cohort_id, user.id)
        )
        db.commit()
    return {"ok": True}


@router.get("/cohorts")
def desk_cohorts(request: Request, user: User = Depends(require_desk)):
    """Cohorts visible to this desk user (QUOTING + QUOTED)."""
    engine = request.app.state.engine
    quoting = engine.list_cohorts(status="QUOTING")
    quoted = engine.list_cohorts(status="QUOTED")
    return quoting + quoted


@router.get("/cohorts/{cohort_id}/passengers")
def passenger_review(cohort_id: str, request: Request,
                     user: User = Depends(require_desk)):
    """Passenger and waitlist review for flight/group-booking operations."""
    engine = request.app.state.engine
    try:
        cohort = engine.cohort_view(cohort_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    rows = engine.db.execute(
        "SELECT id, name, email, status, price_cap, hold_amount, pnr, joined_at "
        "FROM members WHERE cohort_id=? "
        "ORDER BY CASE status WHEN 'JOINED' THEN 1 WHEN 'ACCEPTED' THEN 2 "
        "WHEN 'TICKETED' THEN 3 WHEN 'WAITLIST' THEN 4 ELSE 5 END, joined_at",
        (cohort_id,),
    ).fetchall()
    waitlist_position = 0
    passengers = []
    for row in rows:
        passenger = dict(row)
        if passenger["status"] == "WAITLIST":
            waitlist_position += 1
            passenger["waitlist_position"] = waitlist_position
        else:
            passenger["waitlist_position"] = None
        passengers.append(passenger)
    return {"cohort": cohort, "passengers": passengers}


@router.get("/pnr-export/{cohort_id}")
def pnr_export(cohort_id: str, request: Request,
               user: User = Depends(require_desk)):
    """CSV passenger manifest for a ticketed cohort."""
    db = get_db()
    rows = db.execute(
        "SELECT name, email, pnr, status FROM members"
        " WHERE cohort_id=? AND status='TICKETED'",
        (cohort_id,)
    ).fetchall()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["name", "email", "pnr", "status"])
    writer.writeheader()
    writer.writerows([dict(r) for r in rows])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=manifest_{cohort_id}.csv"},
    )


@router.get("/my-submissions")
def my_submissions(user: User = Depends(require_desk)):
    """Desk submissions for the current user, or all submissions for admins."""
    if user.role == "PLATFORM_ADMIN":
        rows = get_db().execute(
            "SELECT * FROM rfq_submissions ORDER BY submitted_at DESC"
        ).fetchall()
    else:
        rows = get_db().execute(
            "SELECT * FROM rfq_submissions WHERE submitted_by=? ORDER BY submitted_at DESC",
            (user.id,)
        ).fetchall()
    return [dict(r) for r in rows]
