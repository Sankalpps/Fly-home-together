"""Integration tests: full HTTP round-trips via FastAPI TestClient."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine import CohortEngine, SimulatedAirlineGateway, SimulatedPaymentGateway
from backend.db import init_db
from backend.auth.service import register, login, update_role
from backend.gateway.flights import SimulatedFlightGateway
from backend.auth.router import router as auth_router
from backend.cohorts.router import router as cohorts_router
from backend.desk.router import router as desk_router
from backend.admin.router import router as admin_router


def make_app(tmp_path, threshold=3, capacity=5):
    db_path = str(tmp_path / "test.db")
    engine = CohortEngine(db_path, airline=SimulatedAirlineGateway(seed=5),
                          payments=SimulatedPaymentGateway(),
                          threshold=threshold, capacity=capacity)
    init_db(db_path)

    app = FastAPI()
    app.state.engine = engine
    app.state.flight_gateway = SimulatedFlightGateway()
    app.include_router(auth_router)
    app.include_router(cohorts_router)
    app.include_router(desk_router)
    app.include_router(admin_router)
    return TestClient(app), engine


# ---------------------------------------------------------------------------
# Auth endpoint tests
# ---------------------------------------------------------------------------

class TestAuthEndpoints:
    def test_register_traveler(self, tmp_path):
        client, _ = make_app(tmp_path)
        resp = client.post("/auth/register", json={
            "email": "user1@gmail.com", "password": "mypassword", "name": "User One",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["user"]["role"] == "TRAVELER"
        assert "access_token" in data

    def test_desk_role_auto_assigned(self, tmp_path):
        client, _ = make_app(tmp_path)
        resp = client.post("/auth/register", json={
            "email": "officer@atc.gov", "password": "securepass", "name": "Officer",
        })
        assert resp.status_code == 201
        assert resp.json()["user"]["role"] == "DESK_STAFF"

    def test_login_single_page(self, tmp_path):
        """Single /auth/login for all roles — no separate admin login."""
        client, _ = make_app(tmp_path)
        client.post("/auth/register", json={
            "email": "me@x.com", "password": "pass1234", "name": "Me",
        })
        resp = client.post("/auth/login", json={
            "email": "me@x.com", "password": "pass1234",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_me_returns_role(self, tmp_path):
        client, _ = make_app(tmp_path)
        r = client.post("/auth/register", json={
            "email": "me2@x.com", "password": "pass1234", "name": "Me2",
        })
        token = r.json()["access_token"]
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["role"] == "TRAVELER"

    def test_wrong_password_401(self, tmp_path):
        client, _ = make_app(tmp_path)
        client.post("/auth/register", json={
            "email": "z@x.com", "password": "rightpass", "name": "Z",
        })
        resp = client.post("/auth/login", json={
            "email": "z@x.com", "password": "wrongpass",
        })
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Admin invisible to non-admins
# ---------------------------------------------------------------------------

class TestAdminAccess:
    def test_traveler_cannot_see_admin_stats(self, tmp_path):
        client, _ = make_app(tmp_path)
        r = client.post("/auth/register", json={
            "email": "normal@gmail.com", "password": "pass1234", "name": "Normal",
        })
        token = r.json()["access_token"]
        resp = client.get("/admin/stats", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_desk_staff_cannot_see_admin_stats(self, tmp_path):
        client, _ = make_app(tmp_path)
        r = client.post("/auth/register", json={
            "email": "desk@atc.gov", "password": "pass1234", "name": "Desk",
        })
        token = r.json()["access_token"]
        resp = client.get("/admin/stats", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_admin_can_see_stats(self, tmp_path):
        client, _ = make_app(tmp_path)
        r = client.post("/auth/register", json={
            "email": "admin@platform.com", "password": "pass1234", "name": "Admin",
            # explicit role in registration (normally done via /admin/users/{id}/role)
        })
        # Promote to admin using service directly
        user_id = r.json()["user"]["id"]
        from backend.auth.service import update_role
        update_role(user_id, "PLATFORM_ADMIN")
        # Re-login to get token with new role
        login_resp = client.post("/auth/login", json={
            "email": "admin@platform.com", "password": "pass1234",
        })
        token = login_resp.json()["access_token"]
        resp = client.get("/admin/stats", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "total_users" in resp.json()


# ---------------------------------------------------------------------------
# Pool / cohort endpoints
# ---------------------------------------------------------------------------

class TestPoolEndpoints:
    def test_airports_includes_all_india(self, tmp_path):
        client, _ = make_app(tmp_path)
        resp = client.get("/api/airports")
        assert resp.status_code == 200
        airports = resp.json()
        for metro in ["DEL", "BOM", "BLR", "MAA", "HYD", "CCU"]:
            assert metro in airports
        assert len(airports) >= 50

    def test_estimate(self, tmp_path):
        client, _ = make_app(tmp_path)
        resp = client.get("/api/estimate?origin=BLR&dest=DEL&date=2026-12-01")
        assert resp.status_code == 200
        assert "market_fare" in resp.json()

    def test_join_pool(self, tmp_path):
        client, engine = make_app(tmp_path)
        d = (engine.now().date() + timedelta(days=15)).isoformat()
        resp = client.post("/api/join", json={
            "name": "Test User", "email": "test@x.com",
            "origin": "BLR", "dest": "DEL", "date": d,
        })
        assert resp.status_code == 201
        assert resp.json()["status"] in ("JOINED", "WAITLIST")

    def test_full_pool_lifecycle(self, tmp_path):
        """Join 3 → auto RFQ → accept all → TICKETED."""
        client, engine = make_app(tmp_path)
        d = (engine.now().date() + timedelta(days=15)).isoformat()
        members = []
        for i in range(3):
            r = client.post("/api/join", json={
                "name": f"User {i}", "email": f"u{i}@flow.com",
                "origin": "BLR", "dest": "HYD", "date": d,
            })
            assert r.status_code == 201
            members.append(r.json())

        cohort_id = members[0]["cohort"]["id"]
        cohort = client.get(f"/api/cohorts/{cohort_id}").json()
        assert cohort["status"] == "QUOTED"

        for m in members:
            resp = client.post(f"/api/members/{m['id']}/accept")
            assert resp.status_code == 200

        final = client.get(f"/api/cohorts/{cohort_id}").json()
        assert final["status"] == "TICKETED"


# ---------------------------------------------------------------------------
# Desk portal
# ---------------------------------------------------------------------------

class TestDeskPortal:
    def test_desk_staff_can_access_rfqs(self, tmp_path):
        client, _ = make_app(tmp_path)
        r = client.post("/auth/register", json={
            "email": "agent@atc.gov", "password": "pass1234", "name": "Agent",
        })
        token = r.json()["access_token"]
        resp = client.get("/desk/rfqs", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_traveler_blocked_from_desk(self, tmp_path):
        client, _ = make_app(tmp_path)
        r = client.post("/auth/register", json={
            "email": "traveler@gmail.com", "password": "pass1234", "name": "Traveler",
        })
        token = r.json()["access_token"]
        resp = client.get("/desk/rfqs", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Flight data
# ---------------------------------------------------------------------------

class TestFlightData:
    def test_departures_endpoint(self, tmp_path):
        client, _ = make_app(tmp_path)
        resp = client.get("/api/flights/departures/BLR?date=2026-11-15")
        assert resp.status_code == 200
        flights = resp.json()
        assert isinstance(flights, list)
        assert len(flights) > 0

    def test_all_departures_are_domestic(self, tmp_path):
        from backend.gateway.flights import INDIA_AIRPORTS
        client, _ = make_app(tmp_path)
        resp = client.get("/api/flights/departures/DEL?date=2026-11-15")
        for f in resp.json():
            assert f["destination"] in INDIA_AIRPORTS

    def test_flight_status_endpoint(self, tmp_path):
        client, _ = make_app(tmp_path)
        resp = client.get("/api/flights/status/6E-204?date=2026-11-15")
        assert resp.status_code == 200
        assert "status" in resp.json()

    def test_routes_endpoint(self, tmp_path):
        client, _ = make_app(tmp_path)
        resp = client.get("/api/flights/routes/BLR")
        assert resp.status_code == 200
        data = resp.json()
        assert "destinations" in data
        assert len(data["destinations"]) >= 50
