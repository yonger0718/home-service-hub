from shared_lib import create_app

from .database import engine, get_db
from .routers import accounts, balance_adjustments, entries, imports, settings, splits, transfers

app = create_app(
    title="Home Service Hub - Accounting API",
    description="記帳與財務管理微服務。",
    version="2.0.0",
    routers=[
        accounts.router, entries.router, imports.router, settings.router,
        transfers.router, splits.router, balance_adjustments.router,
    ],
    get_db=get_db,
    engine=engine,
    otel_service_name_env="OTEL_SERVICE_NAME_ACCOUNTING",
    otel_strict=True,
)
