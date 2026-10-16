import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from datetime import datetime, timedelta, timezone

from app import database, notifications, seed, services

from app.main import app

from .conftest import confirm, hdr, request_appt, slot


def sql(query: str, **params):
    """Raw SQL straight to the database (bypasses the service layer on purpose)."""
    with database.engine().begin() as conn:
        return conn.execute(text(query), params)


# ---------- baseline ----------
def test_new_request_persists_as_pending(client):
    a = request_appt(client)
    assert a["status"] == "pending" and a["version"] == 1
    listed = client.get("/api/appointments", headers=hdr("p1")).json()
    assert [x["id"] for x in listed] == [a["id"]]
    assert client.get("/api/appointments", headers=hdr("dr1")).json()[0]["id"] == a["id"]
    assert client.get("/api/appointments", headers=hdr("p2")).json() == []  # other patient sees nothing


def test_patient_cannot_cancel_pending(client):
    a = request_appt(client)
    r = client.post(f"/api/appointments/{a['id']}/cancel", headers=hdr("p1"), json={"version": a["version"]})
    assert r.status_code == 400


def test_roles_are_enforced_server_side(client):
    a = request_appt(client)
    assert client.post(f"/api/appointments/{a['id']}/confirm", headers=hdr("p1"), json={"version": 1}).status_code == 403
    assert confirm(client, a, provider="dr2").status_code == 404  # unrelated provider can't even see it
    assert client.get("/api/appointments").status_code == 401  # no demo user header


def test_confirmed_can_be_cancelled_by_patient_and_is_terminal(client):
    a = confirm(client, request_appt(client)).json()
    r = client.post(f"/api/appointments/{a['id']}/cancel", headers=hdr("p1"), json={"version": a["version"]})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    c = r.json()
    r2 = client.patch(f"/api/appointments/{c['id']}/reschedule", headers=hdr("dr1"),
                      json={"version": c["version"], "startTime": slot(5, 9)})
    assert r2.status_code == 400


# ---------- Problem 1: stale updates ----------
def test_stale_cancel_gets_409_and_does_not_overwrite(client):
    a = confirm(client, request_appt(client)).json()                      # version 2, patient's screen
    moved = client.patch(f"/api/appointments/{a['id']}/reschedule", headers=hdr("dr1"),
                         json={"version": a["version"], "startTime": slot(3, 12)}).json()
    r = client.post(f"/api/appointments/{a['id']}/cancel", headers=hdr("p1"), json={"version": a["version"]})  # stale
    assert r.status_code == 409
    assert r.json()["current"]["startTime"] == moved["startTime"]        # client is told the truth
    now = client.get("/api/appointments", headers=hdr("p1")).json()[0]
    assert now["status"] == "confirmed" and now["version"] == moved["version"]  # nothing was saved


def test_stale_confirm_gets_409(client):
    a = request_appt(client)
    client.patch(f"/api/appointments/{a['id']}/reschedule", headers=hdr("dr1"),
                 json={"version": 1, "startTime": slot(3, 12)})
    assert confirm(client, a).status_code == 409


# ---------- Problem 2: audit history ----------
def test_history_answers_when_who_and_before(client):
    a = request_appt(client, start=slot(2, 10))
    a = confirm(client, a).json()
    moved = client.patch(f"/api/appointments/{a['id']}/reschedule", headers=hdr("dr1"),
                         json={"version": a["version"], "startTime": slot(4, 14)}).json()
    h = client.get(f"/api/appointments/{a['id']}/history", headers=hdr("p1")).json()
    assert [e["action"] for e in h] == ["rescheduled", "confirmed", "created"]  # newest first
    r = h[0]
    assert r["actorRole"] == "provider" and r["actorName"] == "Dr. Priya Shah"
    assert r["fromStart"] == a["startTime"] and r["toStart"] == moved["startTime"]
    assert r["fromStatus"] == "confirmed" and r["createdAt"]
    assert h[2]["fromStart"] is None  # the original booking time is preserved in the 'created' event


