"""國泰證券 daily statement job: list the Drive folder, unlock, parse and post each statement to the portfolio API."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import re
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from worker import inbox, parser, passwords, pdf
from worker.api import ApiClient
from worker.cathay_statement import StatementError, extract_text, parse_statement, to_payload
from worker.config import WorkerConfig
from worker.drive import Drive, DownloadError, ListingError, Listed

log = logging.getLogger("worker")

LATCH = "cathay-disabled.json"
LOCK = ".cathay.lock"
STATE = "cathay-state.json"
STAGE_PREFIX = "cathay-"
HISTORY = "cathay-history-report.json"
ENDPOINT = "/api/portfolio/imports/cathay-statement"
OUTCOMES = frozenset({"imported", "verified", "duplicate", "mismatch", "held", "conflict"})
TERMINAL = frozenset({"imported", "verified", "duplicate", "conflict", "mismatch", "pre_cutover"})


class CathayDisabled(RuntimeError):
    """A non-transient failure latched the daily run; `python -m worker cathay --unlatch` clears it."""


class AlreadyRunning(RuntimeError):
    """Another cathay run holds the cathay lock."""


class NothingPending(RuntimeError):
    """`--ack` for a date the portfolio has nothing pending for."""


class CathayError(RuntimeError):
    """A failure identified by a code only; it never carries statement content.

    `latch`: non-transient, latches a commit run. `stop`: no point trying the next file."""

    def __init__(self, code: str, *, latch: bool = False, stop: bool = False):
        super().__init__(code)
        self.code, self.latch, self.stop = code, latch, stop


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


def _send(cfg: WorkerConfig, http: httpx.Client, path: str, *, body: dict, params: dict | None = None) -> httpx.Response:
    failure = None
    try:
        response = http.post(path, params=params, json=body, timeout=cfg.cathay_timeout_s)
    except httpx.HTTPError as exc:
        failure = type(exc).__name__
    if failure:  # raised outside the except block: the httpx error (and its request URL) is not the context
        raise CathayError(f"portfolio:{failure}", stop=True)
    return response


def _post_statement(cfg: WorkerConfig, http: httpx.Client, payload: dict, dry_run: bool) -> dict:
    response = _send(cfg, http, ENDPOINT, body=payload, params={"dry_run": "true" if dry_run else "false"})
    status = response.status_code
    if status >= 400:
        transient = status >= 500 or status in (408, 429)  # 500 carries a body, but the attempt rolled back
        raise CathayError(f"portfolio:{status}", latch=not transient, stop=transient)
    try:
        body = response.json()
    except ValueError:
        raise CathayError("portfolio:body", stop=True) from None
    if (not isinstance(body, dict) or body.get("outcome") not in OUTCOMES
            or body.get("trade_date") != payload["trade_date"] or not isinstance(body.get("errors"), list)
            or (body["outcome"] in ("imported", "verified") and body["errors"])):
        raise CathayError("portfolio:body", stop=True)
    return body


def _report(cfg: WorkerConfig, http: httpx.Client, summary: dict) -> None:
    codes = summary["codes"]
    body = {"run_at": datetime.now(timezone.utc).isoformat(), "result": codes[0] if codes else "ok",
            "code": codes[1][:64] if codes else None, "files_seen": summary["files_seen"], "posted": summary["posted"]}
    try:
        http.post(f"{ENDPOINT}/runs", json=body, timeout=cfg.cathay_timeout_s).raise_for_status()
    except httpx.HTTPError as exc:  # the report is best effort: it must not change the run's result
        log.info("cathay: run report not posted (%s)", type(exc).__name__)


def _settlement_run(cfg: WorkerConfig, homehub: ApiClient) -> str:
    """Spec §4.5: ask HomeHub to run the settlement stand-in job and flag the touched statements for re-check. The
    result is a status or `failed:<ExceptionClass>` for the summary only: it never changes the run's result."""
    try:
        data = homehub._call("POST", "/reconciliation/settlements/run", json={"source": "portfolio:cathay"},
                             timeout=cfg.cathay_timeout_s)
        status = data.get("status") if isinstance(data, dict) else None
        return status[:32] if isinstance(status, str) and status else "failed:body"
    except Exception as exc:  # noqa: BLE001 — a class name only; the portfolio import already succeeded
        log.info("cathay: settlement run not done (%s)", type(exc).__name__)
        return f"failed:{type(exc).__name__}"[:64]


def _reason_code(reason: object) -> str:
    """The portfolio sends `code:name`; the history report keeps the code only, never a product name."""
    code = reason.split(":", 1)[0] if isinstance(reason, str) else ""
    return code if re.fullmatch(r"[a-z_]{1,40}", code) else ""


def _latch_reason(latch: Path) -> str:
    try:
        reason = json.loads(latch.read_text(encoding="utf-8")).get("reason")
    except (OSError, ValueError, AttributeError):
        reason = None
    return reason[:64] if isinstance(reason, str) and reason else "latched"


