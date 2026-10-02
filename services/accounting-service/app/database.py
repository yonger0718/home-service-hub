import os
from sqlalchemy.orm import sessionmaker

from shared_lib.database import Base, TimestampMixin, get_database_url, create_db_engine, get_db as _get_db

# 載入環境變數並建立連線
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.env"))

SQLALCHEMY_DATABASE_URL = get_database_url(
    db_env_var="ACCOUNTING_DB",
    required_vars=["POSTGRES_USER", "POSTGRES_PASSWORD", "DB_HOST", "POSTGRES_PORT", "ACCOUNTING_DB"],
    env_path=env_path,
)

engine = create_db_engine(SQLALCHEMY_DATABASE_URL, pool_config={
    "pool_recycle": 1800,
    "pool_size": 10,
    "max_overflow": 20,
    "pool_timeout": 30,
    "pool_use_lifo": True,
    # Error messages (stored in import_run.summary, logged, shown in tracebacks) never carry bound parameters,
    # which for a bulk flush are owner rows.
    "hide_parameters": True,
    "connect_args": {
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    },
})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    yield from _get_db(SessionLocal)


def get_engine():
    return engine
