"""Fly Home Together - cohort aggregation engine.

Pure-stdlib (sqlite3) implementation of the core domain:

* Travelers state an intent: origin, destination, date window, and a maximum
  price they are willing to pay.
* The engine pools them into a *cohort* for a route + date, using best-fit
  assignment for flexible date windows.
* Each member places an escrow-style *hold* (a pre-authorisation) so pools do
  not evaporate when quotes arrive.
* When a cohort reaches its threshold, the engine issues a Request for Quote
  (RFQ) to partner airlines. Airline quoting is SIMULATED here behind the
  ``AirlineGateway`` interface, so a real GDS / group-desk integration can be
  dropped in later.
* Members accept or decline the best quote. Declines free a seat and the
  waitlist is promoted automatically (attrition handling).
* When enough members accept, the cohort is ticketed and holds are captured.
  Otherwise the cohort is cancelled and every hold is released.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD = 31          # passengers needed to trigger an RFQ
DEFAULT_CAPACITY = 45           # hard cap per cohort, including attrition buffer
QUOTE_VALIDITY_HOURS = 24       # how long members have to accept a quote
SERVICE_FEE = 199               # flat per-passenger platform fee (INR)
HOLD_PERCENT = 0.20             # share of the price-cap held up front
MAX_DATE_FLEX_DAYS = 3          # a traveler may flex up to +/- 3 days

# Indicative surge multipliers used by the simulated gateway (demo only).
BASE_FARES = {
    ("BLR", "DEL"): 6000, ("DEL", "BLR"): 6000,
    ("BLR", "BOM"): 4500, ("BOM", "BLR"): 4500,
    ("BLR", "HYD"): 3200, ("HYD", "BLR"): 3200,
    ("BLR", "CCU"): 6500, ("CCU", "BLR"): 6500,
    ("BLR", "MAA"): 2800, ("MAA", "BLR"): 2800,
    ("BLR", "PNQ"): 4200, ("PNQ", "BLR"): 4200,
}

AIRPORTS = {
    "BLR": "Bengaluru", "DEL": "Delhi", "BOM": "Mumbai", "HYD": "Hyderabad",
    "CCU": "Kolkata", "MAA": "Chennai", "PNQ": "Pune",
}

AIRLINES = ["IndiGo", "Air India", "Akasa Air", "SpiceJet"]

# Festival windows -> surge multiplier on the base fare (demo data)
FESTIVAL_SURGE = [
    (date(2026, 11, 6), date(2026, 11, 12), 3.0, "Diwali"),
    (date(2026, 12, 22), date(2027, 1, 2), 2.2, "Christmas / New Year"),
]


class EngineError(Exception):
    """Raised for invalid operations (maps to HTTP 400 in the server)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        raise EngineError(f"Invalid date '{s}', expected YYYY-MM-DD")


def surge_multiplier(d: date) -> tuple[float, str]:
    for start, end, mult, name in FESTIVAL_SURGE:
        if start <= d <= end:
            return mult, name
    return 1.0, ""


def next_festival_start(today: date, lead_days: int = 3, room_days: int = 4) -> date:
    """First day (at least ``lead_days`` ahead) inside the next festival window
    that still has ``room_days`` of window left after that day.

    Falls back to ``today + 10`` when no suitable window is upcoming."""
    earliest = today + timedelta(days=lead_days)
    for start, end, _, _ in FESTIVAL_SURGE:
        if end >= earliest + timedelta(days=room_days):
            return max(start, earliest)
    return today + timedelta(days=10)


def market_fare(origin: str, dest: str, d: date) -> int:
    """Simulated *individual* (market) fare on a date, including festive surge."""
    base = BASE_FARES.get((origin, dest), 5000)
    mult, _ = surge_multiplier(d)
    # small deterministic jitter so different dates are not identical
    jitter = int(hashlib.sha256(f"{origin}{dest}{d}".encode()).hexdigest(), 16) % 400
    return int(base * mult) + jitter


# ---------------------------------------------------------------------------
# Airline gateway (simulated)
# ---------------------------------------------------------------------------

class AirlineGateway:
    """Interface a real airline / GDS group-desk integration must implement."""

    def request_quotes(self, origin: str, dest: str, travel_date: date,
                       passengers: int) -> list[dict]:
        raise NotImplementedError


