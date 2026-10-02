from sqlalchemy import Column, DateTime, Enum, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from ..database import Base

IMPORT_STATUSES = ("running", "succeeded", "failed")


class ImportRun(Base):
    __tablename__ = "import_run"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    file_name = Column(String(255), nullable=False)
    file_sha256 = Column(String(64), nullable=False)
    status = Column(Enum(*IMPORT_STATUSES, name="import_status"), nullable=False)
    row_count = Column(Integer, nullable=True)
    summary = Column(JSONB, nullable=True)
