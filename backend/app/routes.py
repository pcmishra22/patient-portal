from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query
from sqlalchemy.orm import Session

from . import database, notifications, services
from .models import User
from .schemas import (AppointmentOut, AuditEntryOut, CreateIn, EventOut, NotificationOut, RescheduleIn, UserOut,
                      VersionIn)
from .services import ServiceError

router = APIRouter(prefix="/api")


def get_session():
    session = database.session()
    try:
        yield session
    finally:
        session.close()


def current_user(x_user_id: str | None = Header(default=None)) -> User:
    """Demo 'auth': the role switcher sends X-User-Id. NOT real authentication (out of scope)."""
    with database.session() as s:
        user = s.get(User, x_user_id) if x_user_id else None
    if user is None:
        raise ServiceError(401, "Select a demo user first.", "unauthorized")
    return user


@router.get("/users", response_model=list[UserOut])
def users(session: Session = Depends(get_session)):
    return [UserOut.model_validate(u) for u in services.list_users(session)]


@router.get("/appointments", response_model=list[AppointmentOut])
def list_appointments(session: Session = Depends(get_session), user: User = Depends(current_user)):
    return [AppointmentOut.model_validate(a) for a in services.list_appointments(session, user)]


@router.post("/appointments", response_model=AppointmentOut, status_code=201)
def create(body: CreateIn, tasks: BackgroundTasks, session: Session = Depends(get_session),
           user: User = Depends(current_user)):
    appt = services.create_appointment(session, user, body.provider_id, body.start_time, body.duration_min,
                                       body.appointment_type, body.reason)
    tasks.add_task(notifications.dispatch_pending)  # after commit + response; never affects the request
    return AppointmentOut.model_validate(appt)


@router.post("/appointments/{appt_id}/confirm", response_model=AppointmentOut)
def confirm(appt_id: int, body: VersionIn, tasks: BackgroundTasks,
            session: Session = Depends(get_session), user: User = Depends(current_user)):
    appt = services.apply_change(session, user, appt_id, body.version, "confirm")
    tasks.add_task(notifications.dispatch_pending)  # outbox row was committed with the confirm itself
    return AppointmentOut.model_validate(appt)


@router.patch("/appointments/{appt_id}/reschedule", response_model=AppointmentOut)
def reschedule(appt_id: int, body: RescheduleIn, tasks: BackgroundTasks,
               session: Session = Depends(get_session), user: User = Depends(current_user)):
    appt = services.apply_change(session, user, appt_id, body.version, "reschedule", body.start_time)
    tasks.add_task(notifications.dispatch_pending)
    return AppointmentOut.model_validate(appt)


@router.post("/appointments/{appt_id}/cancel", response_model=AppointmentOut)
def cancel(appt_id: int, body: VersionIn, tasks: BackgroundTasks, session: Session = Depends(get_session),
           user: User = Depends(current_user)):
    appt = services.apply_change(session, user, appt_id, body.version, "cancel")
    tasks.add_task(notifications.dispatch_pending)
    return AppointmentOut.model_validate(appt)


@router.get("/appointments/{appt_id}/history", response_model=list[EventOut])
def history(appt_id: int, session: Session = Depends(get_session), user: User = Depends(current_user)):
    return [EventOut.model_validate(e) for e in services.history(session, user, appt_id)]


@router.get("/audit-log", response_model=list[AuditEntryOut])
def audit_log(action: str | None = None, appointment_id: int | None = None, limit: int = Query(200, ge=1, le=500),
              session: Session = Depends(get_session), user: User = Depends(current_user)):
    """Every audited change on every appointment the acting user is part of, newest first."""
    return [AuditEntryOut.model_validate(e) for e in services.audit_log(session, user, action, appointment_id, limit)]


@router.get("/notifications", response_model=list[NotificationOut])
def notifications_inbox(session: Session = Depends(get_session), user: User = Depends(current_user)):
    return [NotificationOut.model_validate(n) for n in services.list_notifications(session, user)]


@router.post("/notifications/read", status_code=204)
def mark_all_read(session: Session = Depends(get_session), user: User = Depends(current_user)):
    services.mark_notifications_read(session, user)


@router.post("/notifications/{notif_id}/read", status_code=204)
def mark_one_read(notif_id: int, session: Session = Depends(get_session), user: User = Depends(current_user)):
    services.mark_notifications_read(session, user, notif_id)
