"""永豐期貨 daily / monthly statement job (spec 2026-11-25-sinopac-futures-statement §7): list both Drive folders,
unlock, parse every new statement, then post them to the portfolio API, dailies by statement date before monthlies
by month.

Shaped like the 國泰 job (`worker/cathay.py`): state, latch and lock files in `state_dir`, failures as codes only
(never statement content), a run report from a plain commit run only. Differences: every file is read and parsed
before anything is posted, so a statement that fails the grammar (or cannot be decrypted) latches and posts nothing,
and the posts never follow the Drive listing's order; a file Drive fails to deliver is dropped from the run like
國泰 does (no state entry, the next run retries it) while the rest still posts: the portfolio re-checks the day, the
next accepted day and the month when a late daily arrives. Every portfolio outcome is terminal for its sha.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from worker import inbox, parser, passwords, pdf
from worker.config import WorkerConfig
from worker.drive import Drive, DownloadError, ListingError, Listed
from worker.pdf import extract_lines
from worker.sinopac_futures_statement import (DailyStatement, StatementError, parse_daily, parse_monthly,
                                              to_payload)

log = logging.getLogger("worker")

LATCH = "sinopac-futures-disabled.json"
LOCK = ".sinopac-futures.lock"
STATE = "sinopac-futures-state.json"
STAGE_PREFIX = "sinopac-futures-"
HISTORY = "sinopac-futures-history-report.json"
ENDPOINT = "/api/portfolio/imports/sinopac-futures-statement"
OUTCOMES = frozenset({"imported", "duplicate", "conflict"})  # every one is terminal for its sha (§6.2)
ACK_STATUSES = ("conflict", "findings")
VALIDATE_MAX = 200  # the portfolio's `/validate` cap (FuturesValidateBody.payloads max_length)

_KEY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}(-[0-9]{2})?$")
_CODE_RE = re.compile(r"^[a-z_]{1,40}$")
_LETTER_RE = re.compile(r"^[A-Za-z]{1,8}$")
_FILE_DATE_RE = re.compile(r"^([0-9]{4}-[0-9]{2}(?:-[0-9]{2})?)_")


class FuturesDisabled(RuntimeError):
    """A non-transient failure latched the daily run; `python -m worker sinopac-futures --unlatch` clears it."""


class AlreadyRunning(RuntimeError):
    """Another sinopac-futures run holds its lock."""


class NothingPending(RuntimeError):
    """`--ack` for a key and status the portfolio has nothing pending for."""


class FuturesError(RuntimeError):
    """A failure identified by a code only; it never carries statement content.

    `latch`: non-transient, latches a commit run. `stop`: no point trying the next file."""

    def __init__(self, code: str, *, latch: bool = False, stop: bool = False):
        super().__init__(code)
        self.code, self.latch, self.stop = code, latch, stop


@dataclass(frozen=True)
class _Parsed:
    item: Listed
    folder: str  # "daily" | "monthly"
    sha: str
    payload: dict
    key: str  # statement date or YYYY-MM
    when: date  # statement date or month end: --since / --until compare this (--retry matches `key`)

    @property
    def order(self) -> tuple:
        return (0 if self.folder == "daily" else 1, self.key, self.item.drive_file_id)


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


def _send(http: httpx.Client, path: str, *, body: dict, timeout: float, params: dict | None = None) -> httpx.Response:
    failure = None
    try:
        response = http.post(path, params=params, json=body, timeout=timeout)
    except httpx.HTTPError as exc:
        failure = type(exc).__name__
    if failure:  # raised outside the except block: the httpx error (and its request URL) is not the context
        raise FuturesError(f"portfolio:{failure}", stop=True)
    return response


def _status(response: httpx.Response) -> None:
    """A 5xx (the import's rolled-back attempt answers 500 with an outcome), 408 or 429 is transient: the run stops,
    nothing is latched and nothing from the body is used. Any other 4xx latches a commit run."""
    status = response.status_code
    if status >= 400:
        transient = status >= 500 or status in (408, 429)
        raise FuturesError(f"portfolio:{status}", latch=not transient, stop=transient)


def _json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        raise FuturesError("portfolio:body", stop=True) from None


def _findings_ok(findings: object) -> bool:
    """A list of `{"code", "dates", "letters"}`: short codes, ISO dates or months, letters only."""
    if not isinstance(findings, list):
        return False
    for f in findings:
        if not (isinstance(f, dict) and isinstance(f.get("code"), str) and _CODE_RE.match(f["code"])
                and isinstance(f.get("dates"), list) and isinstance(f.get("letters"), list)
                and all(isinstance(d, str) and _KEY_RE.match(d) for d in f["dates"])
                and all(isinstance(x, str) and _LETTER_RE.match(x) for x in f["letters"])):
            return False
    return True


def _post_statement(cfg: WorkerConfig, http: httpx.Client, parsed: _Parsed, dry_run: bool) -> dict:
    response = _send(http, ENDPOINT, body=parsed.payload, timeout=cfg.sinopac_futures_timeout_s,
                     params={"dry_run": "true" if dry_run else "false"})
    _status(response)
    body = _json(response)
    if (not isinstance(body, dict) or body.get("outcome") not in OUTCOMES or body.get("key") != parsed.key
            or body.get("errors") != [] or not _findings_ok(body.get("findings"))):
        raise FuturesError("portfolio:body", stop=True)
    return body


def _validate(cfg: WorkerConfig, http: httpx.Client, batch: list[_Parsed]) -> dict:
    """One `/validate` call with every payload in order; its answer, checked, as counts / keys / codes only (the
    items' file names are dropped)."""
    response = _send(http, f"{ENDPOINT}/validate", body={"payloads": [p.payload for p in batch]},
                     timeout=cfg.sinopac_futures_validate_timeout_s)
    _status(response)
    body = _json(response)
    items = body.get("items") if isinstance(body, dict) else None
    findings = body.get("findings") if isinstance(body, dict) else None
    if (not isinstance(items, list) or len(items) != len(batch) or not isinstance(findings, dict)
            or not all(isinstance(it, dict) and it.get("key") == p.key and it.get("outcome") in OUTCOMES
                       for it, p in zip(items, batch))
            or not all(isinstance(k, str) and _KEY_RE.match(k) and _findings_ok(v) for k, v in findings.items())):
        raise FuturesError("portfolio:body", stop=True)
    return {"items": [{"drive_file_id": p.item.drive_file_id, "key": it["key"], "outcome": it["outcome"]}
                      for it, p in zip(items, batch)], "findings": findings}


def _report(cfg: WorkerConfig, http: httpx.Client, summary: dict) -> None:
    codes = summary["codes"]
    body = {"run_at": datetime.now(timezone.utc).isoformat(), "result": codes[0] if codes else "ok",
            "code": codes[1][:64] if codes else None, "files_seen": summary["files_seen"], "posted": summary["posted"]}
    try:
        http.post(f"{ENDPOINT}/runs", json=body, timeout=cfg.sinopac_futures_timeout_s).raise_for_status()
    except httpx.HTTPError as exc:  # the report is best effort: it must not change the run's result
        log.info("sinopac-futures: run report not posted (%s)", type(exc).__name__)


def _latch_reason(latch: Path) -> str:
    try:
        reason = json.loads(latch.read_text(encoding="utf-8")).get("reason")
    except (OSError, ValueError, AttributeError):
        reason = None
    return reason[:64] if isinstance(reason, str) and reason else "latched"


def _ack(cfg: WorkerConfig, http: httpx.Client, key: str, status: str) -> dict:
    """`POST …/days/<key>/ack?status=…`: 204 with no body; 404 when that incident is not pending."""
    if not _KEY_RE.match(key) or status not in ACK_STATUSES:
        raise ValueError("ack key must be YYYY-MM-DD or YYYY-MM and status conflict or findings")
    failure = None
    try:
        response = http.post(f"{ENDPOINT}/days/{key}/ack", params={"status": status},
                             timeout=cfg.sinopac_futures_timeout_s)
    except httpx.HTTPError as exc:
        failure = type(exc).__name__
    if failure:
        return {"codes": ["failed", f"portfolio:{failure}"]}
    if response.status_code == 404:
        raise NothingPending(f"{key} ({status})")
    if response.status_code >= 400:
        return {"codes": ["failed", f"portfolio:{response.status_code}"]}
    return {"ack": {"key": key, "status": status}, "codes": []}


def _file_date(name: str) -> str | None:
    """The date or month a file name starts with (`YYYY-MM-DD_…` / `YYYY-MM_…`), the only part of a name a report keeps."""
    m = _FILE_DATE_RE.match(name)
    return m.group(1) if m else None


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
        self.history: dict = {"result": "ok", "counts": self.counts, "items": [], "findings": {}, "errors": [],
                              "duration_s": None}
        self._candidates: dict[str, list[str]] = {}
        self._passwords: dict[str, list[str]] | None = None
        self._staged = 0
        self._stopped = False

    def summary(self) -> dict:
        return {"dry_run": not self.commit, "validate_history": self.validate, "files_seen": self.files_seen,
                "posted": self.posted, "skipped": self.skipped, "counts": dict(self.counts), "codes": self.codes}

    def fail(self, exc: FuturesError, parsed_folder: str | None = None, item: Listed | None = None) -> None:
        log.info("sinopac-futures: %s", exc.code)
        if exc.stop:
            self._stopped = True
            if self.validate:  # no batch goes out: the report says why
                self.history["result"] = exc.code
        if exc.latch and self.commit:
            latch = {"reason": exc.code, "at": datetime.now(timezone.utc).isoformat()}
            if item is not None:  # which file to look at: its Drive id and name date, never the name or content
                latch |= {"drive_file_id": item.drive_file_id, "file_date": _file_date(item.name)}
            parser.write_private_json(self.cfg.state_dir, self.cfg.state_dir / LATCH, latch)
            self.codes = ["latched", exc.code]
        elif not self.codes:
            self.codes = ["failed", exc.code]
        if self.validate and item is not None:
            self.history["errors"].append({"drive_file_id": item.drive_file_id, "folder": parsed_folder,
                                           "file_date": _file_date(item.name), "code": exc.code})

    def candidates(self, folder_key: str) -> list[str]:
        if self._passwords is None:
            try:
                self._passwords = passwords.load(self.cfg.password_file)[0]
            except (OSError, ValueError):
                raise FuturesError("password_file", stop=True) from None
        return self._passwords.get(folder_key, [])

    def load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise FuturesError("state_file", stop=True) from None
        if not isinstance(data, dict):
            raise FuturesError("state_file", stop=True)
        self.state = data

    def remember(self, parsed: _Parsed, outcome: str) -> None:
        self.state[f"{parsed.item.drive_file_id}:{parsed.sha}"] = {
            "outcome": outcome, "kind": parsed.folder, "key": parsed.key, "date": parsed.when.isoformat(),
            "md5": parsed.item.md5, "at": datetime.now(timezone.utc).isoformat()}
        parser.write_private_json(self.cfg.state_dir, self.state_path, self.state)

    def known(self, item: Listed) -> bool:
        """A listed file already settled for this Drive id and md5 (the name decides nothing)."""
        if self.validate:
            return False
        for key, entry in self.state.items():
            if (key.rpartition(":")[0] == item.drive_file_id and isinstance(entry, dict) and entry.get("md5") == item.md5
                    and entry.get("outcome") in OUTCOMES
                    and not (self.retry_date and entry.get("key") == self.retry_date)):
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
                raise FuturesError("drive:VerifyError")
            return hashlib.sha256(data).hexdigest(), data
        except (DownloadError, OSError) as exc:
            raise FuturesError(f"drive:{type(exc).__name__}") from None
        finally:
            staged.unlink(missing_ok=True)

    def sweep_staging(self) -> None:
        """Remove this job's own files left behind by a crashed run (other jobs' files are not ours)."""
        if self.cfg.staging_dir.is_dir():
            for stale in self.cfg.staging_dir.glob(f"{STAGE_PREFIX}*.pdf"):
                stale.unlink(missing_ok=True)

    def read(self, data: bytes, folder_key: str) -> list:
        candidates = self.candidates(folder_key)
        if not candidates:
            raise FuturesError("decrypt", latch=True)
        try:
            unlocked = pdf.unlock(data, candidates)
        except pdf.PasswordError:
            raise FuturesError("decrypt", latch=True) from None
        except pdf.Malformed:
            raise FuturesError("pdf_malformed", latch=True) from None
        except pdf.TooLarge:
            raise FuturesError("pdf_too_large", latch=True) from None
        try:
            index = unlocked.password_index
            return extract_lines(data, candidates[index] if index is not None else None)
        except Exception:  # noqa: BLE001 — the library's text may hold statement content: code only
            raise FuturesError("extract", latch=True) from None

    def parse(self, item: Listed, folder: str, folder_key: str) -> _Parsed | None:
        sha, data = self.fetch(item)
        lines = self.read(data, folder_key)
        try:
            statement = parse_daily(lines) if folder == "daily" else parse_monthly(lines)
            payload = to_payload(statement, drive_file_id=item.drive_file_id, sha256=sha, file_name=item.name)
        except StatementError as exc:
            raise FuturesError(exc.code, latch=True) from None
        except Exception as exc:  # noqa: BLE001 — a parser crash on these bytes repeats on every run: latch, class only
            raise FuturesError(f"parse:{type(exc).__name__}"[:64], latch=True) from None
        if isinstance(statement, DailyStatement):
            key, when = statement.statement_date.isoformat(), statement.statement_date
        else:
            key, when = statement.month, statement.month_end
        if (self.since and when < self.since) or (self.until and when > self.until):
            self.skipped += 1
            return None
        return _Parsed(item, folder, sha, payload, key, when)

    def listing(self) -> list[tuple[str, str, Listed]]:
        out = []
        for folder, folder_key in (("daily", self.cfg.sinopac_futures_daily_folder),
                                   ("monthly", self.cfg.sinopac_futures_monthly_folder)):
            items = self.drive._list_root("sinopac_futures", f"{self.cfg.drive_root}/{folder_key}")
            out += [(folder, folder_key, item) for item in sorted(items, key=lambda i: i.path)]
        return out

    def post_each(self, batch: list[_Parsed]) -> None:
        for parsed in batch:
            self.posted += 1
            try:
                body = _post_statement(self.cfg, self.http, parsed, dry_run=not self.commit)
            except FuturesError as exc:  # nothing from a failed answer is recorded
                self.fail(exc, parsed.folder, parsed.item)
                if exc.stop or (exc.latch and self.commit):
                    return
                continue
            outcome = body["outcome"]
            self.counts[outcome] = self.counts.get(outcome, 0) + 1
            if self.commit:  # recorded one by one: a later failure keeps what was posted before it
                self.remember(parsed, outcome)

    def post_batch(self, batch: list[_Parsed]) -> None:
        if self.history["errors"]:
            self.history["result"] = "partial"
        if not batch:
            return
        if len(batch) > VALIDATE_MAX:  # the portfolio would answer 422: send nothing
            self.history["result"] = "validate_too_many"
            self.fail(FuturesError("validate_too_many"))
            return
        self.posted = len(batch)
        started = time.monotonic()
        try:
            answer = _validate(self.cfg, self.http, batch)
        except FuturesError as exc:
            self.history["result"] = exc.code
            self.fail(exc)
            return
        finally:
            self.history["duration_s"] = round(time.monotonic() - started, 3)
        for it in answer["items"]:
            self.counts[it["outcome"]] = self.counts.get(it["outcome"], 0) + 1
        self.history["items"], self.history["findings"] = answer["items"], answer["findings"]

    def run(self) -> None:
        if not self.cfg.password_file.is_file():  # checked here, not in the CLI, so the daily run reports it
            raise FuturesError("password_file", stop=True)
        self.sweep_staging()
        if not self.validate:
            self.load_state()
        listed = self.listing()
        if self.file_id:
            listed = [entry for entry in listed if entry[2].drive_file_id == self.file_id]
            if not listed:
                raise FuturesError("file_not_found")
        self.files_seen = len(listed)
        batch: list[_Parsed] = []
        for folder, folder_key, item in listed:
            if self.known(item):
                self.skipped += 1
                continue
            try:
                parsed = self.parse(item, folder, folder_key)
            except FuturesError as exc:
                self.fail(exc, folder, item)
                if exc.stop or (exc.latch and self.commit):
                    break
                continue
            if parsed is not None:
                batch.append(parsed)
        # A latch posts nothing; a file Drive failed to deliver is left out and retried by the next run.
        if self._stopped or (self.commit and self.codes[:1] == ["latched"]):
            return
        batch.sort(key=lambda p: p.order)
        if self.validate:
            self.post_batch(batch)
        else:
            self.post_each(batch)


def run(cfg: WorkerConfig, *, portfolio_http: httpx.Client, drive: Drive, dry_run: bool = False,
        file_id: str | None = None, since: date | None = None, until: date | None = None,
        retry_date: str | None = None, ack_key: str | None = None, ack_status: str | None = None,
        unlatch: bool = False, validate_history: bool = False) -> dict:
    """List, read and post the statements; failures become codes, never exceptions (a dry run never touches state).

    `retry_date` is a key, `YYYY-MM-DD` or `YYYY-MM`: the settled file with that statement date or month is posted again.
    """
    latch = cfg.state_dir / LATCH
    with _lock(cfg):
        if ack_key:  # touches neither Drive nor state: allowed while latched
            return _ack(cfg, portfolio_http, ack_key, ack_status or "")
        # only a plain daily commit run reports: the notifier reads the report as "the daily run's result"
        plain = not (dry_run or validate_history or file_id or since or until or retry_date or unlatch)
        if latch.exists() and not unlatch:
            if plain:
                _report(cfg, portfolio_http, {"codes": ["latched", _latch_reason(latch)], "files_seen": 0, "posted": 0})
            raise FuturesDisabled("sinopac-futures disabled until `python -m worker sinopac-futures --unlatch` succeeds")
        job = _Job(cfg, portfolio_http, drive, dry_run=dry_run, file_id=file_id, since=since, until=until,
                   retry_date=retry_date, validate_history=validate_history)
        try:
            job.run()
        except FuturesError as exc:
            if validate_history:
                job.history["result"] = exc.code
            job.fail(exc)
        except ListingError:
            if validate_history:
                job.history["result"] = "drive_listing"
            job.fail(FuturesError("drive_listing"))
        except Exception as exc:  # noqa: BLE001 — the run report still goes out; the class name is the only detail
            code = f"internal:{type(exc).__name__}"[:64]
            if validate_history:  # whatever raised, the history report must not read `ok`
                job.history["result"] = code
            job.fail(FuturesError(code))
        if validate_history:
            parser.write_private_json(cfg.state_dir, cfg.state_dir / HISTORY, job.history)
        summary = job.summary()
        if plain:
            _report(cfg, portfolio_http, summary)
        if job.commit and unlatch and not (file_id or since or until or retry_date) and not summary["codes"]:
            latch.unlink(missing_ok=True)  # only a clean plain commit run clears it
        return summary