def test_events_are_append_only(client, db):
    request_appt(client)
    with pytest.raises(IntegrityError, match="events_are_append_only"):
        sql("DELETE FROM appointment_events")            # bypass the service layer entirely
    with pytest.raises(IntegrityError, match="events_are_append_only"):
        sql("UPDATE appointment_events SET action='confirmed'")


# ---------- Problem 3: notifications ----------
def test_notification_failure_does_not_undo_confirmation(client, monkeypatch):
    def boom(_):
        raise RuntimeError("provider down")
    monkeypatch.setattr(notifications, "deliver", boom)
    a = request_appt(client)
    r = confirm(client, a)
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert client.get("/api/appointments", headers=hdr("p1")).json()[0]["status"] == "confirmed"


def test_notification_sent_after_confirm(client, caplog):
    caplog.set_level("INFO", logger="portal.notifications")
    confirm(client, request_appt(client))
    assert "Would send email to alex.rivera@example.com: [Appointment confirmed]" in caplog.text


# ---------- Problem 4: no overlapping confirmed appointments ----------
def test_overlapping_confirm_rejected(client):
    a = confirm(client, request_appt(client, start=slot(2, 10))).json()
    b = request_appt(client, patient="p2", start=slot(2, 10, 15))
    r = confirm(client, b)
    assert r.status_code == 409 and "overlaps" in r.json()["message"]
    assert client.get("/api/appointments", headers=hdr("p2")).json()[0]["status"] == "pending"
    assert a["status"] == "confirmed"


def test_back_to_back_is_allowed(client):
    confirm(client, request_appt(client, start=slot(2, 14), minutes=60))           # 14:00-15:00
    assert confirm(client, request_appt(client, patient="p2", start=slot(2, 15))).status_code == 200  # 15:00-15:30


def test_different_providers_may_overlap(client):
    confirm(client, request_appt(client, provider="dr1", start=slot(2, 10)))
    assert confirm(client, request_appt(client, patient="p2", provider="dr2", start=slot(2, 10)), provider="dr2").status_code == 200


def test_pending_overlap_is_fine_but_reschedule_into_confirmed_slot_is_not(client):
    confirm(client, request_appt(client, start=slot(2, 10)))
    pending = request_appt(client, patient="p2", start=slot(3, 10))
    ok = client.patch(f"/api/appointments/{pending['id']}/reschedule", headers=hdr("dr1"),
                      json={"version": 1, "startTime": slot(2, 10, 15)})
    assert ok.status_code == 200 and ok.json()["status"] == "pending"   # pending rows never block
    assert confirm(client, ok.json()).status_code == 409


def test_confirmed_reschedule_into_conflict_rejected_but_nudging_itself_is_fine(client):
    a = confirm(client, request_appt(client, start=slot(2, 10))).json()
    b = confirm(client, request_appt(client, patient="p2", start=slot(2, 11))).json()
    nudge = client.patch(f"/api/appointments/{a['id']}/reschedule", headers=hdr("dr1"),
                         json={"version": a["version"], "startTime": slot(2, 10, 15)})
    assert nudge.status_code == 200                                      # overlaps only itself
    clash = client.patch(f"/api/appointments/{b['id']}/reschedule", headers=hdr("dr1"),
                         json={"version": b["version"], "startTime": slot(2, 10, 30)})
    assert clash.status_code == 409


def race_confirms(n=8, day=2):
    """n threads, each with its own session/connection, confirm n pending requests for the SAME provider and slot."""
    start = datetime.fromisoformat(slot(day, 10).replace("Z", "+00:00"))
    with database.session() as s:
        patient, provider = services.get_user(s, "p1"), services.get_user(s, "dr1")
    ids = []
    for _ in range(n):
        with database.session() as s:
            ids.append(services.create_appointment(s, patient, "dr1", start, 30, "Check-up", "race").id)
    return run_threads(ids, lambda s, appt_id: services.apply_change(s, provider, appt_id, 1, "confirm"))


def run_threads(args, fn):
    """Run fn(session, arg) once per arg, all released at the same instant. Returns the list of HTTP-style results."""
    results, barrier = [], threading.Barrier(len(args))

    def worker(arg):
        with database.session() as s:
            try:
                barrier.wait()
                fn(s, arg)
                results.append(200)
            except services.ServiceError as e:
                results.append(e.status)
            except Exception as e:  # noqa: BLE001 - an unexpected DB error would be an HTTP 500 in production
                results.append(f"500:{type(e).__name__}")

    threads = [threading.Thread(target=worker, args=(a,)) for a in args]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


def confirmed_count():
    return sql("SELECT COUNT(*) FROM appointments WHERE status='confirmed'").scalar()


def test_simultaneous_confirms_exactly_one_wins(db):
    results = race_confirms(8)
    assert sorted(results) == [200] + [409] * 7          # one winner, seven clean conflicts, zero 500s
    assert confirmed_count() == 1


def test_repeated_races_never_error_and_never_double_book(db):
    for day in range(2, 8):                               # six independent 8-way races
        results = race_confirms(8, day)
        assert sorted(results) == [200] + [409] * 7, results
    assert confirmed_count() == 6


def test_constraint_alone_still_prevents_double_booking_without_the_provider_lock(db, monkeypatch):
    """Defense in depth: even if the application-level provider lock is removed, the DATABASE keeps the invariant."""
    monkeypatch.setattr(services, "_lock_provider", lambda session, provider_id: None)
    for day in range(2, 6):
        results = race_confirms(8, day)
        assert results.count(200) == 1, results            # never two winners
        assert set(results) <= {200, 409, 503}, results    # losers: conflict, or a retryable 'busy' - never a crash
    assert confirmed_count() == 4


def test_two_requests_on_the_same_appointment_one_wins(client, db):
    a = confirm(client, request_appt(client)).json()
    with database.session() as s:
        patient = services.get_user(s, "p1")
    results = run_threads([1, 2], lambda s, _: services.apply_change(s, patient, a["id"], a["version"], "cancel"))
    assert sorted(results) == [200, 409]                  # the second saw the new version and was rejected


def test_racing_reschedules_into_the_same_slot_one_wins(client, db):
    a = confirm(client, request_appt(client, start=slot(2, 10))).json()
    b = confirm(client, request_appt(client, patient="p2", start=slot(2, 12))).json()
    with database.session() as s:
        provider = services.get_user(s, "dr1")
    target = datetime.fromisoformat(slot(3, 14).replace("Z", "+00:00"))
    versions = {a["id"]: a["version"], b["id"]: b["version"]}
    results = run_threads([a["id"], b["id"]],
                          lambda s, i: services.apply_change(s, provider, i, versions[i], "reschedule", target))
    assert sorted(results) == [200, 409]
    assert confirmed_count() == 2                         # both still confirmed, but never at the same time


def test_exclusion_constraint_blocks_overlap_even_if_service_is_bypassed(client, db):
    confirm(client, request_appt(client, start=slot(2, 10)))
    b = request_appt(client, patient="p2", start=slot(2, 10, 15))
    with pytest.raises(IntegrityError, match="no_provider_overlap"):
        sql("UPDATE appointments SET status='confirmed' WHERE id=:i", i=b["id"])
    # ...and back-to-back is accepted by the constraint itself (half-open ranges)
    c = request_appt(client, patient="p2", start=slot(2, 10, 30))
    sql("UPDATE appointments SET status='confirmed' WHERE id=:i", i=c["id"])


def test_init_db_is_idempotent(db):
    database.init_db()
    database.init_db()


def test_seeded_database_has_coherent_history(empty_db):
    seed.seed_if_empty()
    seed.seed_if_empty()  # idempotent
    c = TestClient(app)
    appts = c.get("/api/appointments", headers=hdr("dr1")).json()
    assert [a["status"] for a in appts] == ["confirmed", "pending", "pending"]
    h = c.get(f"/api/appointments/{appts[0]['id']}/history", headers=hdr("dr1")).json()
    assert [e["action"] for e in h] == ["confirmed", "created"]