def _ack(cfg: WorkerConfig, http: httpx.Client, day: date) -> dict:
    failure = None
    try:
        response = http.post(f"{ENDPOINT}/days/{day.isoformat()}/ack", timeout=cfg.cathay_timeout_s)
    except httpx.HTTPError as exc:
        failure = type(exc).__name__
    if failure:
        return {"codes": ["failed", f"portfolio:{failure}"]}
    if response.status_code == 404:
        raise NothingPending(day.isoformat())
    if response.status_code >= 400:
        return {"codes": ["failed", f"portfolio:{response.status_code}"]}
    try:
        return {"ack": response.json(), "codes": []}
    except ValueError:
        return {"codes": ["failed", "portfolio:body"]}


class _Job:
    def __init__(self, cfg, http, drive, *, dry_run, file_id, since, until, retry_date, validate_history):
        self.cfg, self.http, self.drive = cfg, http, drive
        self.validate = validate_history
        self.commit = not (dry_run or validate_history)
        self.file_id, self.since, self.until, self.retry_date = file_id, since, until, retry_date
        self.state_path = cfg.state_dir / STATE
        self.state: dict[str, dict] = {}
        self.codes: list[str] = []
        self.counts: dict[str, int] = {}
        self.files_seen = self.posted = self.skipped = 0
        self.history: dict = {"counts": self.counts, "mismatch_dates": [], "held": [], "errors": []}
        self._candidates: list[str] | None = None
        self._staged = 0

    def summary(self) -> dict:
        return {"dry_run": not self.commit, "validate_history": self.validate, "files_seen": self.files_seen,
                "posted": self.posted, "skipped": self.skipped, "counts": dict(self.counts), "codes": self.codes}

    def fail(self, exc: CathayError, item: Listed | None = None) -> None:
        log.info("cathay: %s", exc.code)
        if exc.latch and self.commit:
            parser.write_private_json(self.cfg.state_dir, self.cfg.state_dir / LATCH,
                                      {"reason": exc.code, "at": datetime.now(timezone.utc).isoformat()})
            self.codes = ["latched", exc.code]
        elif not self.codes:
            self.codes = ["failed", exc.code]
        if self.validate and item is not None:
            self.history["errors"].append({"file_name": item.name, "code": exc.code})

    def candidates(self) -> list[str]:
        if self._candidates is None:
            try:
                self._candidates = passwords.load(self.cfg.password_file)[0].get(self.cfg.cathay_folder, [])
            except (OSError, ValueError):
                raise CathayError("password_file", stop=True) from None
        return self._candidates

    def load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CathayError("state_file", stop=True) from None
        if not isinstance(data, dict):
            raise CathayError("state_file", stop=True)
        self.state = data

    def remember(self, item: Listed, sha: str, outcome: str, trade_date: date) -> None:
        self.state[f"{item.drive_file_id}:{sha}"] = {"outcome": outcome, "trade_date": trade_date.isoformat(),
                                                      "md5": item.md5, "at": datetime.now(timezone.utc).isoformat()}
        parser.write_private_json(self.cfg.state_dir, self.state_path, self.state)

    def known(self, item: Listed) -> bool:
        """A listed file already settled for this Drive id and md5 (the name decides nothing)."""
        if self.validate:
            return False
        for key, entry in self.state.items():
            if (key.rpartition(":")[0] == item.drive_file_id and isinstance(entry, dict) and entry.get("md5") == item.md5
                    and entry.get("outcome") in TERMINAL
                    and not (self.retry_date and entry.get("trade_date") == self.retry_date.isoformat())):
                return True
        return False

    def fetch(self, item: Listed) -> tuple[str, bytes]:
        """Download into staging, check the listing's md5 and size, return (sha256, bytes); staging never keeps the file."""
        self._staged += 1
        staged = self.cfg.staging_dir / f"{STAGE_PREFIX}{os.getpid()}-{self._staged}.pdf"
        try:
            self.drive.download(item, staged)
            data = staged.read_bytes()
            if hashlib.md5(data).hexdigest() != item.md5.lower() or len(data) != item.size:
                raise CathayError("drive:VerifyError")
            return hashlib.sha256(data).hexdigest(), data
        except (DownloadError, OSError) as exc:
            raise CathayError(f"drive:{type(exc).__name__}") from None
        finally:
            staged.unlink(missing_ok=True)

    def sweep_staging(self) -> None:
        """Remove this job's own files left behind by a crashed run (the pipeline's `<id>.tmp` files are not ours)."""
        if self.cfg.staging_dir.is_dir():
            for stale in self.cfg.staging_dir.glob(f"{STAGE_PREFIX}*.pdf"):
                stale.unlink(missing_ok=True)

    def read(self, data: bytes) -> str:
        candidates = self.candidates()
        if not candidates:
            raise CathayError("decrypt", latch=True)
        try:
            unlocked = pdf.unlock(data, candidates)
        except pdf.PasswordError:
            raise CathayError("decrypt", latch=True) from None
        except pdf.Malformed:
            raise CathayError("pdf_malformed", latch=True) from None
        except pdf.TooLarge:
            raise CathayError("pdf_too_large", latch=True) from None
        try:
            index = unlocked.password_index
            return extract_text(data, candidates[index] if index is not None else None)
        except Exception:  # noqa: BLE001 — the library's text may hold statement content: code only
            raise CathayError("extract", latch=True) from None

    def process(self, item: Listed) -> None:
        sha, data = self.fetch(item)
        text = self.read(data)
        try:
            statement = parse_statement(text)
            payload = to_payload(statement, drive_file_id=item.drive_file_id, sha256=sha, file_name=item.name)
        except StatementError as exc:
            raise CathayError(exc.code, latch=True) from None
        trade = statement.trade_date
        if (self.since and trade < self.since) or (self.until and trade > self.until):
            self.skipped += 1
            return
        if trade < self.cfg.cathay_cutover and not self.validate:
            self.counts["pre_cutover"] = self.counts.get("pre_cutover", 0) + 1
            if self.commit:
                self.remember(item, sha, "pre_cutover", trade)
            return
        self.posted += 1
        body = _post_statement(self.cfg, self.http, payload, dry_run=not self.commit)
        outcome = body["outcome"]
        self.counts[outcome] = self.counts.get(outcome, 0) + 1
        if self.validate and outcome == "mismatch":
            self.history["mismatch_dates"].append(trade.isoformat())
        if self.validate and outcome == "held":
            self.history["held"].append({"trade_date": trade.isoformat(), "reason": _reason_code(body.get("reason"))})
        if self.commit and outcome in TERMINAL:  # held is never written: it is posted again on every run
            self.remember(item, sha, outcome, trade)
        elif self.commit and self.state.pop(f"{item.drive_file_id}:{sha}", None) is not None:
            # a --retry re-post that came back held: drop the old terminal entry so the daily runs keep posting it
            parser.write_private_json(self.cfg.state_dir, self.state_path, self.state)

    def run(self) -> None:
        if not self.cfg.password_file.is_file():  # checked here, not in the CLI, so the daily run reports it
            raise CathayError("password_file", stop=True)
        self.sweep_staging()
        self.load_state()
        items = sorted(self.drive._list_root("cathay", f"{self.cfg.drive_root}/{self.cfg.cathay_folder}"), key=lambda i: i.path)
        if self.file_id:
            items = [i for i in items if i.drive_file_id == self.file_id]
            if not items:
                raise CathayError("file_not_found")
        self.files_seen = len(items)
        for item in items:
            if self.known(item):
                self.skipped += 1
                continue
            try:
                self.process(item)
            except CathayError as exc:
                self.fail(exc, item)
                if exc.stop or (exc.latch and self.commit):
                    break


