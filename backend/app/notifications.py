"""Notification delivery (stub) for the transactional outbox.

Flow: services.py inserts a `notifications` row in the SAME transaction as the appointment change. After the
response is sent, routes call dispatch_pending() as a FastAPI background task, and a small worker thread
(start_retry_worker) calls it every few seconds. dispatch_pending() claims ONE row at a time (short transaction,
FOR UPDATE SKIP LOCKED so concurrent workers never pick the same row), "sends" it OUTSIDE any transaction (a slow
provider never holds a database lock), then records the result. Every error is caught here: a broken notifier can
never undo or slow an appointment change.

Delivery guarantees:
  * Failed sends are retried with growing delays (RETRY_DELAYS) up to MAX_DELIVERY_ATTEMPTS, then flagged gave_up.
  * A row stuck in 'sending' (the process died after claiming it) is reclaimed once STALE_CLAIM has passed,
    so a crash can't lose a notification. (At-least-once: in the rare crash-after-send case it may be sent twice.)

NOTIFY_MODE=ok (default) | slow (sleeps 5s per message) | fail (raises)  -- handy for demoing Problem 3."""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from . import database
from .models import MAX_DELIVERY_ATTEMPTS, Notification

log = logging.getLogger("portal.notifications")
MAX_ATTEMPTS = MAX_DELIVERY_ATTEMPTS
STALE_CLAIM = timedelta(seconds=60)          # a 'sending' claim older than this belongs to a dead worker
RETRY_DELAYS = (10, 30, 120, 600)            # seconds to wait after failed attempt 1, 2, 3, 4+
MAX_PER_DRAIN = 50


def deliver(n: dict) -> None:
    """The stub. Replace the body with a real email/SMS provider call.
    Privacy: log identifiers and the subject only, never the message body (it can contain visit details)."""
    mode = os.environ.get("NOTIFY_MODE", "ok")
    if mode == "slow":
        time.sleep(5)
    if mode == "fail":
        raise RuntimeError("simulated notification provider outage")
    log.info("Would send %s to %s: [%s] (notification %s, appointment %s)",
             n["channel"], n["to_address"], n["subject"], n["id"], n["appointment_id"])


def _retry_delay(attempts: int) -> timedelta:
    return timedelta(seconds=RETRY_DELAYS[min(max(attempts, 1), len(RETRY_DELAYS)) - 1])


def _is_due(n: Notification, now: datetime) -> bool:
    if n.delivery_status == "pending" or n.claimed_at is None:
        return True
    wait = STALE_CLAIM if n.delivery_status == "sending" else _retry_delay(n.attempts)
    return n.claimed_at + wait <= now


def _claim_one(now: datetime) -> dict | None:
    """Short transaction: pick the next due row, lock it (SKIP LOCKED) and mark it 'sending'."""
    retry_windows = []
    for index, delay in enumerate(RETRY_DELAYS):
        if len(RETRY_DELAYS) == 1:
            attempts = Notification.attempts >= 0
        elif index == 0:
            attempts = Notification.attempts <= 1
        elif index == len(RETRY_DELAYS) - 1:
            attempts = Notification.attempts >= index + 1
        else:
            attempts = Notification.attempts == index + 1
        retry_windows.append(and_(attempts, Notification.claimed_at <= now - timedelta(seconds=delay)))
    due = or_(
        Notification.delivery_status == "pending",
        and_(Notification.delivery_status == "failed", Notification.attempts < MAX_ATTEMPTS,
             or_(Notification.claimed_at.is_(None), *retry_windows)),
        and_(Notification.delivery_status == "sending",
             or_(Notification.claimed_at.is_(None), Notification.claimed_at <= now - STALE_CLAIM)),
    )
    with database.session() as s, s.begin():
        candidates = s.scalars(select(Notification).where(due).order_by(Notification.id).limit(100)).all()
        for cand in candidates:
            if not _is_due(cand, now):
                continue
            n = s.scalar(select(Notification).where(Notification.id == cand.id)
                         .with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if n is None or not _is_due(n, now):  # another worker claimed it between our read and our lock
                continue
            if n.attempts >= MAX_ATTEMPTS:         # worker died during the final attempt: nothing left to try
                n.delivery_status, n.last_error = "failed", "worker died during the final attempt"
                log.error("Notification %s abandoned after %s attempts", n.id, n.attempts)
                continue
            n.delivery_status, n.attempts, n.claimed_at = "sending", n.attempts + 1, now
            return {"id": n.id, "channel": n.channel, "to_address": n.to_address, "subject": n.subject,
                    "appointment_id": n.appointment_id}
    return None


def _finish(notif_id: int, error: str | None) -> None:
    with database.session() as s, s.begin():
        n = s.get(Notification, notif_id)
        n.delivery_status = "failed" if error else "sent"
        n.last_error = error
        n.sent_at = None if error else datetime.now(timezone.utc)
        if error and n.attempts >= MAX_ATTEMPTS:
            log.error("Notification %s abandoned after %s failed attempts: %s", n.id, n.attempts, error)


def dispatch_pending(now: datetime | None = None) -> None:
    """Send everything that is due. `now` can be injected (tests); by default the real clock is used."""
    try:
        for _ in range(MAX_PER_DRAIN):
            item = _claim_one(now or datetime.now(timezone.utc))
            if item is None:
                break
            try:
                deliver(item)
                _finish(item["id"], None)
            except Exception as exc:  # noqa: BLE001 - isolate every failure
                log.exception("Notification %s for appointment %s failed (the change itself is unaffected)",
                              item["id"], item["appointment_id"])
                try:
                    _finish(item["id"], str(exc)[:300])
                except Exception:  # noqa: BLE001
                    log.exception("Could not record failure for notification %s", item["id"])
    except Exception:  # noqa: BLE001
        log.exception("Notification dispatch aborted (appointments are unaffected)")


def start_retry_worker(stop: threading.Event, interval: float = 15.0) -> threading.Thread:
    """Background thread: drains the outbox every `interval` seconds until `stop` is set."""
    def loop() -> None:
        while not stop.wait(interval):
            dispatch_pending()

    thread = threading.Thread(target=loop, name="outbox-retry", daemon=True)
    thread.start()
    return thread
