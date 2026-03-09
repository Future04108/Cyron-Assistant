"""FastAPI backend application entry point."""

import asyncio
import logging
import sys
from pathlib import Path
from contextlib import asynccontextmanager

# Add project root to Python path
project_root = Path(__file__).parent.parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.config import config
from backend.api import health, relay, knowledge, usage, guilds, auth
from backend.db.session import async_session_factory, engine, init_db
from backend.services.reset_service import run_daily_reset, run_monthly_reset

# Structlog configuration
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)
logging.basicConfig(level=getattr(logging, config.log_level))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: DB, Redis, migrations, scheduler."""
    from alembic.config import Config
    from alembic import command

    log = structlog.get_logger()
    log.info("phase", msg="Starting AI Ticket Assistant Backend")

    # Redis
    redis = Redis.from_url(config.redis_url, decode_responses=True)
    try:
        await redis.ping()
    except Exception as e:
        log.error("redis_connect_failed", error=str(e))
        raise
    app.state.redis = redis
    log.info("redis_connected")

    # Ensure database schema exists (Phase 2: use SQLAlchemy metadata)
    # Alembic migrations can still be run manually if needed.
    try:
        await init_db()
        log.info("db_initialized")
    except Exception as e:
        log.error("db_init_failed", error=str(e))
        raise

    # Scheduler for daily/monthly resets
    scheduler = AsyncIOScheduler()
    async def daily_job() -> None:
        async with async_session_factory() as session:
            await run_daily_reset(session, redis)

    async def monthly_job() -> None:
        async with async_session_factory() as session:
            await run_monthly_reset(session, redis)
    scheduler.add_job(
        daily_job,
        CronTrigger(hour=0, minute=0, timezone="UTC"),
        id="daily_reset",
    )
    scheduler.add_job(
        monthly_job,
        CronTrigger(day=1, hour=0, minute=0, timezone="UTC"),
        id="monthly_reset",
    )
    scheduler.start()
    app.state.scheduler = scheduler
    log.info("scheduler_started")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    await redis.aclose()
    await engine.dispose()
    log.info("phase", msg="Shutdown complete")


app = FastAPI(
    title="AI Ticket Assistant Backend",
    description="Backend API for AI-powered Discord ticket support bot",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(guilds.router)
app.include_router(knowledge.router)
app.include_router(usage.router)
app.include_router(relay.router)


@app.get("/")
async def root():
    return {"service": "AI Ticket Assistant Backend", "version": "0.2.0", "status": "running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=config.host,
        port=config.port,
        reload=True,
        log_level=config.log_level.lower(),
    )