# ---------- Audit log (global view) ----------
def test_audit_log_lists_every_change_newest_first_with_context(client):
    a = confirm(client, request_appt(client)).json()
    client.patch(f"/api/appointments/{a['id']}/reschedule", headers=hdr("dr1"),
                 json={"version": a["version"], "startTime": slot(4, 14)})
    log = client.get("/api/audit-log", headers=hdr("p1")).json()
    assert [e["action"] for e in log] == ["rescheduled", "confirmed", "created"]
    top = log[0]
    assert top["actorName"] == "Dr. Priya Shah" and top["patientName"] == "Alex Rivera"
    assert top["providerName"] == "Dr. Priya Shah" and top["appointmentType"] == "Check-up"
    assert top["fromStart"] != top["toStart"]


def test_audit_log_is_scoped_filtered_and_requires_a_user(client):
    a = request_appt(client)                                   # p1 <-> dr1
    request_appt(client, patient="p2", provider="dr2", start=slot(3, 10))
    confirm(client, a)
    assert {e["appointmentId"] for e in client.get("/api/audit-log", headers=hdr("p1")).json()} == {a["id"]}
    assert len(client.get("/api/audit-log", headers=hdr("dr1")).json()) == 2        # created + confirmed
    assert len(client.get("/api/audit-log", headers=hdr("p2")).json()) == 1         # only their own request
    only = client.get("/api/audit-log?action=confirmed", headers=hdr("dr1")).json()
    assert [e["action"] for e in only] == ["confirmed"]
    assert client.get("/api/audit-log?action=bogus", headers=hdr("dr1")).status_code == 400
    assert client.get("/api/audit-log").status_code == 401


def test_rejected_changes_leave_no_audit_event_and_no_notification(client):
    a = request_appt(client)
    before = len(client.get("/api/audit-log", headers=hdr("dr1")).json())
    stale = client.post(f"/api/appointments/{a['id']}/confirm", headers=hdr("dr1"), json={"version": 99})
    assert stale.status_code == 409
    assert len(client.get("/api/audit-log", headers=hdr("dr1")).json()) == before
    assert len(client.get("/api/notifications", headers=hdr("p1")).json()) == 0


# ---------- Notifications on every update ----------
def test_each_update_notifies_the_other_party(client):
    a = request_appt(client)                                                       # patient requests
    inbox_dr = client.get("/api/notifications", headers=hdr("dr1")).json()
    assert [n["kind"] for n in inbox_dr] == ["created"] and inbox_dr[0]["subject"] == "New appointment request"
    assert client.get("/api/notifications", headers=hdr("p1")).json() == []         # no self-notification

    a = confirm(client, a).json()                                                  # provider confirms -> patient
    moved = client.patch(f"/api/appointments/{a['id']}/reschedule", headers=hdr("dr1"),
                         json={"version": a["version"], "startTime": slot(4, 14)}).json()   # -> patient
    client.post(f"/api/appointments/{a['id']}/cancel", headers=hdr("p1"), json={"version": moved["version"]})  # -> provider

    patient_inbox = client.get("/api/notifications", headers=hdr("p1")).json()
    assert [n["kind"] for n in patient_inbox] == ["rescheduled", "confirmed"]       # newest first
    assert "moved your Check-up from" in patient_inbox[0]["body"]
    assert [n["kind"] for n in client.get("/api/notifications", headers=hdr("dr1")).json()] == ["cancelled", "created"]
    assert client.get("/api/notifications", headers=hdr("p2")).json() == []         # strangers see nothing


def test_notifications_are_delivered_after_the_change_and_marked_sent(client):
    confirm(client, request_appt(client))
    assert {n["deliveryStatus"] for n in client.get("/api/notifications", headers=hdr("p1")).json()} == {"sent"}


def test_mark_read_only_touches_own_notifications(client):
    a = confirm(client, request_appt(client)).json()
    mine = client.get("/api/notifications", headers=hdr("p1")).json()
    dr_notif = client.get("/api/notifications", headers=hdr("dr1")).json()[0]
    assert all(n["readAt"] is None for n in mine)
    assert client.post(f"/api/notifications/{dr_notif['id']}/read", headers=hdr("p1")).status_code == 204  # not theirs
    assert client.get("/api/notifications", headers=hdr("dr1")).json()[0]["readAt"] is None
    assert client.post("/api/notifications/read", headers=hdr("p1")).status_code == 204
    assert all(n["readAt"] for n in client.get("/api/notifications", headers=hdr("p1")).json())
    assert client.get("/api/notifications", headers=hdr("dr1")).json()[0]["readAt"] is None


