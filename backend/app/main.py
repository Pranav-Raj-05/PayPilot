import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import ai, core, payments, resources, webhooks
from app.core.config import get_settings
from app.core.db import get_engine, session_factory, upgrade_sqlite_dev_schema
from app.core.errors import register_exception_handlers
from app.core.logging import setup_logging
from app.models import Base
from app.services import automations

log = logging.getLogger("paypilot")


def _scheduler_tick() -> int:
    db = session_factory()()
    try:
        n = automations.run_due(db)
        db.commit()
        return n
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _scheduler(interval: int):
    while True:
        await asyncio.sleep(interval)
        try:
            ran = await asyncio.to_thread(_scheduler_tick)
            log.info("scheduler tick ran=%s", ran)
        except Exception:
            log.exception("scheduler tick failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(get_engine())  # dev convenience; production uses supabase/migrations
        for col in upgrade_sqlite_dev_schema(get_engine()):
            log.info("dev sqlite: added missing column %s", col)
    task = asyncio.create_task(_scheduler(settings.scheduler_interval_seconds)) if settings.scheduler_enabled else None
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    setup_logging()
    settings = get_settings()
    app = FastAPI(title="PayPilot API", version="1.0.0", description="AI-powered payment operations (PayPal primary, Razorpay secondary).",
                  lifespan=lifespan, docs_url="/docs", openapi_url="/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        rid, start = uuid.uuid4().hex[:12], time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        log.info("request", extra={"request_id": rid, "method": request.method, "path": request.url.path, "status": response.status_code,
                                    "duration_ms": round((time.perf_counter() - start) * 1000, 1)})
        return response

    register_exception_handlers(app)
    for r in (core.auth_router, core.dashboard_router, core.analytics_router, core.notifications_router, core.automations_router, core.settings_router,
              resources.customers_router, resources.invoices_router, resources.transactions_router, resources.links_router,
              payments.paypal_router, payments.razorpay_router, payments.public_router, webhooks.router, ai.router):
        app.include_router(r)

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok", "version": app.version}

    return app


app = create_app()
