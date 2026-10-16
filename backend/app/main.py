import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import notifications
from .database import init_db
from .routes import router
from .seed import seed_if_empty
from .services import ServiceError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()           # extension, tables, exclusion constraint, triggers (idempotent)
    seed_if_empty()     # demo data only on an empty database
    notifications.dispatch_pending()  # outbox recovery: send anything left undelivered by a previous run
    stop = threading.Event()
    notifications.start_retry_worker(stop)  # keeps retrying failed / interrupted notifications with backoff
    yield
    stop.set()


app = FastAPI(title="Patient Portal API", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError):
    body = {"message": exc.message, "code": exc.code}
    if exc.current is not None:  # on 409 the client gets the latest truth in the same response
        body["current"] = exc.current.model_dump(by_alias=True, mode="json")
    return JSONResponse(status_code=exc.status, content=body)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p != "body")
    return JSONResponse(status_code=422, content={"message": f"{field}: {first['msg']}", "code": "invalid"})


# Optional single-process mode: if the frontend has been built, serve it from here too.
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