def run(cfg: WorkerConfig, *, portfolio_http: httpx.Client, drive: Drive, dry_run: bool = False,
        file_id: str | None = None, since: date | None = None, until: date | None = None,
        retry_date: date | None = None, ack_date: date | None = None, unlatch: bool = False,
        validate_history: bool = False, homehub: ApiClient | None = None) -> dict:
    """List, read and post the statements; failures become codes, never exceptions (a dry run never touches state)."""
    latch = cfg.state_dir / LATCH
    with _lock(cfg):
        if ack_date:  # touches neither Drive nor state: allowed while latched
            return _ack(cfg, portfolio_http, ack_date)
        # only a plain daily commit run reports: the notifier reads the report as "the daily run's result"
        plain = not (dry_run or validate_history or file_id or since or until or retry_date or unlatch)
        if latch.exists() and not unlatch:
            if plain:
                _report(cfg, portfolio_http, {"codes": ["latched", _latch_reason(latch)], "files_seen": 0, "posted": 0})
            raise CathayDisabled("cathay disabled until `python -m worker cathay --unlatch` succeeds")
        job = _Job(cfg, portfolio_http, drive, dry_run=dry_run, file_id=file_id, since=since, until=until,
                   retry_date=retry_date, validate_history=validate_history)
        try:
            job.run()
        except CathayError as exc:
            job.fail(exc)
        except ListingError:
            job.fail(CathayError("drive_listing"))
        except Exception as exc:  # noqa: BLE001 — the run report still goes out; the class name is the only detail
            job.fail(CathayError(f"internal:{type(exc).__name__}"[:64]))
        if validate_history:
            parser.write_private_json(cfg.state_dir, cfg.state_dir / HISTORY, job.history)
        summary = job.summary()
        if plain:
            _report(cfg, portfolio_http, summary)
            if job.commit:
                summary["settlement_run"] = _settlement_run(cfg, homehub) if homehub is not None else "skipped:no_token"
        if job.commit:
            if unlatch and not (file_id or since or until or retry_date) and not summary["codes"]:
                latch.unlink(missing_ok=True)  # only a clean plain daily commit run clears it
        return summary
