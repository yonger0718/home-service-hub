"""Run orchestration and the per-file state machine (§4.1, §4.2, §5.1–5.6, rulings 3–5, 9–10, 13)."""
from __future__ import annotations

import fcntl
import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from worker import drive as drive_mod
from worker import inbox as inbox_mod
from worker import mask, parse_schema, parser as parser_mod, passwords, pdf
from worker import api as api_mod
from worker.api import ApiClient, ApiError, Lease, LeaseKeeper, LeaseLost, RunState
from worker.config import WorkerConfig
from worker.runner import Runner

log = logging.getLogger("worker")
BACKOFF = {1: timedelta(hours=1), 2: timedelta(hours=6)}
MAX_TRANSIENT = 5
FULL_RUN_TRIGGERS = ("timer", "owner_cli")


class RunAborted(RuntimeError):
    pass


class Refused(RuntimeError):
    pass


class AlreadyRunning(RuntimeError):
    pass


class RetryLog:
    """Per-sha transient epoch and store state, per Drive identity acquisition backoff (0600 JSON).
    A missing, empty, corrupt or non-object file loads as empty (a retry log is advisory)."""

    def __init__(self, path: Path):
        self.path, self.data = path, {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, UnicodeDecodeError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            self.data = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}

    def get(self, key: str) -> dict:
        default = {"transient_attempts": 0, "sandbox_failed_at": None,
                   "store": {"pending": True, "attempts": 0, "next_at": None, "confirmed_by": None},
                   "attempts": 0, "next_at": None}
        return {**default, **(self.data.get(key) or {})}

    def _write(self) -> None:
        inbox_mod.private_dir(self.path.parent)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def set(self, sha: str, **fields) -> None:
        self.data[sha] = {**self.get(sha), **fields}
        self._write()

    def delete(self, key: str) -> None:
        if self.data.pop(key, None) is not None:
            self._write()


@dataclass
class Services:
    cfg: WorkerConfig
    api: ApiClient
    drive: drive_mod.Drive
    inbox: inbox_mod.Inbox
    index: inbox_mod.SourceIndex
    retries: RetryLog
    store: inbox_mod.ObjectStore
    parser: parser_mod.Parser
    gate: parser_mod.Gate
    candidates: passwords.Candidates
    credential_version: str
    masker: mask.Masker
    runner: Runner
    keeper: LeaseKeeper | None = None
    parser_disabled: bool = False
    parser_version: str = ""
    cli_version: str = ""


def build(cfg: WorkerConfig, client: httpx.Client, runner: Runner) -> Services:
    inbox_mod.private_dir(cfg.state_dir)
    token = cfg.api_token_file.read_text(encoding="utf-8").strip()
    candidates, credential_version = passwords.load(cfg.password_file)
    services = Services(cfg, None, drive_mod.Drive(cfg, runner), inbox_mod.Inbox(cfg),
                        inbox_mod.SourceIndex(cfg.state_dir / "sources.json"), RetryLog(cfg.state_dir / "retries.json"),
                        inbox_mod.store_for(cfg), parser_mod.Parser(cfg, runner), parser_mod.Gate(cfg), candidates,
                        credential_version, mask.Masker(passwords.identity(cfg.password_file)), runner)
    services.api = ApiClient(client, token, guard=lambda: services.keeper is not None and services.keeper.lost)
    services.cli_version = services.parser.cli_version() or "unknown"
    services.parser_version = parse_schema.version(services.cli_version, cfg.parser_model)
    return services


@contextmanager
def singleton(cfg: WorkerConfig):
    inbox_mod.private_dir(cfg.state_dir)
    with cfg.lock_path.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlreadyRunning(str(cfg.lock_path)) from exc
        yield


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value) -> datetime | None:
    """An ISO timestamp from the API or a state file as an aware UTC datetime (None when absent or unreadable)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def gate_checked_at(gate: parser_mod.Gate, parser: parser_mod.Parser) -> datetime | None:
    """`checked_at` of the worker parser's gate evidence when its last gate run was ok, else None."""
    try:
        evidence = json.loads(gate.evidence_path_for(gate.key(parser)).read_text(encoding="utf-8"))
        return _dt(evidence["checked_at"]) if evidence.get("ok") else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return None


