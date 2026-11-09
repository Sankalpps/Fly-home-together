"""Tests for notification dispatch — all free channels."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from engine import CohortEngine
from backend.db import init_db
from backend.notifications.service import dispatch, get_notification_log


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    eng = CohortEngine(db_path)
    init_db(db_path)
    yield db_path
    eng.close()


class TestNotifications:
    def test_simulated_email_stored_in_db(self):
        dispatch(
            notification_type="JOIN_CONFIRM",
            recipient_email="user@x.com",
            payload={"cohort_id": "c_test", "status": "JOINED"},
            cohort_id="c_test",
        )
        log = get_notification_log(10)
        email_entries = [n for n in log if n["channel"] == "EMAIL"]
        assert len(email_entries) == 1
        assert email_entries[0]["type"] == "JOIN_CONFIRM"
        assert email_entries[0]["status"] == "SIMULATED"

    def test_no_telegram_sent_without_chat_id(self):
        dispatch(
            notification_type="TICKET_ISSUED",
            recipient_email="user@x.com",
            payload={"pnr": "FH12345"},
            telegram_chat_id=None,
        )
        log = get_notification_log(10)
        tg = [n for n in log if n["channel"] == "TELEGRAM"]
        assert len(tg) == 0

    def test_telegram_entry_created_when_chat_id_present(self):
        dispatch(
            notification_type="JOIN_CONFIRM",
            recipient_email="user@x.com",
            payload={"cohort_id": "c_xyz"},
            telegram_chat_id="999888777",
        )
        log = get_notification_log(10)
        channels = {n["channel"] for n in log}
        assert "EMAIL" in channels
        assert "TELEGRAM" in channels

    def test_simulated_status_when_no_credentials(self):
        dispatch(
            notification_type="POOL_CANCELLED",
            recipient_email="user@x.com",
            payload={"reason": "test"},
        )
        log = get_notification_log(5)
        assert all(n["status"] == "SIMULATED" for n in log)

    def test_multiple_notification_types_stored(self):
        for ntype in ["JOIN_CONFIRM", "NEAR_THRESHOLD", "QUOTE_READY"]:
            dispatch(
                notification_type=ntype,
                recipient_email="user@x.com",
                payload={"type": ntype},
            )
        log = get_notification_log(20)
        types = {n["type"] for n in log}
        assert types >= {"JOIN_CONFIRM", "NEAR_THRESHOLD", "QUOTE_READY"}

    def test_user_id_stored_in_log(self):
        dispatch(
            notification_type="JOIN_CONFIRM",
            recipient_email="user@x.com",
            payload={},
            user_id="u_testuser",
        )
        log = get_notification_log(10, user_id="u_testuser")
        assert len(log) >= 1
        assert log[0]["user_id"] == "u_testuser"
