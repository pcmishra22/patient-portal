import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import Base

DEFAULT_URL = "postgresql+psycopg://portal:portal@localhost:5432/portal"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


@lru_cache(maxsize=None)
def _engine(url: str) -> Engine:
    # Store/return everything in UTC regardless of the server's timezone setting.
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20,
                         connect_args={"options": "-c timezone=UTC"})


def engine() -> Engine:
    return _engine(database_url())


def session() -> Session:
    """New session. expire_on_commit=False so returned objects stay readable after the transaction ends."""
    return Session(engine(), expire_on_commit=False)


EVENTS_IMMUTABLE = """
CREATE OR REPLACE FUNCTION forbid_event_changes() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'events_are_append_only' USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_immutable ON appointment_events;
CREATE TRIGGER events_immutable BEFORE UPDATE OR DELETE ON appointment_events
  FOR EACH ROW EXECUTE FUNCTION forbid_event_changes();
"""


def init_db() -> None:
    """Idempotent. (For a production app this would be Alembic migrations.)"""
    eng = engine()
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS btree_gist")  # needed for '=' on text inside GiST
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql(EVENTS_IMMUTABLE)
