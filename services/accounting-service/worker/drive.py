"""Google Drive acquisition through rclone: a complete listing, then downloads by file id (§5.1)."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from worker.config import WorkerConfig
from worker.inbox import MAX_PDF_BYTES, private_dir
from worker.runner import Runner


class ListingError(RuntimeError):
    """The listing was not complete: nothing may be marked removed."""


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class Listed:
    drive_file_id: str
    path: str  # relative to the root listed
    name: str
    size: int
    md5: str
    mod_time: str
    root: str  # 'mail' | 'manual'


def folder_of(item: Listed) -> str:
    parts = [p for p in item.path.split("/") if p]
    return "/".join(parts[:-1])


def kind_of(item: Listed) -> str:
    """The manual root holds 綜合月結單 bank statements; mail files are bank only under 銀行帳戶."""
    return "bank" if item.root == "manual" or folder_of(item).startswith("銀行帳戶") else "card"


class Drive:
    def __init__(self, cfg: WorkerConfig, runner: Runner):
        self.cfg, self.runner = cfg, runner

    def _list_root(self, root: str, drive_path: str) -> list[Listed]:
        args = ["rclone", "lsjson", "--hash", "--recursive", "--files-only", f"{self.cfg.rclone_remote}{drive_path}"]
        result = self.runner.run(args, timeout=300)
        if result.returncode != 0:
            raise ListingError(f"rclone lsjson exit {result.returncode}")
        try:
            rows = json.loads(result.stdout.decode("utf-8"))
            if not isinstance(rows, list):
                raise ListingError("rclone lsjson output is not a list")
            out = []
            for row in rows:
                if not str(row.get("Path", "")).lower().endswith(".pdf"):
                    continue
                md5 = (row.get("Hashes") or {}).get("md5")
                if not md5 or "ID" not in row:
                    raise ListingError(f"listing row without md5 or ID: {row.get('Path')}")
                out.append(Listed(row["ID"], row["Path"], row.get("Name", ""), int(row.get("Size", 0)),
                                  md5.lower(), row.get("ModTime", ""), root))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
            raise ListingError("rclone lsjson output is malformed") from exc
        return out

    def list_pdfs(self) -> list[Listed]:
        return self._list_root("mail", self.cfg.mail_root) + self._list_root("manual", self.cfg.manual_root)

    def download(self, item: Listed, dest: Path, *, max_bytes: int = MAX_PDF_BYTES, timeout_s: float = 600,
                 poll_s: float = 0.25) -> None:
        """Download by id, watching the destination: the process group is killed the moment `dest` exceeds
        `max_bytes` (DownloadError "too_large") or `timeout_s` passes (DownloadError "timeout")."""
        private_dir(dest.parent)
        dest.unlink(missing_ok=True)  # never let a stale file pass for a fresh download
        args = ["rclone", "backend", "copyid", self.cfg.rclone_remote, item.drive_file_id, str(dest)]
        proc = self.runner.start(args)
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                rc = proc.wait(timeout=poll_s)
            except subprocess.TimeoutExpired:
                rc = None
            try:
                over = dest.exists() and dest.stat().st_size > max_bytes
            except OSError:
                over = False
            if over:
                _kill_group(proc)
                dest.unlink(missing_ok=True)
                raise DownloadError("too_large")
            if rc is not None:
                break
            if time.monotonic() > deadline:
                _kill_group(proc)
                raise DownloadError("timeout")
        if rc != 0 or not dest.exists():
            raise DownloadError(f"rclone copyid exit {rc}")
        os.chmod(dest, 0o600)


def _kill_group(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass
