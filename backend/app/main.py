from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

# Optional imports — backend can run in degraded mode without these
try:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    _sentry_available = True
except ImportError:
    sentry_sdk = None  # type: ignore
    _sentry_available = False

try:
    from redis import Redis
    _redis_available = True
except ImportError:
    Redis = None  # type: ignore
    _redis_available = False

try:
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    _slowapi_available = True
except ImportError:
    RateLimitExceeded = Exception  # type: ignore
    SlowAPIMiddleware = None  # type: ignore
    _slowapi_available = False

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from sqlalchemy import text

from app.config import get_settings, is_secure_admin_token
from app.database import AsyncSessionLocal
from app.exceptions import AppError
from app.logging import configure_logging, get_logger
from app.rate_limit import RATE_LIMIT_MESSAGE, limiter
from app.routers import admin, analysis, dashboard, feedback, health, matches, predictions, signals, stats
from app.version import VERSION

configure_logging()
settings = get_settings()
logger = get_logger(__name__)

if _sentry_available and settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        integrations=[FastApiIntegration(), CeleryIntegration()],
        traces_sample_rate=0.05,
        environment=settings.environment,
    )


async def _wait_for_database() -> None:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
            logger.info("Database connection ready on startup (attempt %s)", attempt)
            return
        except Exception as exc:
            last_error = exc
            logger.warning("Database startup check failed on attempt %s/3: %s", attempt, exc)
            if attempt < 3:
                await asyncio.sleep(5)
    raise RuntimeError(f"Database unavailable after 3 startup attempts: {last_error}") from last_error


async def _check_redis_startup() -> None:
    if not _redis_available:
        logger.info("Redis package not installed; continuing in degraded mode")
        return
    try:
        redis = Redis.from_url(settings.redis_url)
        await asyncio.to_thread(redis.ping)
        logger.info("Redis connection ready on startup")
    except Exception as exc:
        logger.warning("Redis unavailable on startup; continuing in degraded mode: %s", exc)


def _enforce_startup_security() -> None:
    """Fail closed for unsafe runtime secrets before serving requests."""
    if not is_secure_admin_token(settings.admin_token):
        raise RuntimeError(
            "Refusing to start with a weak or default ADMIN_TOKEN. "
            "Set a generated secret of at least 32 characters in backend/.env or .env."
        )
    if "*" in settings.cors_origins:
        raise RuntimeError("Refusing wildcard CORS while credentialed requests are enabled.")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _enforce_startup_security()
    await _wait_for_database()
    await _check_redis_startup()
    yield


app = FastAPI(title=settings.app_name, version=VERSION, lifespan=lifespan)
app.state.limiter = limiter
if _slowapi_available and SlowAPIMiddleware is not None:
    app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


if _slowapi_available:
    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(_: Request, __: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": RATE_LIMIT_MESSAGE})


@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info("%s %s", request.method, request.url.path)
    response = await call_next(request)
    logger.info("%s %s -> %s", request.method, request.url.path, response.status_code)
    return response


app.include_router(health.router)
app.include_router(matches.router, prefix=settings.api_prefix)
app.include_router(predictions.router, prefix=settings.api_prefix)
app.include_router(signals.router, prefix=settings.api_prefix)
app.include_router(feedback.router, prefix=settings.api_prefix)
app.include_router(stats.router, prefix=settings.api_prefix)
app.include_router(admin.router, prefix=settings.api_prefix)
app.include_router(analysis.router, prefix=settings.api_prefix)
app.include_router(dashboard.router)

# Dashboard static files
_static_dir = Path(__file__).resolve().parent.parent / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/dashboard")
async def serve_dashboard():
    dashboard_html = _static_dir / "dashboard.html"
    return FileResponse(str(dashboard_html))
