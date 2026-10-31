"""Cathay daily statement: per-date ownership record and import-job run state."""

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Column, Date, DateTime, Integer, Numeric, String
from sqlalchemy.dialects import postgresql

from ..database import Base

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

# Spec 5.2
ACCEPTED_KINDS = ("imported", "verified")
LAST_STATUSES = ("imported", "verified", "duplicate", "mismatch", "held", "conflict")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class CathayStatementDay(Base):
    __tablename__ = "cathay_statement_day"
    __table_args__ = (
        CheckConstraint(
            f"accepted_kind IS NULL OR {_in('accepted_kind', ACCEPTED_KINDS)}",
            name="ck_cathay_statement_day_accepted_kind",
        ),
        CheckConstraint(
            f"last_status IS NULL OR {_in('last_status', LAST_STATUSES)}",
            name="ck_cathay_statement_day_last_status",
        ),
    )

    trade_date = Column(Date, primary_key=True)

    # Acceptance: written once, never changed by a later post.
    accepted_kind = Column(String(16), nullable=True)
    accepted_sha256 = Column(String(64), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    canonical_lines = Column(_JSON, nullable=True)

    # Latest attempt: overwritten by each post.
    last_sha256 = Column(String(64), nullable=True)
    last_status = Column(String(16), nullable=True)
    last_reason = Column(String(80), nullable=True)
    last_detail = Column(_JSON, nullable=True)
    last_at = Column(DateTime(timezone=True), nullable=True)
    since = Column(DateTime(timezone=True), nullable=True)  # first attempt in the current status

    # Settlement facts of the accepted file (the latest post for unaccepted days); NULL before e9settle.
    settlement_date = Column(Date, nullable=True)
    net_amount = Column(Numeric(18, 4), nullable=True)  # 應收付, points lines included
    cash_net = Column(Numeric(18, 4), nullable=True)  # 淨收付, points lines excluded; NULL for an unsupported day

    # Owner acknowledgement (spec 7.1).
    ack_sha256 = Column(String(64), nullable=True)
    ack_status = Column(String(16), nullable=True)
    ack_at = Column(DateTime(timezone=True), nullable=True)

    drive_file_id = Column(String(128), nullable=True)


class CathayStatementRun(Base):
    """At most two rows, upserted by kind: the latest run and the latest ok run."""

    __tablename__ = "cathay_statement_run"
    __table_args__ = (
        CheckConstraint("kind IN ('latest', 'ok')", name="ck_cathay_statement_run_kind"),
        CheckConstraint("result IN ('ok', 'latched', 'failed')", name="ck_cathay_statement_run_result"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(8), nullable=False, unique=True)
    run_at = Column(DateTime(timezone=True), nullable=False)
    result = Column(String(8), nullable=False)
    code = Column(String(64), nullable=True)
    files_seen = Column(Integer, nullable=False, default=0, server_default="0")
    posted = Column(Integer, nullable=False, default=0, server_default="0")
