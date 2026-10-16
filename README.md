# Patient Portal

A minimal patient portal: patients view, request and cancel appointments; providers confirm and reschedule them.
Built to solve four specific problems correctly: stale updates, audit history, non-blocking notifications, and double-booking.

**Stack:** React + TypeScript (Vite) · Python FastAPI · SQLAlchemy 2.0 · PostgreSQL 16 · Pydantic · pytest

## Assignment coverage

The implementation covers the patient/provider baseline and all four concurrency and reliability scenarios in `jd.pdf`:

| Assignment requirement | Implementation | Representative coverage |
|---|---|---|
| Patient and provider workflows | Role switcher, appointment list, request form, confirm, reschedule, and cancel; server-side role checks | `backend/tests/test_api.py`: baseline and role tests |
| 1. Stale updates | Version token, row lock, and `409 stale` response with the current appointment; no stale write is saved | `test_stale_cancel_gets_409_and_does_not_overwrite`, `test_stale_confirm_gets_409` |
| 2. Audit history | Change events are recorded in the same transaction and protected against update/delete; appointment history and filtered global audit log | `test_history_answers_when_who_and_before`, `test_events_are_append_only`, audit-log tests |
| 3. Notification isolation | Transactional outbox and in-app inbox; delivery runs after the appointment transaction, so delivery failure does not undo confirmation | `test_notification_failure_does_not_undo_confirmation`, notification tests |
| 4. No overlapping confirmed bookings | PostgreSQL exclusion constraint on provider and half-open appointment ranges; service maps conflicts to `409` | Overlap, back-to-back, direct-SQL, and concurrent-race tests |
| Runnable local setup | Docker Compose PostgreSQL, database initialization, and root Makefile for setup, development, and tests | Commands below and `Makefile` |

The implementation and tests are organized across `frontend/src/`, `backend/app/`, and `backend/tests/`. The [AI session transcript](AI_TRANSCRIPT.md) summarizes the full-stack implementation scope and key decisions; it is a summary, not a verbatim chat export.

## Run it

Prerequisites: Python 3.10+, Node 18+, Docker (for PostgreSQL), `make`.

```bash
make setup     # one time: venv + pip + npm install
make db        # starts PostgreSQL in Docker (databases: portal, portal_test)
make dev       # API :8000 + UI :5173  ->  open http://localhost:5173
```

Single-process alternative: `make start` builds the UI and serves everything on http://localhost:8000.
Tests: `make test` (needs `make db` first). No `make` (Windows)? See "Manual commands" below.

**No Docker?** Use any PostgreSQL 13+ and point the app at it:
```bash
createdb portal && createdb portal_test        # role needs permission to CREATE EXTENSION btree_gist (the DB owner has it)
export DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/portal
export TEST_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/portal_test
```

Tables, the exclusion constraint and demo data are created automatically on first start. To reset: `docker compose down -v`.

## Demo identities (role switcher, top right)

| Id | Name | Role |
|---|---|---|
| p1 | Alex Rivera | patient |
| p2 | Sam Lee | patient |
| dr1 | Dr. Priya Shah | provider |
| dr2 | Dr. Marcus Chen | provider |

There is no real auth. The UI sends `X-User-Id`, and the server enforces permissions from it (only the assigned provider
confirms/reschedules; only the patient cancels, and only when confirmed).

Seed data includes a confirmed 15:00 UTC appointment tomorrow for `dr1` and a pending 15:15 request that overlaps it, so you can demo the conflict immediately.

## Demo script

1. **Stale update:** open the app in two browser windows. Window A: Alex (patient), don't touch it. Window B: Dr. Shah, reschedule Alex's confirmed appointment. Back in A, click Cancel without refreshing: you get a 409 message, nothing is saved, the card refreshes to the new time.
2. **Double-booking:** as Dr. Shah, confirm Sam's 15:15 request: 409. Reschedule it to start exactly when the other one ends: allowed (back-to-back).
3. **History:** "View history" on any card, or the **Audit log** tab, shows who/what/when with before and after.
4. **Notifications:** the bell shows an inbox for the acting user (each change notifies the *other* party).
5. **Notification isolation:** `NOTIFY_MODE=fail make dev` (or `slow`), then confirm something: confirm still succeeds instantly; the bell shows `sending…`, a background worker retries with growing delays (10 s, 30 s, 2 min, 10 min), and after 5 failed attempts it shows `could not be sent`.

## API

All under `/api`; mutations send the `version` the client last saw. JSON is camelCase. Interactive docs at http://localhost:8000/docs.

| Method | Path | Purpose |
|---|---|---|
| GET | `/users` | demo users |
| GET | `/appointments` | appointments for the acting user |
| POST | `/appointments` | request (starts `pending`) |
| POST | `/appointments/{id}/confirm` | provider confirms `{version}` |
| PATCH | `/appointments/{id}/reschedule` | provider moves `{version, startTime}` |
| POST | `/appointments/{id}/cancel` | patient cancels confirmed `{version}` |
| GET | `/appointments/{id}/history` | audit trail of one appointment |
| GET | `/audit-log?action=&appointmentId=` | audit trail across all my appointments |
| GET | `/notifications` | my inbox |
| POST | `/notifications/read`, `/notifications/{id}/read` | mark read (204) |

Errors are `{message, code, current?}`. `409` = `stale` (version changed) or `overlap`; `current` carries the latest appointment. `400` invalid input/transition, `403` wrong role, `404` not found/not yours, `401` no demo user, `422` validation, `503` retryable.

## The four problems: decision, why, what changes at scale

