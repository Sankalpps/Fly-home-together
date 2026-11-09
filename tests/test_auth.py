"""Tests for auth: register, login, JWT, role auto-detection.
Single login page — role is detected from email domain.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from engine import CohortEngine
from backend.db import init_db
from backend.auth.service import (
    AuthError, create_access_token, decode_access_token,
    get_user_by_id, link_telegram, login, register,
    rotate_refresh_token, update_role,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    eng = CohortEngine(db_path)          # creates cohort schema
    init_db(db_path)                      # adds platform schema
    yield db_path
    eng.close()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegister:
    def test_traveler_role_for_gmail(self):
        u = register("alice@gmail.com", "secret123", "Alice")
        assert u.role == "TRAVELER"

    def test_desk_staff_auto_assigned_for_atc_domain(self):
        u = register("ragu@atc.gov", "password1", "Ragu S")
        assert u.role == "DESK_STAFF"

    def test_desk_staff_auto_assigned_for_aai_domain(self):
        u = register("officer@aai.aero", "password1", "Officer")
        assert u.role == "DESK_STAFF"

    def test_duplicate_email_rejected(self):
        register("bob@x.com", "pass1234", "Bob")
        with pytest.raises(AuthError, match="already registered"):
            register("bob@x.com", "other123", "Bob2")

    def test_bad_email_rejected(self):
        with pytest.raises(AuthError):
            register("not-an-email", "pass1234", "X")

    def test_short_password_rejected(self):
        with pytest.raises(AuthError, match="6 characters"):
            register("x@y.com", "abc", "X")

    def test_empty_name_rejected(self):
        with pytest.raises(AuthError, match="Name"):
            register("x@y.com", "pass1234", "")

    def test_explicit_role_honoured(self):
        u = register("admin@custom.org", "pass1234", "Admin",
                     role="PLATFORM_ADMIN")
        assert u.role == "PLATFORM_ADMIN"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_returns_user_and_tokens(self):
        register("carol@x.com", "mypassword", "Carol")
        user, access, refresh = login("carol@x.com", "mypassword")
        assert user.email == "carol@x.com"
        assert access.startswith("ey")   # JWT
        assert len(refresh) > 20

    def test_wrong_password_fails(self):
        register("dave@x.com", "rightpass", "Dave")
        with pytest.raises(AuthError, match="Invalid"):
            login("dave@x.com", "wrongpass")

    def test_unknown_email_fails(self):
        with pytest.raises(AuthError):
            login("nobody@x.com", "pass123")

    def test_case_insensitive_email(self):
        register("Eve@X.COM", "pass1234", "Eve")
        user, _, _ = login("eve@x.com", "pass1234")
        assert user.email == "eve@x.com"


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

class TestJWT:
    def test_token_contains_role(self):
        register("f@x.com", "pass1234", "F")
        _, access, _ = login("f@x.com", "pass1234")
        payload = decode_access_token(access)
        assert payload["role"] == "TRAVELER"
        assert payload["sub"]

    def test_desk_staff_role_in_token(self):
        register("g@atc.gov", "pass1234", "G")
        _, access, _ = login("g@atc.gov", "pass1234")
        assert decode_access_token(access)["role"] == "DESK_STAFF"

    def test_tampered_token_rejected(self):
        register("h@x.com", "pass1234", "H")
        _, access, _ = login("h@x.com", "pass1234")
        with pytest.raises(AuthError):
            decode_access_token(access[:-5] + "XXXXX")

    def test_refresh_token_rotation(self):
        register("i@x.com", "pass1234", "I")
        user, _, refresh_raw = login("i@x.com", "pass1234")
        new_user, new_raw = rotate_refresh_token(refresh_raw)
        assert new_user.id == user.id
        assert new_raw != refresh_raw
        # Old token should no longer work
        with pytest.raises(AuthError):
            rotate_refresh_token(refresh_raw)


# ---------------------------------------------------------------------------
# Role management
# ---------------------------------------------------------------------------

class TestRoleUpdate:
    def test_promote_traveler_to_admin(self):
        u = register("j@x.com", "pass1234", "J")
        updated = update_role(u.id, "PLATFORM_ADMIN")
        assert updated.role == "PLATFORM_ADMIN"

    def test_assign_department_and_airline(self):
        u = register("k@atc.gov", "pass1234", "K")
        updated = update_role(u.id, "DESK_STAFF", department="PRICING",
                              airline="IndiGo")
        assert updated.department == "PRICING"
        assert updated.airline == "IndiGo"

    def test_unknown_role_rejected(self):
        u = register("l@x.com", "pass1234", "L")
        with pytest.raises(AuthError, match="Unknown role"):
            update_role(u.id, "SUPER_WIZARD")


# ---------------------------------------------------------------------------
# Telegram linking
# ---------------------------------------------------------------------------

class TestTelegramLink:
    def test_link_chat_id(self):
        u = register("m@x.com", "pass1234", "M")
        assert u.telegram_chat_id is None
        updated = link_telegram(u.id, "123456789")
        assert updated.telegram_chat_id == "123456789"
