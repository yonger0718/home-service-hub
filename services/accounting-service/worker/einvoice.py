"""財政部 e-invoice job: list the Drive CSV folder, parse every file, merge, and post ONE import (or a match).

Privacy: only Drive ids, counts and codes reach the summary, the log or an exception message. Drive names and paths
are used in memory for ordering and never written; API and Drive error text is never read (class names and statuses
only); staging files are named by pid and counter and removed in `finally` (and swept at the start)."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import logging
import os
from collections.abc import Iterator

import httpx

from worker import inbox
from worker.api import ApiClient, ApiError
from worker.config import WorkerConfig
from worker.drive import Drive, DownloadError, ListingError, Listed
from worker.einvoice_csv import ParseError, ParsedInvoice, parse, period_of, to_payload

log = logging.getLogger("worker")

LOCK = ".einvoice.lock"
STAGE_PREFIX = "einvoice-"
MAX_CSV_BYTES = 5_000_000


class AlreadyRunning(RuntimeError):
    """Another einvoice run holds the lock."""


class VerifyError(RuntimeError):
    """The downloaded bytes differ from the listing's md5 or size."""


@contextlib.contextmanager
def _lock(cfg: WorkerConfig) -> Iterator[None]:
    inbox.private_dir(cfg.state_dir)
    fd = os.open(cfg.state_dir / LOCK, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning(LOCK) from None
        yield


def _fetch(cfg: WorkerConfig, drive: Drive, item: Listed, n: int) -> bytes:
    staged = cfg.staging_dir / f"{STAGE_PREFIX}{os.getpid()}-{n}.csv"
    try:
        drive.download(item, staged, max_bytes=MAX_CSV_BYTES)
        data = staged.read_bytes()
        if hashlib.md5(data).hexdigest() != item.md5.lower() or len(data) != item.size:
            raise VerifyError("md5 or size")
        return data
    finally:
        staged.unlink(missing_ok=True)


def run(cfg: WorkerConfig, *, drive: Drive, api: ApiClient) -> dict:
    summary: dict = {"files_seen": 0, "files_parsed": 0, "parse_failed": 0, "invoices": 0, "import": None,
                     "match": None, "failures": [], "codes": []}
    with _lock(cfg):
        inbox.private_dir(cfg.staging_dir)
        for stale in cfg.staging_dir.glob(f"{STAGE_PREFIX}*.csv"):
            stale.unlink(missing_ok=True)
        try:
            _run(cfg, drive, api, summary)
        except ListingError:
            summary["codes"].append("drive_listing")
        except ApiError as exc:
            summary["codes"].append(f"api:{exc.status}")
        except httpx.HTTPError as exc:
            summary["codes"].append(f"api:{type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001 — a class name only
            summary["codes"].append(f"internal:{type(exc).__name__}")
    for f in summary["failures"]:
        log.warning("einvoice: file %s %s", f["file"], f["code"])
    log.info("einvoice: files=%d parsed=%d failed=%d invoices=%d", summary["files_seen"], summary["files_parsed"],
             len(summary["failures"]), summary["invoices"])
    return summary


def _run(cfg: WorkerConfig, drive: Drive, api: ApiClient, summary: dict) -> None:
    items = drive._list_root("einvoice", f"{cfg.drive_root}/{cfg.einvoice_folder}", extensions=(".csv",))
    items.sort(key=lambda i: i.path)
    summary["files_seen"] = len(items)
    merged: dict[tuple, dict] = {}
    for n, item in enumerate(items, 1):
        try:
            invoices: list[ParsedInvoice] = parse(_fetch(cfg, drive, item, n))
        except ParseError as exc:
            summary["parse_failed"] += 1
            summary["failures"].append({"file": item.drive_file_id, "code": f"parse:{exc.code}"})
            continue
        except (DownloadError, VerifyError, OSError) as exc:
            summary["failures"].append({"file": item.drive_file_id, "code": f"drive:{type(exc).__name__}"})
            continue
        summary["files_parsed"] += 1
        for inv in invoices:  # later file wins; keyed by (number, period), the API's identity
            merged[(inv.number, period_of(inv.issue_date))] = to_payload(inv, source_drive_id=item.drive_file_id)
    summary["invoices"] = len(merged)
    timeout = cfg.einvoice_timeout_s
    if merged:
        summary["import"] = api.import_einvoices(list(merged.values()), timeout=timeout)
    else:
        summary["match"] = api.match_einvoices(timeout=timeout)
