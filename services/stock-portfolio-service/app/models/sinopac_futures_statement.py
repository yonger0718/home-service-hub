"""永豐期貨 statement: accepted day / month records and import-job run state."""

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Column, Date, DateTime, Integer, Numeric, String
from sqlalchemy.dialects import postgresql

from ..database import Base

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

LAST_STATUSES = ("imported", "duplicate", "conflict")

DAY_AMOUNTS = (
    "prev_balance", "deposits_net", "fees_and_tax", "futures_realized", "premium_net", "balance",
    "futures_unrealized", "equity", "total_equity", "initial_margin", "maintenance_margin",
    "securities_collateral", "excess_margin", "margin_call", "additional_margin", "withdrawable",
)
MONTH_AMOUNTS = (
    "a", "b", "g", "h", "k", "m", "n", "q", "p", "u", "v", "w", "securities_collateral",
    "additional_margin", "withdrawable", "long_premium_value", "short_premium_value", "total_equity",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _checks(table: str) -> tuple[CheckConstraint, CheckConstraint]:
    return (
        CheckConstraint(
            f"last_status IS NULL OR {_in('last_status', LAST_STATUSES)}",
            name=f"ck_{table}_last_status",
        ),
        CheckConstraint(
            f"ack_status IS NULL OR {_in('ack_status', LAST_STATUSES)}",
            name=f"ck_{table}_ack_status",
        ),
    )


class _StatementMixin:
    """Acceptance (write-once), latest attempt, findings and owner acknowledgement columns."""

    accepted_sha256 = Column(String(64), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=False)
    drive_file_id = Column(String(128), nullable=True)

    last_sha256 = Column(String(64), nullable=True)
    last_status = Column(String(16), nullable=True)
    last_detail = Column(_JSON, nullable=True)
    last_at = Column(DateTime(timezone=True), nullable=True)
    since = Column(DateTime(timezone=True), nullable=True)

    findings = Column(_JSON, nullable=True)
    findings_sha = Column(String(64), nullable=True)
    findings_since = Column(DateTime(timezone=True), nullable=True)

    ack_sha256 = Column(String(64), nullable=True)
    ack_status = Column(String(16), nullable=True)
    ack_at = Column(DateTime(timezone=True), nullable=True)
    ack_findings_sha = Column(String(64), nullable=True)


class FuturesStatementDay(_StatementMixin, Base):
    __tablename__ = "futures_statement_day"
    __table_args__ = _checks("futures_statement_day")

    statement_date = Column(Date, primary_key=True)
    deposits = Column(_JSON, nullable=True)
    fills = Column(_JSON, nullable=True)
    fills_total = Column(_JSON, nullable=True)
    positions = Column(_JSON, nullable=True)
    position_lines = Column(_JSON, nullable=True)
    open_positions = Column(Integer, nullable=True)


class FuturesStatementMonth(_StatementMixin, Base):
    __tablename__ = "futures_statement_month"
    __table_args__ = _checks("futures_statement_month")

    month = Column(Date, primary_key=True)  # first day of the month
    month_end = Column(Date, nullable=False)
    activity_dates = Column(_JSON, nullable=True)
    open_positions = Column(Integer, nullable=True)


for _name in DAY_AMOUNTS:
    setattr(FuturesStatementDay, _name, Column(_name, Numeric(18, 4), nullable=False))
for _name in MONTH_AMOUNTS:
    setattr(FuturesStatementMonth, _name, Column(_name, Numeric(18, 4), nullable=False))


class FuturesStatementRun(Base):
    """At most two rows, upserted by kind: the latest run and the latest ok run."""

    __tablename__ = "futures_statement_run"
    __table_args__ = (
        CheckConstraint("kind IN ('latest', 'ok')", name="ck_futures_statement_run_kind"),
        CheckConstraint("result IN ('ok', 'latched', 'failed')", name="ck_futures_statement_run_result"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(8), nullable=False, unique=True)
    run_at = Column(DateTime(timezone=True), nullable=False)
    result = Column(String(8), nullable=False)
    code = Column(String(64), nullable=True)
    files_seen = Column(Integer, nullable=False, default=0, server_default="0")
    posted = Column(Integer, nullable=False, default=0, server_default="0")