def retry_due(row: dict, retry: dict, now: datetime, credential_version: str, mapping_version: str,
              parser_version: str, *, latched: bool = False, gate_ok_at: datetime | None = None) -> bool:
    status, failure = row["status"], row.get("failure")
    if status in ("new", "unlocked"):
        return True
    if status == "parsed":
        return False
    if failure == "transient":
        nxt = _dt(row.get("next_retry_at"))
        return (nxt is None or nxt <= now) and retry["transient_attempts"] < MAX_TRANSIENT
    if failure == "password":
        return row.get("credential_version") != credential_version
    if failure == "mapping":
        return row.get("mapping_version") != mapping_version
    if failure in ("parse", "guardrail", "no_text_layer", "too_large"):
        return row.get("parser_version") != parser_version
    if failure == "sandbox":
        if latched:
            return False
        failed_at = _dt(retry.get("sandbox_failed_at"))
        gate_newer = gate_ok_at is not None and (failed_at is None or gate_ok_at > failed_at)
        return row.get("parser_version") != parser_version or gate_newer
    return False


def _folder_key(listed: drive_mod.Listed) -> str:
    parts = [p for p in drive_mod.folder_of(listed).split("/") if p][:2]
    return "/".join([listed.root, *parts])


@dataclass
class Summary:
    listed: int = 0
    new_files: int = 0
    parsed: int = 0
    needs_review: int = 0
    failed: int = 0
    ignored: int = 0
    skipped: int = 0
    unchanged: int = 0
    deferred: int = 0
    transient: int = 0
    too_large: int = 0
    cases_opened: int = 0
    live_revisions: int = 0
    resumed: bool = False
    errors: list[str] = field(default_factory=list)
    sweep: dict = field(default_factory=dict)
    versions: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _fail(services: Services, lease: Lease, file_row: dict, failure: str, summary: Summary, _epoch: bool = True,
          **versions) -> str:
    sha = file_row["sha256"]
    fields = {"status": "failed", "failure": failure, **versions}
    if failure == "transient":
        attempts = services.retries.get(sha)["transient_attempts"] + (1 if _epoch else 0)
        fields["next_retry_at"] = (_now() + BACKOFF.get(max(attempts, 1), timedelta(hours=24))).isoformat()
        local = {"transient_attempts": attempts}
    else:
        local = {"transient_attempts": 0,
                 "sandbox_failed_at": _now().isoformat() if failure == "sandbox" else None}
    services.api.update_file(lease, file_row["id"], **fields)  # the local epoch moves only once the API has it
    services.retries.set(sha, **local)
    if failure == "transient":
        summary.transient += 1
    else:
        summary.failed += 1
    return "failed"


def _review(services: Services, lease: Lease, file_row: dict, failure: str, summary: Summary, **fields) -> str:
    services.retries.set(file_row["sha256"], transient_attempts=0)
    services.api.update_file(lease, file_row["id"], status="needs_review", failure=failure,
                             parser_version=services.parser_version, **fields)
    summary.needs_review += 1
    return "needs_review"


