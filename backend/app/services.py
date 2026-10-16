"""All business rules live here. Routes only translate HTTP <-> these functions.

Every change to an existing appointment goes through ONE function, apply_change(), which in a single transaction:
locks the row, authorizes, checks the version, enforces the state machine, updates, appends the audit event and queues
the notification. State, history and notification intent therefore commit or roll back together.

Where each of the four problems is enforced:
  1 stale updates   -> SELECT ... FOR UPDATE + explicit version compare (+ UPDATE ... WHERE version as a 2nd guard)
  2 audit history   -> same transaction, immutable table
  3 notification    -> outbox row in the transaction; delivery after commit (notifications.py)
  4 no double-book  -> PostgreSQL EXCLUDE constraint (models.py) is the guarantee; a per-provider lock keeps competing
                       writers orderly; this module maps the constraint's error to a friendly 409"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import or_, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .models import Appointment, AppointmentEvent, Notification, User
from .schemas import AppointmentOut

EVENT_FOR = {"confirm": "confirmed", "reschedule": "rescheduled", "cancel": "cancelled"}
STALE = "This appointment was changed by someone else while you were viewing it."
OVERLAP = "That time overlaps another confirmed appointment for this provider."
EXCLUSION_VIOLATION = "23P01"
RETRYABLE = {"40P01", "40001"}  # deadlock_detected, serialization_failure


class ServiceError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None, current: AppointmentOut | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.current = status, message, code, current


# ---------- helpers ----------
def _utc(dt: datetime) -> datetime:
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


@contextmanager
def write_tx(session: Session):
    """One transaction: commits on success, rolls back on ANY exception (including ServiceError)."""
    try:
        with session.begin():
            yield
    except DBAPIError as exc:
        if _sqlstate(exc) in RETRYABLE:  # should not happen (we lock in a fixed order), but never leak a 500
            raise ServiceError(503, "The system is busy. Please try again.", "busy") from exc
        raise


def _lock_provider(session: Session, provider_id: str) -> None:
    """Serialize everything that can make ONE provider's confirmed time ranges overlap. FOR NO KEY UPDATE on the
    provider's row is self-exclusive but does not conflict with the foreign-key checks of unrelated inserts. This is a
    safeguard, not the guarantee: the exclusion constraint alone keeps the invariant (tests run both ways), but
    ordering the writers means racing requests queue up and get a clean 409 rather than relying on Postgres'
    deadlock detection. Lock order is always: appointment row, then provider row."""
    session.execute(select(User.id).where(User.id == provider_id).with_for_update(key_share=True))


def _values(status: str, start: datetime, end: datetime) -> dict:
    return {"status": status, "start_time": start.isoformat(), "end_time": end.isoformat()}


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")


def _log_event(session: Session, actor: User, action: str, appt: Appointment,
               old: SimpleNamespace | None, status: str, start: datetime, end: datetime) -> None:
    """Append the audit event AND queue the notification for the other party, in the caller's transaction."""
    event = AppointmentEvent(
        appointment_id=appt.id, actor_id=actor.id, actor_role=actor.role, action=action,
        from_status=old.status if old else None, to_status=status,
        from_start=old.start_time if old else None, to_start=start,
        from_end=old.end_time if old else None, to_end=end,
        old_values=_values(old.status, old.start_time, old.end_time) if old else None,
        new_values=_values(status, start, end))
    session.add(event)
    session.flush()
    _queue_notification(session, actor, event, appt, old, start)


