import logging

from shared_lib import create_app

from .database import SessionLocal, engine, get_db
from .logging_config import configure_logging
from .models import corporate_action, portfolio_snapshot, price_history, symbol_map  # noqa: F401  (register tables with Base.metadata)
from .routers import dividends_backfill, exdividend, history, imports, portfolio, realized_pnl, symbol_map as symbol_map_router, upcoming_events
from .services import scheduler as scheduler_module
from .services.twse_client import bootstrap_truststore


configure_logging()
bootstrap_truststore()

logger = logging.getLogger(__name__)

app = create_app(
    title="Home Service Hub - Stock Portfolio API",
    description="投資組合管理微服務。",
    version="1.1.0",
    routers=[
        portfolio.router,
        realized_pnl.router,
        exdividend.router,
        imports.router,
        history.router,
        history.snapshot_router,
        history.corp_router,
        symbol_map_router.router,
        dividends_backfill.router,
        upcoming_events.router,
    ],
    get_db=get_db,
    engine=engine,
    otel_service_name_env="OTEL_SERVICE_NAME_STOCK",
    otel_strict=False,
)


_scheduler = None


@app.on_event("startup")
def _start_scheduler() -> None:
    global _scheduler
    if not scheduler_module.is_enabled():
        logger.info("scheduler.disabled")
        return
    try:
        _scheduler = scheduler_module.build_scheduler(SessionLocal)
        _scheduler.start()
    except Exception as exc:  # noqa: BLE001 — scheduler is optional; API must stay up
        _scheduler = None
        logger.exception("scheduler.bootstrap.failed", extra={"error": str(exc)})
        return
    job_ids = [job.id for job in _scheduler.get_jobs()]
    logger.info("scheduler.started", extra={"jobs": job_ids})


@app.on_event("shutdown")
def _stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("scheduler.stopped")
