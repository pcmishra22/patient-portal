"""Demo data. Seeded THROUGH the service layer, so seed history looks exactly like real history."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from . import services
from . import database
from .models import User

USERS = [
    ("p1", "Alex Rivera", "alex.rivera@example.com", "patient"),
    ("p2", "Sam Lee", "sam.lee@example.com", "patient"),
    ("dr1", "Dr. Priya Shah", "priya.shah@example.com", "provider"),
    ("dr2", "Dr. Marcus Chen", "marcus.chen@example.com", "provider"),
]


def seed_users() -> None:
    with database.session() as s, s.begin():
        s.add_all(User(id=i, name=n, email=e, role=r) for i, n, e, r in USERS)


def _user(user_id: str) -> User:
    with database.session() as s:
        return services.get_user(s, user_id)


def seed_if_empty() -> None:
    with database.session() as s:
        if s.scalar(select(func.count(User.id))):
            return
    seed_users()
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    def at(days: int, hour: int, minute: int = 0) -> datetime:
        return (base + timedelta(days=days)).replace(hour=hour, minute=minute)

    # (patient, provider, start, minutes, type, reason, confirm?)
    rows = [
        ("p1", "dr1", at(1, 15), 30, "Check-up", "Annual physical", True),
        ("p1", "dr1", at(2, 18), 30, "Follow-up", "Blood pressure review", False),
        ("p2", "dr1", at(1, 15, 15), 30, "Consultation", "Recurring headaches", False),  # overlaps #1 on purpose
        ("p2", "dr2", at(3, 14), 45, "Check-up", "Vaccination", True),
    ]
    for patient, provider, start, mins, typ, reason, confirm in rows:
        with database.session() as s:
            appt = services.create_appointment(s, _user(patient), provider, start, mins, typ, reason)
            appt_id, version = appt.id, appt.version
        if confirm:
            with database.session() as s:
                services.apply_change(s, _user(provider), appt_id, version, "confirm")