def _queue_notification(session: Session, actor: User, event: AppointmentEvent, appt: Appointment,
                        old: SimpleNamespace | None, start: datetime) -> None:
    """Outbox row for the person on the other side of the change. Only DB writes happen here (no network), so it
    cannot slow the change down; actual delivery runs after commit (notifications.dispatch_pending)."""
    patient, provider = session.get(User, appt.patient_id), session.get(User, appt.provider_id)
    kind, atype = event.action, appt.appointment_type
    if kind == "created":
        to, subject = provider, "New appointment request"
        body = f"{patient.name} requested a {atype} on {_fmt(start)}. Reason: {appt.reason}"
    elif kind == "confirmed":
        to, subject = patient, "Appointment confirmed"
        body = f"Your {atype} with {provider.name} is confirmed for {_fmt(start)}."
    elif kind == "rescheduled":
        to, subject = patient, "Appointment rescheduled"
        body = (f"{provider.name} moved your {atype} from {_fmt(old.start_time)} to {_fmt(start)}."
                f" Current status: {event.to_status}.")
    else:  # cancelled (by the patient)
        to, subject = provider, "Appointment cancelled"
        body = f"{patient.name} cancelled their {atype} on {_fmt(old.start_time)}."
    if to.id == actor.id:  # never notify people about their own action
        return
    session.add(Notification(
        recipient_id=to.id, appointment_id=appt.id, event_id=event.id, kind=kind, to_address=to.email,
        subject=subject, body=body))
    session.flush()


def _reload(session: Session, appt_id: int) -> Appointment:
    """Fresh copy straight from the database (safe to read after the session ends)."""
    return session.execute(select(Appointment).where(Appointment.id == appt_id)
                           .execution_options(populate_existing=True)).scalar_one()


def _can_view(actor: User, appt: Appointment) -> bool:
    return actor.id in (appt.patient_id, appt.provider_id)


def _require_future(start: datetime) -> None:
    if _utc(start) <= datetime.now(timezone.utc):
        raise ServiceError(400, "Choose a time in the future.", "invalid")


# ---------- queries ----------
def list_users(session: Session):
    return session.scalars(select(User).order_by(User.role.desc(), User.name)).all()