def test_failed_delivery_is_recorded_and_retried_later(client, monkeypatch):
    real_deliver = notifications.deliver
    monkeypatch.setattr(notifications, "deliver", boom_deliver)
    a = confirm(client, request_appt(client))
    assert a.status_code == 200 and a.json()["status"] == "confirmed"                 # confirm unaffected
    n = client.get("/api/notifications", headers=hdr("p1")).json()[0]
    assert n["deliveryStatus"] == "failed"                                            # recorded, not lost
    monkeypatch.setattr(notifications, "deliver", real_deliver)                        # provider recovers
    notifications.dispatch_pending(now=later())                                        # a later drain retries it
    assert client.get("/api/notifications", headers=hdr("p1")).json()[0]["deliveryStatus"] == "sent"


def test_slow_notifier_does_not_hold_the_database(client, monkeypatch):
    """deliver() runs outside any transaction, so even a hung provider cannot block other writes."""
    started = threading.Event()
    release = threading.Event()

    def hang(_):
        started.set()
        release.wait(5)

    monkeypatch.setattr(notifications, "deliver", hang)
    a = request_appt(client)
    t = threading.Thread(target=notifications.dispatch_pending)
    t.start()
    assert started.wait(5)
    b = request_appt(client, patient="p2", start=slot(3, 10))      # writes still go straight through
    assert b["status"] == "pending"
    release.set()
    t.join()


def test_startup_drain_sends_notifications_left_over_from_a_crash(client, monkeypatch, caplog):
    real_deliver = notifications.deliver
    monkeypatch.setattr(notifications, "deliver", boom_deliver)
    confirm(client, request_appt(client))
    monkeypatch.setattr(notifications, "deliver", real_deliver)
    caplog.set_level("INFO", logger="portal.notifications")
    notifications.dispatch_pending(now=later())                                        # what the lifespan hook does
    assert "Would send email to alex.rivera@example.com" in caplog.text


def later(minutes: int = 60) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def boom_deliver(_):
    raise RuntimeError("provider down")


# ---------- Outbox hardening: crash recovery, backoff, give-up, retry worker, privacy ----------
def _set_notification(**cols):
    """Edit the PATIENT's notification row directly (simulating crashes / old claims)."""
    sets = ", ".join(f"{k}=:{k}" for k in cols)
    sql(f"UPDATE notifications SET {sets} WHERE recipient_id='p1'", **cols)


def _status(client, user="p1"):
    return client.get("/api/notifications", headers=hdr(user)).json()[0]


