import logging

from shared_lib import create_app
from starlette.middleware import Middleware

from .auth import ApiTokenMiddleware, api_auth_status, docs_paths
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

# Innermost user middleware (appended, not add_middleware's insert-at-0): CORS stays outside it, so preflights are
# answered by CORS and a 401 still carries CORS headers. api_auth_status() also fails startup on a malformed list;
# it goes to uvicorn's logger because that one is configured (INFO) before uvicorn imports this module.
app.user_middleware.append(Middleware(ApiTokenMiddleware, docs_paths=docs_paths(app)))
logging.getLogger("uvicorn.error").info(api_auth_status())