def get_user(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def list_appointments(session: Session, actor: User):
    return session.scalars(select(Appointment).where(
        or_(Appointment.patient_id == actor.id, Appointment.provider_id == actor.id))
        .order_by(Appointment.start_time, Appointment.id)).all()


def history(session: Session, actor: User, appt_id: int):
    appt = session.get(Appointment, appt_id)
    if appt is None or not _can_view(actor, appt):
        raise ServiceError(404, "Appointment not found.", "not_found")
    return session.scalars(select(AppointmentEvent).where(AppointmentEvent.appointment_id == appt_id)
                           .order_by(AppointmentEvent.id.desc())).all()


VALID_ACTIONS = set(EVENT_FOR.values()) | {"created"}


def audit_log(session: Session, actor: User, action: str | None = None, appt_id: int | None = None,
              limit: int = 200):
    """Global audit trail: every event on every appointment the actor is part of, newest first."""
    if action is not None and action not in VALID_ACTIONS:
        raise ServiceError(400, "Unknown action filter.", "invalid")
    mine = AppointmentEvent.appointment.has(
        or_(Appointment.patient_id == actor.id, Appointment.provider_id == actor.id))
    stmt = select(AppointmentEvent).where(mine)
    if action:
        stmt = stmt.where(AppointmentEvent.action == action)
    if appt_id is not None:
        stmt = stmt.where(AppointmentEvent.appointment_id == appt_id)
    return session.scalars(stmt.order_by(AppointmentEvent.id.desc()).limit(min(max(limit, 1), 500))).all()


def list_notifications(session: Session, actor: User, limit: int = 50):
    return session.scalars(select(Notification).where(Notification.recipient_id == actor.id)
                           .order_by(Notification.id.desc()).limit(limit)).all()


def mark_notifications_read(session: Session, actor: User, notif_id: int | None = None) -> None:
    """One notification, or all of the actor's unread ones. Only touches the actor's own rows."""
    stmt = update(Notification).where(Notification.recipient_id == actor.id, Notification.read_at.is_(None))
    if notif_id is not None:
        stmt = stmt.where(Notification.id == notif_id)
    with write_tx(session):
        session.execute(stmt.values(read_at=datetime.now(timezone.utc)))


# ---------- mutations ----------
def create_appointment(session: Session, actor: User, provider_id: str, start: datetime, duration_min: int,
                       appointment_type: str, reason: str) -> Appointment:
    if actor.role != "patient":
        raise ServiceError(403, "Only patients can request appointments.", "forbidden")
    if not reason.strip():
        raise ServiceError(400, "Please add a reason for the visit.", "invalid")
    _require_future(start)
    s = _utc(start)
    e = s + timedelta(minutes=duration_min)
    with write_tx(session):
        provider = session.get(User, provider_id)
        if provider is None or provider.role != "provider":
            raise ServiceError(400, "Choose a valid provider.", "invalid")
        appt = Appointment(patient_id=actor.id, provider_id=provider_id, start_time=s, end_time=e,
                           appointment_type=appointment_type.strip(), reason=reason.strip(), status="pending")
        session.add(appt)
        session.flush()
        _log_event(session, actor, "created", appt, None, "pending", s, e)
        result = _reload(session, appt.id)
    return result


def apply_change(session: Session, actor: User, appt_id: int, expected_version: int, action: str,
                 new_start: datetime | None = None) -> Appointment:
    """action: 'confirm' | 'reschedule' | 'cancel'. Returns the updated appointment (version bumped)."""
    with write_tx(session):
        # Row lock: a second request for the SAME appointment waits here, then re-reads the committed row and
        # (if it acted on an old version) is rejected below. READ COMMITTED re-fetches the locked row.
        appt = session.scalar(select(Appointment).where(Appointment.id == appt_id)
                              .with_for_update().execution_options(populate_existing=True))
        if appt is None or not _can_view(actor, appt):
            raise ServiceError(404, "Appointment not found.", "not_found")
        current = AppointmentOut.model_validate(appt)  # snapshot of the truth, returned to the client on 409

        # 1. Who may do what (independent of data freshness).
        if action in ("confirm", "reschedule") and not (actor.role == "provider" and actor.id == appt.provider_id):
            raise ServiceError(403, f"Only the assigned provider can {action} this appointment.", "forbidden")
        if action == "cancel" and not (actor.role == "patient" and actor.id == appt.patient_id):
            raise ServiceError(403, "Only the patient can cancel their appointment.", "forbidden")

        # 2. Stale check (Problem 1): the caller acted on an old view -> reject, save nothing, return the truth.
        if appt.version != expected_version:
            raise ServiceError(409, STALE, "stale", current)

        # 3. State machine: pending -> confirmed -> cancelled (cancelled is terminal).
        status, start, end = appt.status, appt.start_time, appt.end_time
        if action == "confirm":
            if status != "pending":
                raise ServiceError(400, "Only pending appointments can be confirmed.", "invalid_transition")
            status = "confirmed"
        elif action == "cancel":
            if status != "confirmed":
                raise ServiceError(400, "Only confirmed appointments can be cancelled.", "invalid_transition")
            status = "cancelled"
        elif action == "reschedule":
            if status == "cancelled":
                raise ServiceError(400, "Cancelled appointments cannot be rescheduled.", "invalid_transition")
            if new_start is None:
                raise ServiceError(400, "A new start time is required.", "invalid")
            _require_future(new_start)
            length = end - start
            start = _utc(new_start)
            end = start + length
            if start == appt.start_time:
                raise ServiceError(400, "That is already the appointment time.", "invalid")
        else:
            raise ValueError(action)

        # 4. Write. Problem 4 is enforced HERE by the database: if this UPDATE would make two confirmed
        #    appointments of one provider overlap, Postgres rejects it. We first take the provider lock so racing
        #    confirms/reschedules for the same provider run one at a time. version_id_col also makes the UPDATE
        #    itself conditional on the version.
        if status == "confirmed":
            _lock_provider(session, appt.provider_id)
        old = SimpleNamespace(status=appt.status, start_time=appt.start_time, end_time=appt.end_time)
        appt.status, appt.start_time, appt.end_time = status, start, end
        try:
            session.flush()
        except StaleDataError as exc:
            raise ServiceError(409, STALE, "stale", current) from exc
        except IntegrityError as exc:
            if _sqlstate(exc) == EXCLUSION_VIOLATION:
                raise ServiceError(409, OVERLAP, "overlap", current) from exc
            raise

        # 5. Same transaction: audit event + notification outbox row.
        _log_event(session, actor, EVENT_FOR[action], appt, old, status, start, end)
        result = _reload(session, appt.id)
    return result
