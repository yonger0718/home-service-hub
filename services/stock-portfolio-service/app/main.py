from shared_lib import create_app

from .database import engine, get_db
from .models import price_history  # noqa: F401  (registers table with Base.metadata)
from .routers import exdividend, history, imports, portfolio
from .services.twse_client import bootstrap_truststore


bootstrap_truststore()

app = create_app(
    title="Home Service Hub - Stock Portfolio API",
    description="投資組合管理微服務。",
    version="1.1.0",
    routers=[portfolio.router, exdividend.router, imports.router, history.router],
    get_db=get_db,
    engine=engine,
    otel_service_name_env="OTEL_SERVICE_NAME_STOCK",
    otel_strict=False,
)