def ago(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


def test_row_stuck_in_sending_after_a_crash_is_reclaimed(client, monkeypatch):
    sent = []
    monkeypatch.setattr(notifications, "deliver", lambda n: sent.append(n["id"]))
    confirm(client, request_appt(client))
    _set_notification(delivery_status="sending", sent_at=None, claimed_at=ago(minutes=5))  # a worker died mid-send
    sent.clear()
    notifications.dispatch_pending()
    assert _status(client)["deliveryStatus"] == "sent" and len(sent) == 1


def test_fresh_sending_claim_is_not_stolen_from_a_live_worker(client, monkeypatch):
    sent = []
    monkeypatch.setattr(notifications, "deliver", lambda n: sent.append(n["id"]))
    confirm(client, request_appt(client))
    _set_notification(delivery_status="sending", sent_at=None, claimed_at=ago(seconds=5))
    sent.clear()
    notifications.dispatch_pending()
    assert sent == [] and _status(client)["deliveryStatus"] == "sending"   # still owned by the other worker


def test_concurrent_drains_never_send_the_same_notification_twice(client, monkeypatch):
    sent, lock = [], threading.Lock()

    def record(n):
        with lock:
            sent.append(n["id"])
        threading.Event().wait(0.05)                       # widen the race window

    for i in range(6):
        request_appt(client, patient="p2", start=slot(3 + i, 9))
    monkeypatch.setattr(notifications, "deliver", record)
    _set_all_pending()
    threads = [threading.Thread(target=notifications.dispatch_pending) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(sent) == 6 and len(set(sent)) == 6          # SKIP LOCKED: each row claimed by exactly one worker


def _set_all_pending():
    sql("UPDATE notifications SET delivery_status='pending', sent_at=NULL, attempts=0, claimed_at=NULL")


def test_failed_delivery_waits_for_backoff_then_retries(client, monkeypatch):
    monkeypatch.setattr(notifications, "deliver", boom_deliver)
    confirm(client, request_appt(client))                       # attempt 1 fails inside the background task
    assert _status(client)["deliveryStatus"] == "failed"
    ok = []
    monkeypatch.setattr(notifications, "deliver", lambda n: ok.append(n["id"]))
    notifications.dispatch_pending()                            # immediately: still inside the backoff window
    assert ok == [] and _status(client)["deliveryStatus"] == "failed"
    notifications.dispatch_pending(now=later(1))                # a minute later: both failed rows are retried
    assert len(ok) == 2 and _status(client)["deliveryStatus"] == "sent"


def test_delayed_retries_do_not_block_later_pending_notifications(client):
    request_appt(client)
    seed_id = sql("SELECT id FROM notifications").scalar()
    now = datetime.now(timezone.utc)
    sql("UPDATE notifications SET delivery_status='failed', attempts=1, last_error='retry later', claimed_at=:now "
        "WHERE id=:seed_id", now=now, seed_id=seed_id)
    sql("""
        INSERT INTO notifications
            (recipient_id, appointment_id, event_id, kind, channel, to_address, subject, body,
             delivery_status, attempts, last_error, created_at, claimed_at)
        SELECT n.recipient_id, n.appointment_id, n.event_id, n.kind, n.channel, n.to_address, n.subject, n.body,
               'failed', 1, 'retry later', now(), now()
        FROM notifications AS n CROSS JOIN generate_series(1, 99)
        WHERE n.id=:seed_id
    """, seed_id=seed_id)
    sql("""
        INSERT INTO notifications
            (recipient_id, appointment_id, event_id, kind, channel, to_address, subject, body,
             delivery_status, attempts, created_at)
        SELECT n.recipient_id, n.appointment_id, n.event_id, n.kind, n.channel, n.to_address, n.subject, n.body,
               'pending', 0, now()
        FROM notifications AS n WHERE n.id=:seed_id
    """, seed_id=seed_id)
    pending_id = sql("SELECT max(id) FROM notifications").scalar()

    claimed = notifications._claim_one(now)

    assert claimed is not None and claimed["id"] == pending_id


def test_notification_is_abandoned_after_max_attempts_and_flagged(client, monkeypatch, caplog):
    monkeypatch.setattr(notifications, "deliver", boom_deliver)
    confirm(client, request_appt(client))
    for i in range(1, 10):                                       # drain far more often than needed
        notifications.dispatch_pending(now=later(60 * i))
    n = _status(client)
    assert n["deliveryStatus"] == "failed" and n["gaveUp"] is True
    assert sql("SELECT attempts FROM notifications WHERE recipient_id='p1'").scalar() == notifications.MAX_ATTEMPTS
    assert "abandoned after" in caplog.text


def test_retry_worker_delivers_in_the_background(client, monkeypatch):
    monkeypatch.setattr(notifications, "deliver", boom_deliver)
    confirm(client, request_appt(client))
    assert _status(client)["deliveryStatus"] == "failed"
    monkeypatch.setattr(notifications, "deliver", lambda n: None)
    monkeypatch.setattr(notifications, "RETRY_DELAYS", (0,))     # no waiting in the test
    stop = threading.Event()
    worker = notifications.start_retry_worker(stop, interval=0.05)
    try:
        for _ in range(100):
            if _status(client)["deliveryStatus"] == "sent":
                break
            threading.Event().wait(0.05)
        assert _status(client)["deliveryStatus"] == "sent"
    finally:
        stop.set()
        worker.join(2)


def test_logs_never_contain_the_message_body(client, caplog):
    caplog.set_level("INFO", logger="portal.notifications")
    request_appt(client)                                         # body of this notification contains the visit reason
    assert "Reason:" not in caplog.text and "[New appointment request]" in caplog.text
