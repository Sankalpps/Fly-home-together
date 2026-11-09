"""Background scheduler — periodic engine tick and notification jobs."""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler


def setup_scheduler(app) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")

    def engine_tick():
        """Expire stale quotes every 5 minutes."""
        result = app.state.engine.tick()
        if result.get("checked", 0):
            print(f"[Tick] {result['checked']} quoted cohort(s) checked")

    scheduler.add_job(engine_tick, "interval", minutes=5, id="engine_tick",
                      replace_existing=True)
    scheduler.start()
    return scheduler