**1. Stale updates.** *Decision:* optimistic concurrency. Every appointment has a `version`; every mutation sends the version it saw. If it no longer matches, the server returns **409 `stale` and saves nothing**, along with the latest appointment. The UI says it changed, reloads, and the person decides again. *Why:* the patient did nothing wrong, but silently applying a cancel to a time they never saw would be worse than asking again; silently ignoring it is worse still. *Where:* the service layer (`apply_change`), inside the transaction: `SELECT ... FOR UPDATE` locks the row so a second request for the same appointment waits, then re-reads the committed row and sees the new version; SQLAlchemy's `version_id_col` (`UPDATE ... WHERE version = ?`) is a second guard. The UI only reacts. *Scale:* same pattern with ETags/`If-Match`.

**2. Audit history.** *Decision:* `appointments` holds current state; `appointment_events` is an append-only log written by the same function, in the same transaction, as every change (create, confirm, reschedule, cancel). Each event stores actor, action, timestamp and explicit `from_/to_` status and times (plus JSONB snapshots). A Postgres trigger rejects UPDATE/DELETE of events. *Why:* one mechanism (single function, single transaction), so state and history can't diverge; "booked Monday, now Wednesday" is a single query. Exposed per appointment and as a global, filterable audit log. *Scale:* partition/archive the table; stream events to a log store.

**3. Notifications.** *Decision:* a **transactional outbox** that doubles as the in-app inbox. In the same transaction as each change, `services.py` inserts a `notifications` row for the person on the other side (new request -> provider; confirmed / rescheduled -> patient; cancelled -> provider; nobody is notified of their own action). After the response is sent, a background task (`notifications.dispatch_pending`) claims rows **one at a time** (`FOR UPDATE SKIP LOCKED`, so concurrent workers never double-send), "sends" them **outside any transaction** (stub logs `Would send email to X`), and records `sent` / `failed`; every error is caught there. *Why good enough:* the change can't be slowed or failed by the notifier (tested, including a hung notifier not blocking other writes); a crash between commit and send doesn't lose anything because the row is already committed: a row left in `sending` by a dead process is **reclaimed after 60 s** (at-least-once), and failed rows are retried by a small worker thread (every 15 s, with backoff, max 5 attempts, then flagged `gave up` and logged as an error). Logs contain identifiers and subjects, never message bodies. *Real traffic:* run the worker as its own process, add per-user channel preferences and a real email/SMS provider (with an idempotency key), and push (SSE/websocket) instead of 5 s polling.

**4. No overlapping confirmed appointments.** *Decision:* enforced **by the database**: a PostgreSQL exclusion constraint

```sql
EXCLUDE USING gist (provider_id WITH =, tstzrange(start_time, end_time, '[)') WITH &&) WHERE (status = 'confirmed')
```

makes two overlapping confirmed appointments for one provider impossible, whatever code (or SQL console) touches the table. Ranges are half-open, so back-to-back is allowed; only `confirmed` rows count; a row never conflicts with itself. *Two simultaneous confirms:* Postgres serializes the conflicting writers: the second waits for the first transaction to finish, then fails with `23P01`, which the service maps to a clean **409 `overlap`**. As a safeguard, `apply_change` first locks the provider's row (`FOR NO KEY UPDATE`) so competing confirms/reschedules queue up in a fixed lock order; tests also run with that lock removed to show the constraint alone holds. Tested with 8 simultaneous confirms (service level and over real HTTP): exactly one 200, seven 409, zero 500s, repeated for dozens of rounds; and for two racing *reschedules* into the same slot. *Scale:* this already is the production-grade answer; add a retry on `503`.

## Other decisions

- **Stack:** FastAPI gives validated, documented endpoints and a clear backend boundary. PostgreSQL gives the exclusion constraint, row locks and `SKIP LOCKED`, which turn the two hardest requirements into declarative guarantees instead of application code. Endpoints are plain `def`, so they run in a thread pool and truly run concurrently.
- **Time:** stored as `timestamptz` (UTC); the browser converts for display. Duration defaults to 30 min (needed to detect overlap); rescheduling preserves duration and current status.
- **Business rules live in `services.py`**, not routes and not React. Disabled buttons are UX only.

## Known limitations / production changes

No real authentication or authorization model; no clinic timezones or working hours; schema is created with `create_all` + a bootstrap function (use Alembic migrations in production); delivery is at-least-once (a crash right after a real send could send twice, so a real provider call should carry an idempotency key); no pagination beyond a `limit` on the audit log and inbox; the bell polls but the appointment list does not auto-refresh (deliberate: a stale view is handled by the 409 check; add SSE for live updates).

## Manual commands

```bash
# database (or use your own Postgres, see above)
docker compose up -d --wait db
# backend
cd backend && python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt && uvicorn app.main:app --reload --port 8000
# frontend (second terminal)
cd frontend && npm install && npm run dev
# tests (needs the database)
cd backend && python -m pytest -q
```

## Layout

```
docker-compose.yml  docker/init.sql        PostgreSQL for local use
backend/
  app/ main.py  routes.py  services.py  models.py  database.py  schemas.py  notifications.py  seed.py
  tests/ conftest.py  test_api.py
  requirements.txt
frontend/src/
  api/          client.ts (fetch + ApiError)  appointments.ts  users.ts  audit.ts  notifications.ts  index.ts
  types/        index.ts
  hooks/        useUsers  useAppointments  useHistory  useAuditLog  useNotifications
  components/   RoleSwitcher  AppointmentList  AppointmentCard  AppointmentForm  RescheduleDialog
                AppointmentHistory  AuditLog  NotificationBell  ConflictAlert  StatusBadge
  pages/        PortalPage.tsx
  utils/        format.ts
  App.tsx  main.tsx  styles.css
```