def process_file(services: Services, lease: Lease, file_row: dict, listed: drive_mod.Listed, data: bytes,
                 account_map: dict[str, int], mapping_version: str, summary: Summary) -> str:
    account_id = account_map.get(_folder_key(listed))
    if account_id is None:
        services.api.update_file(lease, file_row["id"], status="ignored", failure="mapping",
                                 mapping_version=mapping_version)
        summary.ignored += 1
        return "ignored"
    if services.parser_disabled:
        summary.skipped += 1
        return "skipped"
    candidates = services.candidates.get(passwords.password_key(listed.root, drive_mod.folder_of(listed)), [])
    try:
        unlocked = pdf.unlock(data, candidates)
        password = candidates[unlocked.password_index] if unlocked.password_index is not None else None
        extracted = pdf.extract(data, password)
    except pdf.PasswordError:
        return _fail(services, lease, file_row, "password", summary, credential_version=services.credential_version)
    except pdf.Malformed:
        return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
    except pdf.NoTextLayer:
        return _review(services, lease, file_row, "no_text_layer", summary, has_text_layer=False)
    except pdf.TooLarge:
        return _review(services, lease, file_row, "too_large", summary)
    except pdf.ExtractTimeout:
        return _fail(services, lease, file_row, "transient", summary)
    services.api.update_file(lease, file_row["id"], status="unlocked", failure=None, has_text_layer=True,
                             text_chars=len(extracted.text), pages=extracted.pages,
                             credential_version=services.credential_version)
    masked = services.masker.render(extracted, candidates)
    if len(masked.encode("utf-8")) > parser_mod.STDIN_CAP:
        return _review(services, lease, file_row, "too_large", summary)
    try:
        parsed = services.parser.parse(masked)
    except parser_mod.ParseError as exc:
        if exc.reason in ("sandbox", "cli_version", "auth"):
            services.parser_disabled = True
            services.gate.latch(f"{exc.reason} on real input", services.gate.key(services.parser))
            if "parser_disabled" not in summary.errors:
                summary.errors.append("parser_disabled")
            return _fail(services, lease, file_row, "sandbox", summary, parser_version=services.parser_version)
        if exc.reason in ("timeout", "exit"):  # CLI non-zero, rate limit, outage: retry on the ladder
            return _fail(services, lease, file_row, "transient", summary)
        return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
    body = revision_body(parsed, file_id=file_row["id"], account_id=account_id, parser_version=services.parser_version)
    try:
        out = services.api.submit_revision(lease, body)
    except LeaseLost:
        raise
    except ApiError as exc:
        if exc.status == 422:
            return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
        raise
    summary.cases_opened += len(out.get("case_ids") or [])
    summary.live_revisions += 1 if out.get("mode") == "live" else 0
    if not out.get("guardrail_ok"):
        return _review(services, lease, file_row, "guardrail", summary)
    services.retries.set(file_row["sha256"], transient_attempts=0)
    services.api.update_file(lease, file_row["id"], status="parsed", failure=None, parser_version=services.parser_version)
    summary.parsed += 1
    return "parsed"


def revision_body(parsed: parse_schema.StatementParse, *, file_id: int, account_id: int, parser_version: str) -> dict:
    lines = [line.model_dump(mode="json") for line in parsed.lines]
    return {"file_id": file_id, "account_id": account_id, "kind": parsed.kind, "parser": "claude-cli",
            "parser_version": parser_version, "currency": parsed.currency,
            "period_start": parsed.period_start.isoformat(), "period_end": parsed.period_end.isoformat(),
            "closing_date": parsed.closing_date.isoformat() if parsed.closing_date else None,
            "due_date": parsed.due_date.isoformat() if parsed.due_date else None,
            "opening_balance": parsed.opening_balance, "statement_total": parsed.statement_total,
            "minimum_payment": parsed.minimum_payment, "lines": lines, "raw": {"notes": parsed.notes}}


def _download_verified(services: Services, listed: drive_mod.Listed, summary: Summary):
    """Download by id, verify, publish; on a mismatch re-list once (the file may have been replaced).
    Returns (Published, Listed-as-verified) or None (transient, counted)."""
    staged = services.cfg.staging_dir / f"{listed.drive_file_id}.tmp"
    inbox_mod.private_dir(services.cfg.staging_dir)
    ident = f"acquire:{listed.drive_file_id}:{listed.md5}"
    backoff = services.retries.get(ident)
    if backoff["next_at"] and (_dt(backoff["next_at"]) or _now()) > _now():
        summary.deferred += 1
        return None
    for attempt in (1, 2):
        try:
            if listed.size > inbox_mod.MAX_PDF_BYTES:  # re-checked for a refreshed identity (AGENT-96 Must 8)
                summary.too_large += 1
                return None
            services.drive.download(listed, staged)
            if staged.stat().st_size > inbox_mod.MAX_PDF_BYTES:  # belt and braces: Drive.download kills at the cap
                staged.unlink(missing_ok=True)
                summary.too_large += 1
                return None
            published = services.inbox.publish(staged, expected_md5=listed.md5, expected_size=listed.size)
            services.retries.delete(ident)
            return published, listed
        except inbox_mod.VerifyError:
            if attempt == 2:
                break
            try:
                fresh = next((i for i in services.drive.list_pdfs() if i.drive_file_id == listed.drive_file_id), None)
            except drive_mod.ListingError:
                break
            if fresh is None or fresh.md5 == listed.md5:
                break
            listed = fresh
        except drive_mod.DownloadError as exc:
            if str(exc) == "too_large":
                staged.unlink(missing_ok=True)
                summary.too_large += 1
                return None
            break
    attempts = backoff["attempts"] + 1
    services.retries.set(ident, attempts=attempts, next_at=(_now() + BACKOFF.get(attempts, timedelta(hours=24))).isoformat())
    summary.transient += 1
    if "download" not in summary.errors:
        summary.errors.append("download")
    return None


