from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ImportReport(BaseModel):
    id: int | None
    kind: Literal["moze_csv", "moze_backup"] = "moze_csv"
    status: Literal["running", "succeeded", "failed", "dry_run"]
    started_at: datetime
    finished_at: datetime | None
    file_name: str
    file_sha256: str
    row_count: int | None
    exported_at: datetime | None = None
    summary: dict | None