class SimulatedAirlineGateway(AirlineGateway):
    """Returns plausible group quotes.

    The discount deliberately depends on how *tight* the market is: during
    surge periods airlines concede less (this mirrors reality and keeps the
    demo honest). ``seed`` makes quotes deterministic for tests.
    """

    def __init__(self, seed: Optional[int] = None, decline_rate: float = 0.0):
        self.rng = random.Random(seed)
        self.decline_rate = decline_rate

    def request_quotes(self, origin, dest, travel_date, passengers):
        market = market_fare(origin, dest, travel_date)
        mult, _ = surge_multiplier(travel_date)
        # Peak => airlines concede 15-30%; off-peak => 20-35%.
        lo, hi = (0.15, 0.30) if mult > 1.0 else (0.20, 0.35)
        quotes = []
        for airline in AIRLINES:
            if self.rng.random() < self.decline_rate:
                continue  # airline had no group inventory
            discount = self.rng.uniform(lo, hi)
            per_seat = int(round(market * (1 - discount), -1))
            quotes.append({
                "airline": airline,
                "per_seat": per_seat,
                "market_fare": market,
                "seats_available": passengers + self.rng.randint(0, 6),
                "deposit_pct": 25,
            })
        quotes.sort(key=lambda q: q["per_seat"])
        return quotes


# ---------------------------------------------------------------------------
# Payment gateway (simulated escrow)
# ---------------------------------------------------------------------------

class PaymentGateway:
    def authorize(self, user: str, amount: int) -> str:
        raise NotImplementedError

    def capture(self, auth_id: str, amount: int) -> None:
        raise NotImplementedError

    def release(self, auth_id: str) -> None:
        raise NotImplementedError


