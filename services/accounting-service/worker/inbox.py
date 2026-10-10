"""The private inbox: verified bytes published atomically by sha256, optionally mirrored to MinIO (ruling 4)."""
from __future__ import annotations

import filecmp
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from worker.config import WorkerConfig

MAX_PDF_BYTES = 20_000_000


def private_dir(path: Path) -> Path:
    """mkdir -p with mode 0700 (and chmod when it already existed with a wider mode)."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


class VerifyError(RuntimeError):
    pass


class CollisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Published:
    sha256: str
    object_key: str
    path: Path


def _digests(path: Path) -> tuple[str, str, int]:
    md5, sha = hashlib.md5(), hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            md5.update(chunk)
            sha.update(chunk)
            size += len(chunk)
    return md5.hexdigest(), sha.hexdigest(), size


def _same_bytes(a: Path, b: Path) -> bool:
    return filecmp.cmp(a, b, shallow=False)  # byte comparison, not a second digest (AGENT-95 Should)


class Inbox:
    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg

    def publish(self, staged: Path, *, expected_md5: str, expected_size: int) -> Published:
        md5, sha, size = _digests(staged)
        if md5 != expected_md5.lower() or size != expected_size:
            staged.unlink(missing_ok=True)
            raise VerifyError("downloaded bytes do not match the listing")
        private_dir(self.cfg.inbox_dir)
        target = self.cfg.inbox_dir / f"{sha}.pdf"
        if target.exists():
            if not _same_bytes(target, staged):
                raise CollisionError(f"inbox object {sha} holds different bytes")
            staged.unlink(missing_ok=True)
        else:
            tmp = self.cfg.inbox_dir / f".{sha}.tmp"
            os.replace(staged, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        return Published(sha, f"by-sha/{sha}.pdf", target)


class SourceIndex:
    """(drive_file_id, md5) -> sha256 of what this worker already published; JSON, private, written atomically.

    A download cache only (ruling 10): it decides whether bytes are fetched again, nothing else.
    """

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, str] = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def get(self, drive_file_id: str, md5: str) -> str | None:
        return self.data.get(f"{drive_file_id}:{md5.lower()}")

    def put(self, drive_file_id: str, md5: str, sha256: str) -> None:
        self.data[f"{drive_file_id}:{md5.lower()}"] = sha256
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fd, 0o600)
            json.dump(self.data, fh)
        os.replace(tmp, self.path)


class ObjectStore(Protocol):
    def put(self, key: str, path: Path) -> None: ...
    def exists(self, key: str) -> bool: ...


class NullStore:
    def put(self, key: str, path: Path) -> None:
        return None

    def exists(self, key: str) -> bool:
        return True


class MinioStore:
    def __init__(self, endpoint: str, bucket: str, key_file: Path):
        from minio import Minio  # imported here so the package is optional without MinIO

        access, secret = key_file.read_text(encoding="utf-8").splitlines()[:2]
        self.client, self.bucket = Minio(endpoint, access_key=access.strip(), secret_key=secret.strip(),
                                         secure=endpoint.startswith("https://")), bucket

    def put(self, key: str, path: Path) -> None:
        self.client.fput_object(self.bucket, key, str(path), content_type="application/pdf")

    def exists(self, key: str) -> bool:
        from minio.error import S3Error

        try:
            self.client.stat_object(self.bucket, key)
        except S3Error:
            return False
        return True


def store_for(cfg: WorkerConfig) -> ObjectStore:
    if cfg.minio_endpoint is None:
        return NullStore()
    if cfg.minio_key_file is None:
        raise RuntimeError("STATEMENT_MINIO_KEY_FILE is required with STATEMENT_MINIO_ENDPOINT")
    return MinioStore(cfg.minio_endpoint, cfg.minio_bucket, cfg.minio_key_file)