def store_id_for(cfg: WorkerConfig) -> str:
    """Identity of the configured store: normalised host:port plus bucket ("null" without MinIO)."""
    if cfg.minio_endpoint is None:
        return "null"
    host, _secure = inbox_mod._split_endpoint(cfg.minio_endpoint)
    return f"{host}/{cfg.minio_bucket}"


def _ensure_stored(services: Services, lease: Lease, file_row: dict, published_path: Path, summary: Summary) -> bool:
    """Storage has its own due/attempt ladder (AGENT-96 Must 4). Returns False only when the file may not proceed
    to parsing this run (an in-flight row whose object is not stored). A settled row (parsed, needs_review, ignored,
    failed for another reason) keeps its status whatever the store says: only the store ladder advances.
    A confirmation and a ladder belong to the store that produced them (`confirmed_by`, `for_store`): a NullStore
    "yes" never satisfies a MinIO configured later, and a new endpoint or bucket starts a fresh ladder."""
    sha = file_row["sha256"]
    store_id = store_id_for(services.cfg)
    store = services.retries.get(sha)["store"]
    if not store["pending"] and store.get("confirmed_by") == store_id:
        return True
    if store.get("for_store") != store_id:
        store = {"pending": True, "attempts": 0, "next_at": None, "confirmed_by": store.get("confirmed_by"),
                 "for_store": store_id}
    in_flight = (file_row.get("status") in ("new", "unlocked")
                 or (file_row.get("status") == "failed" and file_row.get("failure") == "transient"))
    if (store["next_at"] and (_dt(store["next_at"]) or _now()) > _now()) or store["attempts"] >= MAX_TRANSIENT:
        if not in_flight:
            return True
        summary.deferred += 1
        return False
    key = f"by-sha/{sha}.pdf"
    try:
        if not services.store.exists(key):
            services.store.put(key, published_path)
    except Exception:  # noqa: BLE001 — MinIO failures are transient by the §5.1 matrix
        attempts = store["attempts"] + 1
        services.retries.set(sha, store={"pending": True, "attempts": attempts, "confirmed_by": None,
                                         "for_store": store_id,
                                         "next_at": (_now() + BACKOFF.get(attempts, timedelta(hours=24))).isoformat()})
        if "store" not in summary.errors:
            summary.errors.append("store")
        if not in_flight:
            return True
        _fail(services, lease, file_row, "transient", summary, _epoch=False)
        return False
    services.retries.set(sha, store={"pending": False, "attempts": 0, "next_at": None, "confirmed_by": store_id,
                                     "for_store": store_id})
    return True


def _acquire(services: Services, lease: Lease, listed: drive_mod.Listed, known: dict[str, dict], summary: Summary,
             trace: dict | None = None):
    """Ruling 10: fetch bytes only when the index does not know them; register the source every listing;
    ensure the object is stored. Returns (file_row, bytes-or-None, listed) or None when skipped this run."""
    if listed.size > inbox_mod.MAX_PDF_BYTES:
        summary.too_large += 1
        return None
    sha = services.index.get(listed.drive_file_id, listed.md5)
    path = services.cfg.inbox_dir / f"{sha}.pdf" if sha else None
    trace = trace if trace is not None else {}
    data = None
    if not (sha and sha in known and path.exists()):
        got = _download_verified(services, listed, summary)
        if got is None:
            if sha and sha in known:  # ruling 10: every listed source is re-registered every run
                services.api.register_source(lease, known[sha]["id"], listed.root, listed.drive_file_id, listed.path,
                                             listed.md5, listed.size)
            return None
        published, listed = got
        sha, path = published.sha256, published.path
        trace["sha"] = sha
        data = path.read_bytes()
        services.index.put(listed.drive_file_id, listed.md5, sha)
    trace["sha"] = sha
    kind = drive_mod.kind_of(listed)
    file_row = services.api.register_file(lease, sha, path.stat().st_size, kind, f"by-sha/{sha}.pdf")
    if sha not in known:
        summary.new_files += 1
    known[sha] = file_row
    services.api.register_source(lease, file_row["id"], listed.root, listed.drive_file_id, listed.path, listed.md5,
                                 listed.size)
    if not _ensure_stored(services, lease, file_row, path, summary):
        return None
    return file_row, data, listed


