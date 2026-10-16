"""SQLAlchemy 2.0 models for PostgreSQL. The schema itself enforces the important invariants:
  * no_provider_overlap : EXCLUDE constraint -> two overlapping CONFIRMED appointments for one provider cannot exist
  * appointment_events  : immutable (trigger in database.py)
  * version             : optimistic-concurrency token (version_id_col)"""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MAX_DELIVERY_ATTEMPTS = 5  # after this many failed sends a notification is abandoned (and flagged gave_up)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}  # timestamptz everywhere


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('patient','provider')", name="ck_user_role"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    email: Mapped[str]
    role: Mapped[str]


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("status IN ('pending','confirmed','cancelled')", name="ck_appt_status"),
        CheckConstraint("end_time > start_time", name="ck_appt_interval"),
        # Problem 4, enforced by the database itself: for one provider, the half-open time ranges [start, end) of
        # CONFIRMED appointments may never overlap. Back-to-back (15:00 end / 15:00 start) is allowed because the
        # upper bound is exclusive. Postgres serializes conflicting writers: the second transaction waits for the
        # first to finish, then fails with SQLSTATE 23P01 (exclusion_violation).
        ExcludeConstraint(
            ("provider_id", "="),
            (text("tstzrange(start_time, end_time, '[)')"), "&&"),
            name="no_provider_overlap", using="gist", where=text("status = 'confirmed'")),
        Index("idx_appt_patient", "patient_id", "start_time"),
        Index("idx_appt_provider", "provider_id", "start_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    provider_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    start_time: Mapped[datetime]
    end_time: Mapped[datetime]
    appointment_type: Mapped[str]
    reason: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    # Problem 1: optimistic concurrency token. SQLAlchemy emits `UPDATE ... WHERE id=? AND version=?`, increments it
    # on every flush, and raises StaleDataError if no row matched.
    version: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    patient: Mapped[User] = relationship(foreign_keys=[patient_id], lazy="selectin")
    provider: Mapped[User] = relationship(foreign_keys=[provider_id], lazy="selectin")

    __mapper_args__ = {"version_id_col": version}

    @property
    def patient_name(self) -> str:
        return self.patient.name

    @property
    def patient_email(self) -> str:
        return self.patient.email

    @property
    def provider_name(self) -> str:
        return self.provider.name


class AppointmentEvent(Base):
    """Problem 2: append-only audit log, written in the SAME transaction as every change. Explicit from_/to_ columns
    make 'what was it before?' a plain query; JSONB snapshots keep the full old/new values."""
    __tablename__ = "appointment_events"
    __table_args__ = (
        CheckConstraint("action IN ('created','confirmed','rescheduled','cancelled')", name="ck_event_action"),
        Index("idx_events_appt", "appointment_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    actor_role: Mapped[str]
    action: Mapped[str]
    from_status: Mapped[str | None]
    to_status: Mapped[str]
    from_start: Mapped[datetime | None]
    to_start: Mapped[datetime]
    from_end: Mapped[datetime | None]
    to_end: Mapped[datetime]
    old_values: Mapped[dict | None] = mapped_column(JSONB)
    new_values: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    actor: Mapped[User] = relationship(lazy="selectin")
    appointment: Mapped[Appointment] = relationship(lazy="selectin")

    @property
    def actor_name(self) -> str:
        return self.actor.name

    # Context for the global audit log view (so each row is readable without a second lookup).
    @property
    def appointment_type(self) -> str:
        return self.appointment.appointment_type

    @property
    def patient_name(self) -> str:
        return self.appointment.patient_name

    @property
    def provider_name(self) -> str:
        return self.appointment.provider_name


class Notification(Base):
    """Problem 3: transactional outbox + in-app inbox. A row is inserted in the SAME transaction as the appointment
    change (so a committed change always has its notification recorded), then delivered after commit by
    notifications.dispatch_pending(). Delivery problems only ever touch this table, never the appointment.

    delivery_status: pending -> sending -> sent | failed (failed rows are retried until MAX_DELIVERY_ATTEMPTS)."""
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("delivery_status IN ('pending','sending','sent','failed')", name="ck_notif_status"),
        Index("idx_notif_recipient", "recipient_id", "id"),
        Index("idx_notif_undelivered", "id", postgresql_where=text("delivery_status <> 'sent'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"))
    event_id: Mapped[int] = mapped_column(ForeignKey("appointment_events.id"))
    kind: Mapped[str]                      # created | confirmed | rescheduled | cancelled
    channel: Mapped[str] = mapped_column(default="email")
    to_address: Mapped[str]                # snapshot of the address at the time of the change
    subject: Mapped[str]
    body: Mapped[str]
    delivery_status: Mapped[str] = mapped_column(default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    claimed_at: Mapped[datetime | None]    # time of the latest send attempt (backoff + crash recovery)
    sent_at: Mapped[datetime | None]
    read_at: Mapped[datetime | None]       # in-app inbox: null = unread

    @property
    def gave_up(self) -> bool:
        """True once every attempt has failed: the inbox entry stays, but nothing will be sent any more."""
        return self.delivery_status == "failed" and self.attempts >= MAX_DELIVERY_ATTEMPTS
