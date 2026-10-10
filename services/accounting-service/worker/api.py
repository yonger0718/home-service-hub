"""The worker's only way to application state: the ingest API over HTTP under a run lease (§4.1, §8)."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

LEASE_ROUTES = ("/renew", "/finish", "/files", "/sources", "/revisions")


def _redact(body):
    """Drop echoed request `input` and mask lease tokens before a body reaches a message or log."""
    if isinstance(body, dict):
        return {k: ("***" if k == "lease_token" else _redact(v)) for k, v in body.items() if k != "input"}
    if isinstance(body, list):
        return [_redact(v) for v in body]
    return body


CORRUPT = object()  # RunState.load result for an unreadable run.json (the pipeline records run_state_corrupt)


class ApiError(RuntimeError):
    def __init__(self, status: int, body):
        body = _redact(body)
        super().__init__(f"api {status}: {str(body)[:300]}")
        self.status, self.body = status, body


class LeaseLost(ApiError):
    pass


@dataclass(frozen=True)
class Lease:
    run_id: int
    lease_token: str
    lease_expires_at: str
    attempt: int

    def body(self, **extra) -> dict:
        return {"run_id": self.run_id, "lease_token": self.lease_token, **extra}


class ApiClient:
    def __init__(self, client: httpx.Client, token: str, *, guard: Callable[[], bool] | None = None):
        self.client, self.headers, self.guard = client, {"Authorization": f"Bearer {token}"}, guard

    def _call(self, method: str, path: str, *, json=None, params=None, lease_route: bool = False,
              timeout: float | None = None):
        if lease_route and self.guard is not None and self.guard():
            raise LeaseLost(409, "lease lost (local guard)")
        extra = {"timeout": timeout} if timeout is not None else {}
        response = self.client.request(method, path, json=json, params=params, headers=self.headers, **extra)
        if response.status_code >= 400:
            body = response.text[:300]
            if response.headers.get("content-type", "").startswith("application/json"):
                try:
                    body = response.json()
                except ValueError:
                    pass
            if response.status_code == 409 and (lease_route or any(path.endswith(r) or r in path for r in LEASE_ROUTES)):
                raise LeaseLost(response.status_code, body)
            raise ApiError(response.status_code, body)
        return response.json() if response.content else None

    @staticmethod
    def _lease(data: dict) -> Lease:
        return Lease(data["run_id"], data["lease_token"], data["lease_expires_at"], data["attempt"])

    def create_run(self, trigger: str, mode: str, initiator_hint: str | None) -> Lease:
        return self._lease(self._call("POST", "/statements/ingest-runs",
                                      json={"trigger": trigger, "mode": mode, "initiator_hint": initiator_hint}))

    def queued_runs(self) -> list[dict]:
        return self._call("GET", "/statements/ingest-runs", params={"status": "queued"}) or []

    def claim(self, run_id: int) -> Lease:
        return self._lease(self._call("POST", f"/statements/ingest-runs/{run_id}/claim", lease_route=True))

    def renew(self, lease: Lease, *, timeout: float | None = None) -> None:
        self._call("POST", f"/statements/ingest-runs/{lease.run_id}/renew", json={"lease_token": lease.lease_token},
                   lease_route=True, timeout=timeout)

    def finish(self, lease: Lease, status: str, summary: dict) -> None:
        self._call("POST", f"/statements/ingest-runs/{lease.run_id}/finish",
                   json=lease.body(status=status, summary=summary), lease_route=True)

    def account_map(self) -> tuple[dict[str, int], str]:
        data = self._call("GET", "/statements/account-map")
        return data["account_map"], data["mapping_version"]

    def register_file(self, lease: Lease, sha256: str, size: int, kind: str, object_key: str) -> dict:
        return self._call("POST", "/statements/files", json=lease.body(sha256=sha256, size=size, kind=kind,
                                                                       object_key=object_key), lease_route=True)

    def update_file(self, lease: Lease, file_id: int, **fields) -> dict:
        return self._call("PATCH", f"/statements/files/{file_id}", json=lease.body(**fields), lease_route=True)

    def register_source(self, lease: Lease, file_id: int, root: str, drive_file_id: str, drive_path: str,
                        drive_md5: str, drive_size: int) -> dict:
        return self._call("POST", "/statements/sources", json=lease.body(
            file_id=file_id, root=root, drive_file_id=drive_file_id, drive_path=drive_path, drive_md5=drive_md5,
            drive_size=drive_size), lease_route=True)

    def mark_removed(self, lease: Lease, seen_ids: list[str], allow_empty: bool = False) -> int:
        return self._call("POST", "/statements/sources/mark-removed",
                          json=lease.body(seen_drive_file_ids=seen_ids, allow_empty=allow_empty),
                          lease_route=True)["removed"]

    def list_files(self) -> list[dict]:
        return self._call("GET", "/statements/files") or []

    def sweep(self, lease: Lease) -> dict:
        return self._call("POST", "/reconciliation/sweep", json=lease.body(), lease_route=True)

    def submit_revision(self, lease: Lease, body: dict) -> dict:
        return self._call("POST", "/statements/revisions", json=lease.body(**body), lease_route=True)


class LeaseKeeper:
    """Renews the lease every `interval_s` on a daemon thread; `lost` becomes True after a failed renew."""

    def __init__(self, client: ApiClient, lease: Lease, interval_s: float = 300, retry_s: float = 1.0):
        self.retry_s = retry_s
        self.client, self.lease, self.interval_s = client, lease, interval_s
        self.lost = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"lease-keeper-{lease.run_id}", daemon=True)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            for attempt in (1, 2):  # one retry after 1 s before the lease counts as lost
                try:
                    self.client.renew(self.lease, timeout=10)
                    break
                except Exception:  # noqa: BLE001 — ApiError, httpx transport errors, OSError: all mean "not renewed"
                    if attempt == 2 or self._stop.wait(self.retry_s):
                        self.lost = True
                        return

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=15)  # > the renew timeout: no renew is in flight after this
        return False


class RunState:
    """The claimed lease on disk (0600) so a restarted worker resumes its own run (§4.1)."""

    def __init__(self, path: Path):
        self.path = path

    def save(self, lease: Lease, *, trigger: str, mode: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({**lease.__dict__, "trigger": trigger, "mode": mode}, fh)
        os.replace(tmp, self.path)

    def load(self):
        """(lease, trigger, mode), None when absent, or CORRUPT (file removed) when unreadable."""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            lease = Lease(data["run_id"], data["lease_token"], data["lease_expires_at"], data["attempt"])
            return lease, data.get("trigger", "owner_cli"), data.get("mode", "live")
        except (ValueError, KeyError, TypeError):
            self.clear()
            return CORRUPT

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