def _sweep(services: Services, lease: Lease, summary: Summary) -> None:
    # a sweep 409 is a lease failure (LeaseLost) and aborts the run; other API errors propagate to _execute
    summary.sweep = services.api.sweep(lease)


def resume_or_none(services: Services, state: RunState, summary: Summary):
    """§4.1 restart recovery: renew -> resume; expired -> reclaim the same run; discard run.json only when both
    renew and claim say 404/409. Any other API error keeps run.json and aborts (resume_unavailable).
    Returns (lease, trigger, mode) or None."""
    saved = state.load()
    if saved is api_mod.CORRUPT:
        summary.errors.append("run_state_corrupt")
        return None
    if saved is None:
        return None
    lease, trigger, mode = saved
    try:
        services.api.renew(lease)
        summary.resumed = True
        return lease, trigger, mode
    except ApiError as exc:
        if exc.status not in (404, 409):
            raise RunAborted("resume_unavailable") from exc
    try:
        fresh = services.api.claim(lease.run_id)
    except ApiError as exc:
        if exc.status not in (404, 409):
            raise RunAborted("resume_unavailable") from exc
        state.clear()
        return None
    summary.resumed = True
    state.save(fresh, trigger=trigger, mode=mode)
    return fresh, trigger, mode


def _execute(services: Services, summary: Summary, lease: Lease, trigger: str, mode: str) -> dict:
    """Process one claimed run. A normal end or an Exception finishes the run; an interrupt (KeyboardInterrupt,
    SystemExit) leaves it claimed with run.json intact so the next start resumes it."""
    state = RunState(services.cfg.state_dir / "run.json")
    state.save(lease, trigger=trigger, mode=mode)  # right after the claim (also for poll's claimed leases)
    services.parser_disabled = False
    for stale in services.cfg.staging_dir.glob("*.tmp"):  # leftovers of crashed runs (the singleton guarantees none is live)
        stale.unlink(missing_ok=True)
    summary.versions = {"parser_version": services.parser_version, "cli_version": services.cli_version,
                        "model": services.cfg.parser_model, "credential_version": services.credential_version}
    status, interrupted = "failed", False
    try:
        with LeaseKeeper(services.api, lease) as keeper:
            services.keeper = keeper
            try:
                services.gate.ensure(services.parser)
            except parser_mod.ParserDisabled:
                services.parser_disabled = True
                summary.errors.append("parser_disabled")
            except parser_mod.GateUnavailable:  # transient canary failure: skip parsing, nothing latched, run done
                services.parser_disabled = True
                summary.errors.append("gate_unavailable")
            account_map, mapping_version = services.api.account_map()
            summary.versions["mapping_version"] = mapping_version
            try:
                listing = services.drive.list_pdfs()
            except drive_mod.ListingError as exc:
                summary.errors.append("listing")
                raise RunAborted("listing") from exc
            summary.listed = len(listing)
            known = {f["sha256"]: f for f in services.api.list_files()}
            latched = services.gate.latch_path.exists()
            gate_ok_at = gate_checked_at(services.gate, services.parser)
            for listed in listing:
                if keeper.lost:
                    raise RunAborted("lease lost")
                row, trace = None, {}
                try:
                    acquired = _acquire(services, lease, listed, known, summary, trace)
                    if acquired is None:
                        continue
                    file_row, data, listed = acquired
                    row = file_row
                    retry = services.retries.get(file_row["sha256"])
                    if not retry_due(file_row, retry, _now(), services.credential_version, mapping_version,
                                     services.parser_version, latched=latched, gate_ok_at=gate_ok_at):
                        if file_row.get("failure") == "transient":
                            summary.deferred += 1
                        else:
                            summary.unchanged += 1
                        continue
                    if data is None:
                        data = (services.cfg.inbox_dir / f"{file_row['sha256']}.pdf").read_bytes()
                    process_file(services, lease, file_row, listed, data, account_map, mapping_version, summary)
                except (LeaseLost, RunAborted, httpx.TransportError, drive_mod.ListingError):
                    raise
                except Exception as exc:  # noqa: BLE001 — one bad file must not stop the run
                    if isinstance(exc, ApiError) and exc.status in (401, 403):
                        summary.errors.append("auth") if "auth" not in summary.errors else None
                        raise RunAborted("auth") from exc
                    log.warning("file failed: %s", type(exc).__name__)  # class only, never text or traceback
                    if "file_error" not in summary.errors:
                        summary.errors.append("file_error")
                    row = row or known.get(trace.get("sha", ""))
                    # only a row that was due and in flight is marked; anything else keeps its status
                    if row is not None and (row.get("status") in ("new", "unlocked")
                                            or (row.get("status") == "failed" and row.get("failure") == "transient")):
                        try:
                            _fail(services, lease, row, "transient", summary)
                        except (LeaseLost, httpx.TransportError):
                            raise
                        except Exception as inner:  # noqa: BLE001
                            log.warning("could not record the file failure: %s", type(inner).__name__)
            services.api.mark_removed(lease, [i.drive_file_id for i in listing], allow_empty=not listing)
            if trigger in FULL_RUN_TRIGGERS:
                _sweep(services, lease, summary)
            if keeper.lost:
                raise RunAborted("lease lost")
            status = "failed" if "parser_disabled" in summary.errors else "done"
    except LeaseLost as exc:
        if "lease_lost" not in summary.errors:
            summary.errors.append("lease_lost")
        raise RunAborted("lease lost") from exc
    except RunAborted:
        raise
    except ApiError as exc:
        code = "auth" if exc.status in (401, 403) else "unexpected"
        if code not in summary.errors:
            summary.errors.append(code)
        raise RunAborted(code) from exc
    except Exception as exc:
        if "unexpected" not in summary.errors:
            summary.errors.append("unexpected")
        log.warning("run failed: %s", type(exc).__name__)
        raise RunAborted("unexpected") from exc
    except BaseException:
        interrupted = True  # KeyboardInterrupt / SystemExit: do not finish, keep run.json
        raise
    finally:
        if not interrupted:
            try:  # the guard stays armed (services.keeper is still set), so a lost lease cannot finish either
                services.api.finish(lease, status, summary.as_dict())
                state.clear()
            except Exception as exc:  # noqa: BLE001 — ApiError or transport: the lease expires and the run is reclaimed
                log.warning("finish failed: %s", type(exc).__name__)
        services.keeper = None
    return summary.as_dict()


