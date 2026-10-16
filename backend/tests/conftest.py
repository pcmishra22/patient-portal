import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# Tests need a real PostgreSQL (the exclusion constraint and row locks are Postgres features).
# `make db` starts one with Docker and creates both the `portal` and `portal_test` databases.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://portal:portal@localhost:5432/portal_test")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # set BEFORE the app modules read it

from app import database, seed  # noqa: E402
from app.main import app  # noqa: E402

TABLES = "notifications, appointment_events, appointments, users"


@pytest.fixture(scope="session", autouse=True)
def schema():
    database.init_db()


@pytest.fixture()
def empty_db():
    """Clean database, no rows at all."""
    with database.engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))  # TRUNCATE skips the row-level immutability trigger
    return None


@pytest.fixture()
def db(empty_db):
    """Clean database with the four demo users."""
    seed.seed_users()


@pytest.fixture()
def client(db):
    return TestClient(app)


def slot(day: int, hour: int, minute: int = 0) -> str:
    """A UTC timestamp `day` days from now (always in the future)."""
    t = (datetime.now(timezone.utc) + timedelta(days=day)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def hdr(user_id: str) -> dict:
    return {"X-User-Id": user_id}


def request_appt(client, patient="p1", provider="dr1", start=None, minutes=30):
    r = client.post("/api/appointments", headers=hdr(patient), json={
        "providerId": provider, "startTime": start or slot(2, 10), "durationMin": minutes,
        "appointmentType": "Check-up", "reason": "Test"})
    assert r.status_code == 201, r.text
    return r.json()


def confirm(client, appt, provider="dr1"):
    return client.post(f"/api/appointments/{appt['id']}/confirm", headers=hdr(provider), json={"version": appt["version"]})
