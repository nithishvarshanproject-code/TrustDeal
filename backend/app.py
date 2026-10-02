"""FastAPI app factory."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend import models  # noqa: F401  (registers tables on Base)
from backend.agent.followups import run_followups, send_scheduled_summary_if_due
from backend.database import Base, SessionLocal, engine, ensure_schema, get_db
from backend.routes import (customer, deals, demo, health, ledger, omega, policy, products, seller_agent, seller_ask,
                            sellers, stats, verify)
from backend.security import ALLOWED_HOSTS, ALLOWED_ORIGINS, BodySizeLimit, SecurityHeaders
from backend.telegram import runtime as telegram_runtime
from engine import log_redaction
from engine.omega_link import OmegaError, OmegaUnavailable

logger = logging.getLogger("uvicorn.error")


def _run_agent_checks(app: FastAPI) -> None:
    """Use the app's DB override in tests, otherwise the normal session factory."""
    override = app.dependency_overrides.get(get_db)
    if override is None:
        with SessionLocal() as db:
            run_followups(db)
            send_scheduled_summary_if_due(db)
        return
    resource = override()
    if hasattr(resource, "__next__"):
        db = next(resource)
        try:
            run_followups(db)
            send_scheduled_summary_if_due(db)
        finally:
            resource.close()
        return
    db = resource
    try:
        run_followups(db)
        send_scheduled_summary_if_due(db)
    finally:
        db.close()


async def _agent_worker(app: FastAPI) -> None:
    """Periodic checks stay in this process and reuse the current application's database."""
    while True:
        await asyncio.sleep(60)
        try:
            _run_agent_checks(app)
        except Exception:
            logger.exception("Agent follow-up check failed; it will retry on the next interval")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run Telegram and the agent's durable follow-up checks in this backend process."""
    telegram_runtime.start()
    worker = asyncio.create_task(_agent_worker(app), name="agent-followups")
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        telegram_runtime.stop()


def create_app() -> FastAPI:
    log_redaction.install()   # mask keys/tokens in every log line of this process
    Base.metadata.create_all(bind=engine)
    ensure_schema(engine)
    app = FastAPI(title="TrustDeal", summary="The BASIX Deal Agent on Omega", lifespan=lifespan)
    # Middleware added last runs first: headers -> host check -> body cap -> CORS -> routes.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.add_middleware(BodySizeLimit)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.add_middleware(SecurityHeaders)
    app.include_router(health.router)
    app.include_router(deals.router)
    app.include_router(policy.router)
    app.include_router(sellers.router)
    app.include_router(products.router)
    app.include_router(omega.router)
    app.include_router(demo.router)
    app.include_router(customer.router)
    app.include_router(seller_agent.router)
    app.include_router(seller_ask.router)
    app.include_router(stats.router)
    app.include_router(verify.router)
    app.include_router(ledger.router)

    # Clear 422s that never echo the submitted value back (it may be huge, secret or NaN,
    # which is not even valid JSON): only where and what, e.g. "quantity: less than or equal to 100000".
    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = [{"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": str(e.get("msg", ""))}
                  for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    # ENGINE_RUNNER=omega never falls back to local: Omega problems become clear errors.
    @app.exception_handler(OmegaUnavailable)
    async def omega_unavailable(_: Request, exc: OmegaUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(OmegaError)
    async def omega_error(_: Request, exc: OmegaError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    return app
