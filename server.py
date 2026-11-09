"""HTTP server for Fly Home Together (stdlib only).

Run:  python3 server.py [--port 8000] [--db fht.db] [--no-seed]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from engine import (AIRPORTS, CohortEngine, EngineError, SimulatedAirlineGateway,
                    next_festival_start)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
LOCK = threading.Lock()  # SQLite + simple engine => serialise writes


def make_handler(engine: CohortEngine):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FlyHomeTogether/1.0"

        # -- plumbing --------------------------------------------------------
        def log_message(self, fmt, *args):  # quieter logs
            pass

        def _send(self, code: int, payload, ctype="application/json"):
            body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 100_000:
                raise EngineError("Request too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                raise EngineError("Body must be valid JSON")
            if not isinstance(data, dict):
                raise EngineError("Body must be a JSON object")
            return data

        def _static(self, path: str):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            full = os.path.realpath(os.path.join(STATIC_DIR, rel))
            if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
                return self._send(404, {"error": "Not found"})
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype)

        # -- routing ---------------------------------------------------------
        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                with LOCK:
                    if url.path == "/api/airports":
                        return self._send(200, AIRPORTS)
                    if url.path == "/api/config":
                        today = engine.now().date()
                        return self._send(200, {
                            "default_date": (next_festival_start(today) + timedelta(days=1)).isoformat(),
                            "min_date": (today + timedelta(days=1)).isoformat()})
                    if url.path == "/api/cohorts":
                        engine.tick()
                        cohorts = engine.list_cohorts(
                            q.get("origin"), q.get("dest"), q.get("status"))
                        public_cohorts = [c for c in cohorts
                                          if c["status"] in ("QUOTED", "TICKETED")]
                        return self._send(200, public_cohorts)
                    if url.path.startswith("/api/cohorts/"):
                        cid = url.path.split("/")[3]
                        engine.tick()
                        return self._send(200, engine.cohort_view(cid, include_members=True))
                    if url.path.startswith("/api/members/"):
                        mid = url.path.split("/")[3]
                        engine.tick()
                        return self._send(200, engine.member_view(mid))
                    if url.path == "/api/estimate":
                        return self._send(200, engine.estimate(
                            q.get("origin", ""), q.get("dest", ""), q.get("date", "")))
                    if url.path == "/api/summary":
                        return self._send(200, engine.savings_summary())
                if url.path.startswith("/api/"):
                    return self._send(404, {"error": "Unknown endpoint"})
                return self._static(url.path)
            except EngineError as e:
                self._send(400, {"error": str(e)})
            except Exception as e:  # pragma: no cover
                self._send(500, {"error": f"Internal error: {e}"})

        def do_POST(self):
            url = urlparse(self.path)
            parts = url.path.strip("/").split("/")
            try:
                body = self._json_body()
                with LOCK:
                    if url.path == "/api/join":
                        return self._send(201, engine.join(
                            body.get("name"), body.get("email"), body.get("origin"),
                            body.get("dest"), body.get("date"),
                            int(body.get("flex_days") or 0),
                            int(body["price_cap"]) if body.get("price_cap") else None))
                    if len(parts) == 4 and parts[:2] == ["api", "members"]:
                        mid, action = parts[2], parts[3]
                        if action == "accept":
                            return self._send(200, engine.accept(mid))
                        if action == "decline":
                            return self._send(200, engine.decline(mid))
                    # ---- demo controls (would be admin-only in production) ----
                    if len(parts) == 4 and parts[:2] == ["api", "demo"]:
                        cid, action = parts[2], parts[3]
                        return self._send(200, self._demo(cid, action, body))
                    if url.path == "/api/demo/advance":
                        engine.advance_clock(float(body.get("hours", 25)))
                        engine.tick()
                        return self._send(200, {"ok": True})
                return self._send(404, {"error": "Unknown endpoint"})
            except EngineError as e:
                self._send(400, {"error": str(e)})
            except (ValueError, TypeError) as e:
                self._send(400, {"error": f"Bad input: {e}"})
            except Exception as e:  # pragma: no cover
                self._send(500, {"error": f"Internal error: {e}"})

        def _demo(self, cid: str, action: str, body: dict):
            """Demo-only helpers so the whole flow can be shown in one sitting."""
            c = engine.cohort_view(cid)
            if action == "fill":
                need = c["capacity"] - c["committed"]
                target = max(0, min(need, c["threshold"] - c["committed"]))
                for i in range(target):
                    engine.join(f"Demo Traveler {i + 1}", f"demo{i}.{cid}@example.com",
                                c["origin"], c["dest"], c["travel_date"], 0)
                return engine.cohort_view(cid)
            if action == "accept_all":
                rows = engine.db.execute(
                    "SELECT id FROM members WHERE cohort_id=? AND status='JOINED'", (cid,)).fetchall()
                for r in rows:
                    try:
                        engine.accept(r["id"])
                    except EngineError:
                        pass
                return engine.cohort_view(cid)
            raise EngineError("Unknown demo action")

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default=":memory:", help="SQLite path (default: in-memory)")
    ap.add_argument("--no-seed", action="store_true", help="do not create demo pools")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for simulated airline quotes")
    args = ap.parse_args()

    engine = CohortEngine(args.db, airline=SimulatedAirlineGateway(seed=args.seed))
    fresh = engine.db.execute("SELECT COUNT(*) FROM cohorts").fetchone()[0] == 0
    if fresh and not args.no_seed:
        engine.seed_demo()

    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(engine))
    print(f"Fly Home Together running on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