class SimulatedPaymentGateway(PaymentGateway):
    """In-memory stand-in for a PSP pre-authorisation API (e.g. UPI mandate /
    card auth). Replace with Razorpay / Stripe auth-and-capture in production."""

    def __init__(self):
        self.auths: dict[str, dict] = {}

    def authorize(self, user, amount):
        auth_id = "auth_" + uuid.uuid4().hex[:12]
        self.auths[auth_id] = {"user": user, "amount": amount, "state": "held"}
        return auth_id

    def capture(self, auth_id, amount):
        auth = self.auths.get(auth_id)
        if auth is None:
            return
        auth["state"] = "captured"
        auth["captured"] = amount

    def release(self, auth_id):
        auth = self.auths.get(auth_id)
        if auth is not None:
            auth["state"] = "released"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS cohorts (
    id            TEXT PRIMARY KEY,
    origin        TEXT NOT NULL,
    dest          TEXT NOT NULL,
    travel_date   TEXT NOT NULL,
    threshold     INTEGER NOT NULL,
    capacity      INTEGER NOT NULL,
    status        TEXT NOT NULL,   -- OPEN, QUOTING, QUOTED, TICKETED, CANCELLED
    created_at    TEXT NOT NULL,
    quote_json    TEXT,            -- chosen quote (JSON)
    quote_expires TEXT
);
CREATE TABLE IF NOT EXISTS members (
    id            TEXT PRIMARY KEY,
    cohort_id     TEXT NOT NULL REFERENCES cohorts(id),
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    price_cap     INTEGER NOT NULL,
    flex_days     INTEGER NOT NULL,
    status        TEXT NOT NULL,   -- JOINED, WAITLIST, ACCEPTED, DECLINED, EXPIRED, TICKETED, REFUNDED
    auth_id       TEXT,
    hold_amount   INTEGER NOT NULL,
    joined_at     TEXT NOT NULL,
    pnr           TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id TEXT NOT NULL,
    at        TEXT NOT NULL,
    message   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_members_cohort ON members(cohort_id);
CREATE INDEX IF NOT EXISTS idx_cohorts_route ON cohorts(origin, dest, travel_date, status);
CREATE TABLE IF NOT EXISTS engine_migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

ACTIVE_MEMBER = ("JOINED", "ACCEPTED")
OPEN_COHORT_STATES = ("OPEN",)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class CohortEngine:
    def __init__(self, db_path: str = ":memory:",
                 airline: Optional[AirlineGateway] = None,
                 payments: Optional[PaymentGateway] = None,
                 threshold: int = DEFAULT_THRESHOLD,
                 capacity: int = DEFAULT_CAPACITY):
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
        if db_path != ":memory:":
            try:
                self.db.execute("PRAGMA journal_mode=WAL;")
                self.db.execute("PRAGMA busy_timeout=10000;")
            except Exception:
                pass
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.airline = airline or SimulatedAirlineGateway()
        self.payments = payments or SimulatedPaymentGateway()
        self.threshold = threshold
        self.capacity = capacity
        # Test/demo hook: allows time to be advanced without sleeping.
        self._clock_offset = timedelta(0)
        self._migrate_group_quote_threshold()
        self._migrate_admin_quote_review()

    def _migrate_group_quote_threshold(self) -> None:
        """Lower active legacy pools to the 31-passenger quote trigger once.

        Pools stay open at the new threshold until an administrator chooses to
        request a quote.
        """
        migration = "group_quote_threshold_31"
        applied = self.db.execute(
            "SELECT 1 FROM engine_migrations WHERE name=?", (migration,)
        ).fetchone()
        if applied:
            return

        self.db.execute(
            "UPDATE cohorts SET threshold=MIN(?, capacity) "
            "WHERE status IN ('OPEN', 'QUOTING') AND threshold>?",
            (DEFAULT_THRESHOLD, DEFAULT_THRESHOLD),
        )
        self.db.execute(
            "INSERT INTO engine_migrations(name, applied_at) VALUES (?, ?)",
            (migration, _iso(_now())),
        )
        self.db.commit()

    def _migrate_admin_quote_review(self) -> None:
        """Return threshold-triggered legacy RFQs to admin review once."""
        migration = "admin_controls_quote_review_v1"
        applied = self.db.execute(
            "SELECT 1 FROM engine_migrations WHERE name=?", (migration,)
        ).fetchone()
        if applied:
            return

        has_submissions = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rfq_submissions'"
        ).fetchone()
        pending_cohorts = set()
        if has_submissions:
            pending_cohorts = {
                row["cohort_id"] for row in self.db.execute(
                    "SELECT DISTINCT cohort_id FROM rfq_submissions WHERE status='PENDING'"
                ).fetchall()
            }

        waiting = self.db.execute(
            "SELECT id FROM cohorts WHERE status='QUOTING' AND quote_json IS NULL"
        ).fetchall()
        for cohort in waiting:
            if cohort["id"] in pending_cohorts:
                continue
            self.db.execute(
                "UPDATE cohorts SET status='OPEN' WHERE id=?", (cohort["id"],)
            )
            self._log(cohort["id"], "Returned to open status for administrator quote review")
        self.db.execute(
            "INSERT INTO engine_migrations(name, applied_at) VALUES (?, ?)",
            (migration, _iso(_now())),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # -- clock ---------------------------------------------------------------
    def now(self) -> datetime:
        return _now() + self._clock_offset

    def advance_clock(self, hours: float) -> None:
        self._clock_offset += timedelta(hours=hours)

    # -- logging -------------------------------------------------------------
    def _log(self, cohort_id: str, message: str) -> None:
        self.db.execute("INSERT INTO events(cohort_id, at, message) VALUES (?,?,?)",
                        (cohort_id, _iso(self.now()), message))

    # -- queries -------------------------------------------------------------
    def _cohort_row(self, cohort_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM cohorts WHERE id=?", (cohort_id,)).fetchone()
        if not row:
            raise EngineError("Cohort not found")
        return row

    def _member_row(self, member_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
        if not row:
            raise EngineError("Member not found")
        return row

    def _count(self, cohort_id: str, *statuses: str) -> int:
        q = ",".join("?" * len(statuses))
        return self.db.execute(
            f"SELECT COUNT(*) FROM members WHERE cohort_id=? AND status IN ({q})",
            (cohort_id, *statuses)).fetchone()[0]

    def cohort_view(self, cohort_id: str, include_members: bool = False) -> dict:
        c = self._cohort_row(cohort_id)
        joined = self._count(cohort_id, "JOINED")
        accepted = self._count(cohort_id, "ACCEPTED")
        ticketed = self._count(cohort_id, "TICKETED")
        waitlist = self._count(cohort_id, "WAITLIST")
        d = _parse_date(c["travel_date"])
        view = {
            "id": c["id"],
            "origin": c["origin"], "origin_city": AIRPORTS.get(c["origin"], c["origin"]),
            "dest": c["dest"], "dest_city": AIRPORTS.get(c["dest"], c["dest"]),
            "travel_date": c["travel_date"],
            "status": c["status"],
            "threshold": c["threshold"], "capacity": c["capacity"],
            "joined": joined, "accepted": accepted, "ticketed": ticketed,
            "waitlist": waitlist,
            "committed": joined + accepted + ticketed,
            "market_fare": market_fare(c["origin"], c["dest"], d),
            "festival": surge_multiplier(d)[1],
            "quote": json.loads(c["quote_json"]) if c["quote_json"] else None,
            "quote_expires": c["quote_expires"],
            "created_at": c["created_at"],
        }
        view["spots_to_threshold"] = max(0, c["threshold"] - view["committed"])
        events = self.db.execute(
            "SELECT at, message FROM events WHERE cohort_id=? ORDER BY id DESC LIMIT 25",
            (cohort_id,)).fetchall()
        view["events"] = [dict(e) for e in events]
        if include_members:
            rows = self.db.execute(
                "SELECT id, name, status, joined_at FROM members WHERE cohort_id=? "
                "ORDER BY joined_at", (cohort_id,)).fetchall()
            view["members"] = [dict(r) for r in rows]
        return view

    def list_cohorts(self, origin: Optional[str] = None, dest: Optional[str] = None,
                     status: Optional[str] = None) -> list[dict]:
        sql, args = "SELECT id FROM cohorts WHERE 1=1", []
        if origin:
            sql += " AND origin=?"; args.append(origin.upper())
        if dest:
            sql += " AND dest=?"; args.append(dest.upper())
        if status:
            sql += " AND status=?"; args.append(status.upper())
        sql += " ORDER BY travel_date, created_at"
        return [self.cohort_view(r["id"]) for r in self.db.execute(sql, args).fetchall()]

    def edit_cohort(self, cohort_id: str, origin: str, dest: str,
                    travel_date: str, threshold: int, capacity: int) -> dict:
        c = self._cohort_row(cohort_id)
        if c["status"] not in ("OPEN", "QUOTING"):
            raise EngineError(f"Cohort is {c['status']}; only open RFQs can be edited")
        origin, dest = self._validate_route(origin, dest)
        d = _parse_date(travel_date)
        if d <= self.now().date():
            raise EngineError("Travel date must be in the future")
        threshold, capacity = int(threshold), int(capacity)
        committed = self._count(cohort_id, "JOINED", "ACCEPTED", "TICKETED")
        if threshold < 1:
            raise EngineError("Threshold must be at least 1")
        if capacity < threshold:
            raise EngineError("Capacity must be at least the threshold")
        if capacity < committed:
            raise EngineError(f"Capacity cannot be below {committed} committed tickets")
        self.db.execute(
            "UPDATE cohorts SET origin=?, dest=?, travel_date=?, threshold=?, capacity=? "
            "WHERE id=?",
            (origin, dest, d.isoformat(), threshold, capacity, cohort_id),
        )
        self._log(cohort_id, f"Admin edited pool to {origin}->{dest} on {d.isoformat()}")
        self.db.commit()
        return self.cohort_view(cohort_id)

    def reopen_cohort(self, cohort_id: str) -> dict:
        c = self._cohort_row(cohort_id)
        if c["status"] != "CANCELLED":
            raise EngineError(f"Cohort is {c['status']}; only cancelled pools can be reopened")
        if _parse_date(c["travel_date"]) <= self.now().date():
            raise EngineError("Cannot reopen a pool whose travel date has passed")
        self.db.execute(
            "UPDATE cohorts SET status='OPEN', quote_json=NULL, quote_expires=NULL "
            "WHERE id=?", (cohort_id,)
        )
        self._log(cohort_id, "Admin reopened the cancelled pool")
        self.db.commit()
        return self.cohort_view(cohort_id)

    def member_view(self, member_id: str) -> dict:
        m = self._member_row(member_id)
        return {
            "id": m["id"], "name": m["name"], "email": m["email"],
            "status": m["status"], "price_cap": m["price_cap"],
            "hold_amount": m["hold_amount"], "pnr": m["pnr"],
            "joined_at": m["joined_at"],
            "waitlist_position": self._waitlist_position(m) if m["status"] == "WAITLIST" else None,
            "cohort": self.cohort_view(m["cohort_id"]),
        }

    def _waitlist_position(self, m: sqlite3.Row) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM members WHERE cohort_id=? AND status='WAITLIST' "
            "AND joined_at <= ?", (m["cohort_id"], m["joined_at"])).fetchone()[0]

    # -- joining -------------------------------------------------------------
    def estimate(self, origin: str, dest: str, travel_date: str) -> dict:
        origin, dest = self._validate_route(origin, dest)
        d = _parse_date(travel_date)
        fare = market_fare(origin, dest, d)
        mult, fest = surge_multiplier(d)
        return {
            "market_fare": fare, "festival": fest, "surge": mult,
            "expected_group_fare_low": int(fare * 0.70),
            "expected_group_fare_high": int(fare * 0.85),
            "service_fee": SERVICE_FEE,
            "note": "Group fares are indicative estimates, not guaranteed.",
        }

    def _validate_route(self, origin: str, dest: str) -> tuple[str, str]:
        origin, dest = (origin or "").upper().strip(), (dest or "").upper().strip()
        if origin not in AIRPORTS or dest not in AIRPORTS:
            raise EngineError("Unknown airport code")
        if origin == dest:
            raise EngineError("Origin and destination must differ")
        return origin, dest

    def find_active_member(self, email: str, origin: str, dest: str,
                           travel_date: str) -> Optional[dict]:
        """Return an existing active membership for an exact route and date."""
        email = (email or "").strip().lower()
        origin, dest = self._validate_route(origin, dest)
        d = _parse_date(travel_date)
        row = self.db.execute(
            "SELECT m.id FROM members m JOIN cohorts c ON c.id=m.cohort_id "
            "WHERE m.email=? AND c.origin=? AND c.dest=? AND c.travel_date=? "
            "AND m.status IN ('JOINED','ACCEPTED','WAITLIST') "
            "AND c.status NOT IN ('CANCELLED','TICKETED')",
            (email, origin, dest, d.isoformat()),
        ).fetchone()
        return self.member_view(row["id"]) if row else None

    def join(self, name: str, email: str, origin: str, dest: str,
             travel_date: str, flex_days: int = 0, price_cap: Optional[int] = None,
             trigger_rfq: bool = False, idempotent: bool = False) -> dict:
        """Place a traveler in the best-fit cohort (or create one).

        Best-fit: among OPEN cohorts on the same route whose date is within the
        traveler's flex window, choose the *fullest* with space. Concentrating
        demand into the fullest pool maximises the chance a pool reaches the
        threshold. Full cohorts push the traveler to the waitlist of the best
        candidate.
        """
        name, email = (name or "").strip(), (email or "").strip().lower()
        if not name:
            raise EngineError("Name is required")
        if "@" not in email:
            raise EngineError("A valid email is required")
        origin, dest = self._validate_route(origin, dest)
        d = _parse_date(travel_date)
        if d <= self.now().date():
            raise EngineError("Travel date must be in the future")
        if not (0 <= int(flex_days) <= MAX_DATE_FLEX_DAYS):
            raise EngineError(f"flex_days must be between 0 and {MAX_DATE_FLEX_DAYS}")
        flex_days = int(flex_days)

        fare = market_fare(origin, dest, d)
        cap = int(price_cap) if price_cap else fare
        if cap < 500:
            raise EngineError("Price cap is too low")

        existing = self.find_active_member(email, origin, dest, d.isoformat())
        if existing:
            if idempotent:
                return existing
            raise EngineError("This email is already in a pool for that route and date")

        cohort = self._best_cohort(origin, dest, d, flex_days)
        waitlisted = False
        if cohort is not None:
            cohort_id = cohort["id"]
            spots = self.capacity - self._count(cohort_id, "JOINED", "ACCEPTED", "TICKETED")
            waitlisted = spots <= 0
        else:
            # No open pool. If a pool on this route has already been quoted and
            # still has bench space, take a backup seat there: members who drop
            # out of the quote are replaced from the bench (attrition handling).
            bench = self._bench_cohort(origin, dest, d, flex_days)
            if bench is not None:
                cohort_id, waitlisted = bench["id"], True
            else:
                cohort_id = self._create_cohort(origin, dest, d)
        hold = int(cap * HOLD_PERCENT)
        auth_id = None if waitlisted else self.payments.authorize(email, hold)

        member_id = "m_" + uuid.uuid4().hex[:10]
        self.db.execute(
            "INSERT INTO members(id, cohort_id, name, email, price_cap, flex_days, status,"
            " auth_id, hold_amount, joined_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (member_id, cohort_id, name, email, cap, flex_days,
             "WAITLIST" if waitlisted else "JOINED", auth_id,
             0 if waitlisted else hold, _iso(self.now())))
        self._log(cohort_id,
                  f"{name.split()[0]} joined the waitlist" if waitlisted
                  else f"{name.split()[0]} joined the pool (hold Rs {hold} placed)")
        self.db.commit()
        if trigger_rfq:
            self._maybe_trigger_rfq(cohort_id)
        return self.member_view(member_id)

    def _best_cohort(self, origin, dest, d: date, flex: int) -> Optional[sqlite3.Row]:
        lo, hi = d - timedelta(days=flex), d + timedelta(days=flex)
        rows = self.db.execute(
            "SELECT * FROM cohorts WHERE origin=? AND dest=? AND status='OPEN' "
            "AND travel_date BETWEEN ? AND ?", (origin, dest, lo.isoformat(), hi.isoformat())
        ).fetchall()
        if not rows:
            return None
        def key(r):
            committed = self._count(r["id"], "JOINED", "ACCEPTED", "TICKETED")
            exact = 0 if r["travel_date"] == d.isoformat() else 1
            return (-committed, exact, r["travel_date"])
        # prefer cohorts with free capacity; fall back to the fullest (waitlist)
        with_space = [r for r in rows
                      if self._count(r["id"], "JOINED", "ACCEPTED", "TICKETED") < r["capacity"]]
        pool = with_space or rows
        return sorted(pool, key=key)[0]

    def _bench_cohort(self, origin, dest, d: date, flex: int) -> Optional[sqlite3.Row]:
        """A QUOTED cohort in the date window whose backup bench has room.

        Bench size = capacity - passengers covered by the quote, so the spare
        capacity configured on the engine is the attrition buffer."""
        lo, hi = d - timedelta(days=flex), d + timedelta(days=flex)
        rows = self.db.execute(
            "SELECT * FROM cohorts WHERE origin=? AND dest=? AND status='QUOTED' "
            "AND travel_date BETWEEN ? AND ? ORDER BY travel_date",
            (origin, dest, lo.isoformat(), hi.isoformat())).fetchall()
        for r in rows:
            quoted_for = json.loads(r["quote_json"])["passengers"]
            if self._count(r["id"], "WAITLIST") < r["capacity"] - quoted_for:
                return r
        return None

    def _create_cohort(self, origin, dest, d: date) -> str:
        cid = "c_" + uuid.uuid4().hex[:10]
        self.db.execute(
            "INSERT INTO cohorts(id, origin, dest, travel_date, threshold, capacity, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (cid, origin, dest, d.isoformat(), self.threshold, self.capacity, "OPEN",
             _iso(self.now())))
        self._log(cid, f"Pool opened for {origin}->{dest} on {d.isoformat()}")
        return cid

    # -- RFQ -----------------------------------------------------------------
    def _maybe_trigger_rfq(self, cohort_id: str) -> None:
        c = self._cohort_row(cohort_id)
        if c["status"] != "OPEN":
            return
        if self._count(cohort_id, "JOINED") >= c["threshold"]:
            self.run_rfq(cohort_id)

    def run_rfq(self, cohort_id: str) -> dict:
        """Send the RFQ to airlines and store the best acceptable quote."""
        c = self._cohort_row(cohort_id)
        if c["status"] not in ("OPEN", "QUOTING"):
            raise EngineError(f"Cohort is {c['status']}; cannot request quotes")
        passengers = self._count(cohort_id, "JOINED")
        if passengers < c["threshold"]:
            raise EngineError("Threshold not reached yet")
        self.db.execute("UPDATE cohorts SET status='QUOTING' WHERE id=?", (cohort_id,))
        self._log(cohort_id, f"Threshold reached ({passengers}). RFQ sent to {len(AIRLINES)} airlines")

        quotes = self.airline.request_quotes(
            c["origin"], c["dest"], _parse_date(c["travel_date"]), passengers)
        quotes = [q for q in quotes if q["seats_available"] >= passengers]
        if not quotes:
            self._cancel(cohort_id, "No airline could offer group inventory")
            return self.cohort_view(cohort_id)

        best = quotes[0]
        market = market_fare(c["origin"], c["dest"], _parse_date(c["travel_date"]))
        if best["per_seat"] >= market:
            self._cancel(cohort_id, "Best group quote was not cheaper than the market fare")
            return self.cohort_view(cohort_id)

        best["all_quotes"] = [{"airline": q["airline"], "per_seat": q["per_seat"]} for q in quotes]
        best["service_fee"] = SERVICE_FEE
        best["passengers"] = passengers
        best["saving_per_seat"] = market - best["per_seat"]
        expires = self.now() + timedelta(hours=QUOTE_VALIDITY_HOURS)
        self.db.execute(
            "UPDATE cohorts SET status='QUOTED', quote_json=?, quote_expires=? WHERE id=?",
            (json.dumps(best), _iso(expires), cohort_id))
        self._log(cohort_id,
                  f"Best quote: {best['airline']} Rs {best['per_seat']}/seat "
                  f"(market Rs {market}). Members have {QUOTE_VALIDITY_HOURS}h to accept")
        self.db.commit()
        return self.cohort_view(cohort_id)

    # -- member decisions ----------------------------------------------------
    def accept(self, member_id: str) -> dict:
        m = self._member_row(member_id)
        c = self._cohort_row(m["cohort_id"])
        self._expire_if_needed(c["id"])
        c = self._cohort_row(m["cohort_id"])
        if c["status"] != "QUOTED":
            raise EngineError(f"Cohort is {c['status']}; nothing to accept")
        if m["status"] != "JOINED":
            raise EngineError(f"Member is {m['status']}; cannot accept")
        quote = json.loads(c["quote_json"])
        total = quote["per_seat"] + SERVICE_FEE
        if quote["per_seat"] > m["price_cap"]:
            raise EngineError(
                f"Quote (Rs {quote['per_seat']}) is above your price cap (Rs {m['price_cap']}). "
                "You can decline to release your hold.")
        self.db.execute("UPDATE members SET status='ACCEPTED' WHERE id=?", (member_id,))
        self._log(c["id"], f"{m['name'].split()[0]} accepted the quote (Rs {total} total)")
        self.db.commit()
        self._maybe_finalize(c["id"])
        return self.member_view(member_id)

    def decline(self, member_id: str) -> dict:
        m = self._member_row(member_id)
        c = self._cohort_row(m["cohort_id"])
        original_status = m["status"]
        if original_status not in ("JOINED", "ACCEPTED", "WAITLIST"):
            raise EngineError(f"Member is {original_status}; cannot leave")
        if original_status == "ACCEPTED" and c["status"] == "QUOTED":
            raise EngineError("You already accepted; contact support to cancel")
        self._release_member(m, "DECLINED")
        self._log(c["id"], f"{m['name'].split()[0]} left the pool")
        self.db.commit()
        # Attrition handling: promote the waitlist into the freed seat.
        if original_status in ("JOINED", "ACCEPTED"):
            self._promote_waitlist(c["id"])
        if c["status"] == "QUOTED":
            self._maybe_finalize(c["id"])
        return self.member_view(member_id)

    def _release_member(self, m: sqlite3.Row, new_status: str) -> None:
        if m["auth_id"]:
            self.payments.release(m["auth_id"])
        self.db.execute("UPDATE members SET status=?, hold_amount=0 WHERE id=?",
                        (new_status, m["id"]))

    def _promote_waitlist(self, cohort_id: str) -> None:
        c = self._cohort_row(cohort_id)
        if c["status"] not in ("OPEN", "QUOTED"):
            return
        # In a quoted pool we may only refill up to the number of seats the
        # airline quoted for; in an open pool, up to capacity.
        limit = (json.loads(c["quote_json"])["passengers"]
                 if c["status"] == "QUOTED" else c["capacity"])
        while self._count(cohort_id, "JOINED", "ACCEPTED", "TICKETED") < limit:
            nxt = self.db.execute(
                "SELECT * FROM members WHERE cohort_id=? AND status='WAITLIST' "
                "ORDER BY joined_at LIMIT 1", (cohort_id,)).fetchone()
            if not nxt:
                break
            hold = int(nxt["price_cap"] * HOLD_PERCENT)
            auth_id = self.payments.authorize(nxt["email"], hold)
            self.db.execute(
                "UPDATE members SET status='JOINED', auth_id=?, hold_amount=? WHERE id=?",
                (auth_id, hold, nxt["id"]))
            self._log(cohort_id, f"{nxt['name'].split()[0]} moved from waitlist into the pool")
        self.db.commit()

    # -- finalisation --------------------------------------------------------
    def _maybe_finalize(self, cohort_id: str) -> None:
        c = self._cohort_row(cohort_id)
        if c["status"] != "QUOTED":
            return
        joined = self._count(cohort_id, "JOINED")
        accepted = self._count(cohort_id, "ACCEPTED")
        if joined == 0:
            if accepted >= c["threshold"]:
                self._ticket(cohort_id)
            else:
                self._cancel(cohort_id,
                             f"Only {accepted} of {c['threshold']} required members accepted")
            return
        # early success: even if every pending member declined we could not fail
        # (we do not short-circuit; wait for everyone or expiry)

    def _expire_if_needed(self, cohort_id: str) -> None:
        c = self._cohort_row(cohort_id)
        if c["status"] != "QUOTED" or not c["quote_expires"]:
            return
        if self.now() < _parse_iso(c["quote_expires"]):
            return
        pending = self.db.execute(
            "SELECT * FROM members WHERE cohort_id=? AND status='JOINED'", (cohort_id,)).fetchall()
        for m in pending:
            self._release_member(m, "EXPIRED")
        self._log(cohort_id, f"Quote expired; {len(pending)} members who did not respond were released")
        self.db.commit()
        self._maybe_finalize(cohort_id)

    def tick(self) -> dict:
        """Periodic job: expire stale quotes. Returns a small summary."""
        rows = self.db.execute("SELECT id FROM cohorts WHERE status='QUOTED'").fetchall()
        for r in rows:
            self._expire_if_needed(r["id"])
        return {"checked": len(rows)}

    def _ticket(self, cohort_id: str) -> None:
        c = self._cohort_row(cohort_id)
        quote = json.loads(c["quote_json"])
        members = self.db.execute(
            "SELECT * FROM members WHERE cohort_id=? AND status='ACCEPTED'", (cohort_id,)).fetchall()
        total = quote["per_seat"] + SERVICE_FEE
        for m in members:
            self.payments.capture(m["auth_id"], total)
            pnr = ("FH" + hashlib.sha1(f"{m['id']}{cohort_id}".encode()).hexdigest()[:5]).upper()
            self.db.execute("UPDATE members SET status='TICKETED', pnr=? WHERE id=?", (pnr, m["id"]))
        self.db.execute("UPDATE cohorts SET status='TICKETED' WHERE id=?", (cohort_id,))
        # waitlisted members never held money; mark them so they can re-pool
        self.db.execute("UPDATE members SET status='REFUNDED' WHERE cohort_id=? AND status='WAITLIST'",
                        (cohort_id,))
        self._log(cohort_id, f"Ticketed {len(members)} passengers on {quote['airline']}")
        self.db.commit()

    def _cancel(self, cohort_id: str, reason: str) -> None:
        members = self.db.execute(
            "SELECT * FROM members WHERE cohort_id=? AND status IN ('JOINED','ACCEPTED','WAITLIST')",
            (cohort_id,)).fetchall()
        for m in members:
            self._release_member(m, "REFUNDED")
        self.db.execute("UPDATE cohorts SET status='CANCELLED' WHERE id=?", (cohort_id,))
        self._log(cohort_id, f"Pool cancelled: {reason}. All holds released")
        self.db.commit()

    # -- savings report ------------------------------------------------------
    def savings_summary(self) -> dict:
        rows = self.db.execute("SELECT id, quote_json FROM cohorts WHERE status='TICKETED'").fetchall()
        passengers = saved = fees = 0
        for r in rows:
            q = json.loads(r["quote_json"])
            n = self._count(r["id"], "TICKETED")
            passengers += n
            saved += n * q["saving_per_seat"]
            fees += n * SERVICE_FEE
        return {"ticketed_cohorts": len(rows), "passengers": passengers,
                "total_saved": saved, "platform_revenue": fees}

    # -- demo helpers --------------------------------------------------------
    def seed_demo(self, rng_seed: int = 7) -> None:
        """Create a few realistic pools so the UI is not empty on first run."""
        rng = random.Random(rng_seed)
        first = ["Aarav", "Diya", "Rohan", "Isha", "Kabir", "Meera", "Vivaan", "Anaya",
                 "Arjun", "Saanvi", "Aditya", "Riya", "Karan", "Neha", "Ishaan", "Tara",
                 "Dev", "Pooja", "Manav", "Sneha", "Nikhil", "Kavya", "Yash", "Ananya",
                 "Harsh", "Lakshmi", "Varun", "Ira", "Sahil", "Nandini", "Pranav", "Zoya",
                 "Rahul", "Mitali", "Tejas", "Bhavna", "Om", "Sara", "Jai", "Ritika"]
        start = next_festival_start(self.now().date())
        plans = [("BLR", "DEL", start + timedelta(days=1), 37),
                 ("BLR", "HYD", start + timedelta(days=2), 18),
                 ("BLR", "CCU", start + timedelta(days=3), 8),
                 ("MAA", "BLR", start + timedelta(days=1), 29)]
        for o, d, dt, n in plans:
            for i in range(n):
                nm = first[(i + rng.randint(0, 5)) % len(first)]
                try:
                    self.join(f"{nm} {chr(65 + i % 26)}.", f"{nm.lower()}{i}.{o.lower()}{d.lower()}@demo.in",
                              o, d, dt.isoformat(), flex_days=rng.choice([0, 0, 1, 2]))
                except EngineError:
                    pass