def run(services: Services, *, trigger: str, mode: str = "live", initiator_hint: str | None = None,
        acknowledge_live_periods: bool = False, lease: Lease | None = None) -> dict:
    if mode == "backfill" and not acknowledge_live_periods:
        raise Refused("backfill may touch live periods; pass --acknowledge-live-periods")
    summary = Summary()
    if lease is None:
        resumed = resume_or_none(services, RunState(services.cfg.state_dir / "run.json"), summary)
        if resumed is not None:
            if (resumed[1], resumed[2]) != (trigger, mode):
                log.info("resuming run %s as %s/%s (requested %s/%s)", resumed[0].run_id, resumed[1], resumed[2],
                         trigger, mode)
            lease, trigger, mode = resumed
        else:
            lease = services.api.create_run(trigger, mode, initiator_hint)
    return _execute(services, summary, lease, trigger, mode)


def poll(services: Services, *, initiator_hint: str | None) -> dict:
    """Resume a crashed run of our own first, then claim every queued run in turn. The result is the last summary
    plus `claimed` and `bad_runs` (runs that finished failed or aborted)."""
    out: dict = {"claimed": 0, "bad_runs": 0}
    claimed = bad = 0
    summary = Summary()
    resumed = resume_or_none(services, RunState(services.cfg.state_dir / "run.json"), summary)
    todo: list[tuple[Summary, Lease, str, str]] = []
    if resumed is not None:
        todo.append((summary, *resumed))
    seen = {resumed[0].run_id} if resumed else set()
    for step in range(2):  # the resumed run first; queued runs are fetched after it
        if step == 1:
            for queued in services.api.queued_runs():
                if queued["id"] in seen:
                    continue
                try:
                    lease = services.api.claim(queued["id"])
                except LeaseLost:  # someone else claimed it
                    continue
                todo.append((Summary(), lease, "enqueue", "live"))
        for item_summary, lease, trigger, mode in todo:
            claimed += 1
            try:
                out = _execute(services, item_summary, lease, trigger, mode)
            except RunAborted:
                bad += 1
                continue
            if "parser_disabled" in out["errors"]:
                bad += 1
        todo = []
    out["claimed"], out["bad_runs"] = claimed, bad
    return out
