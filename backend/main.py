"""FastAPI application factory for Fly Home Together v2."""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Make engine.py importable from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import CohortEngine, SimulatedAirlineGateway, SimulatedPaymentGateway

from backend.config import get_settings
from backend.db import init_db
from backend.auth.service import seed_default_users
from backend.gateway.flights import build_flight_gateway
from backend.workers.tick import setup_scheduler

from backend.auth.router import router as auth_router
from backend.cohorts.router import router as cohorts_router
from backend.desk.router import router as desk_router
from backend.admin.router import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db_path = settings.DB_PATH

    # Initialize the platform-extended DB (users, notifications, etc.)
    init_db(db_path)
    seed_default_users()

    # Initialize the core engine (uses the same SQLite file)
    engine = CohortEngine(
        db_path=db_path,
        airline=SimulatedAirlineGateway(),
        payments=SimulatedPaymentGateway(),
    )
    if engine.db.execute("SELECT COUNT(*) FROM cohorts").fetchone()[0] == 0:
        engine.seed_demo()

    app.state.engine = engine
    app.state.flight_gateway = build_flight_gateway(settings)

    scheduler = setup_scheduler(app)

    print("[FHT] Fly Home Together v2 -- platform ready")
    print(f"   DB: {db_path}")
    print(f"   Flight data: {'AeroDataBox (real)' if settings.flight_api_enabled else 'Simulated'}")
    print(f"   Email: {'Gmail SMTP' if settings.email_enabled else 'Simulated (console)'}")
    print(f"   Telegram: {'Bot active' if settings.telegram_enabled else 'Simulated (console)'}")

    yield

    scheduler.shutdown()
    engine.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Fly Home Together",
        description="Group flight booking platform — Indian domestic travel",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],        # tighten to your domain in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(cohorts_router)
    app.include_router(desk_router)
    app.include_router(admin_router)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "2.0.0"}

    # Mount static files at root for web interface
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
