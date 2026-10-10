import hashlib
import os

import pytest
from minio.error import S3Error

from worker import config, inbox


@pytest.fixture
def cfg(tmp_path):
    return config.load({"STATEMENT_STATE_DIR": str(tmp_path)})


def _staged(tmp_path, data=b"%PDF-1.4 abc"):
    p = tmp_path / "staging" / "x.tmp"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p, hashlib.md5(data).hexdigest(), len(data)


def _mode(path):
    return oct(path.stat().st_mode)[-3:]


def test_publish_is_atomic_and_idempotent(cfg, tmp_path):
    staged, md5, size = _staged(tmp_path)
    box = inbox.Inbox(cfg)
    first = box.publish(staged, expected_md5=md5, expected_size=size)
    assert first.path == cfg.inbox_dir / f"{first.sha256}.pdf" and first.path.read_bytes() == b"%PDF-1.4 abc"
    assert first.object_key == f"by-sha/{first.sha256}.pdf"
    staged2, _, _ = _staged(tmp_path)
    assert box.publish(staged2, expected_md5=md5, expected_size=size).sha256 == first.sha256
    assert list(cfg.staging_dir.iterdir()) == []
    assert [p for p in cfg.inbox_dir.iterdir() if p.name.endswith(".tmp")] == []


def test_md5_or_size_mismatch_refuses(cfg, tmp_path):
    staged, md5, size = _staged(tmp_path)
    with pytest.raises(inbox.VerifyError):
        inbox.Inbox(cfg).publish(staged, expected_md5="00" * 16, expected_size=size)
    assert not staged.exists()
    staged, md5, size = _staged(tmp_path)
    with pytest.raises(inbox.VerifyError):
        inbox.Inbox(cfg).publish(staged, expected_md5=md5, expected_size=size + 1)
    assert not staged.exists()
    assert not cfg.inbox_dir.exists() or not list(cfg.inbox_dir.iterdir())


def test_same_bytes_compares_bytes_not_digests(cfg, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"y" * 10)
    monkeypatch.setattr(inbox, "_digests", lambda path: ("m", "same-digest", 10))  # a forced digest collision
    assert inbox._same_bytes(a, b) is False


def test_private_modes(cfg, tmp_path):
    staged, md5, size = _staged(tmp_path)
    published = inbox.Inbox(cfg).publish(staged, expected_md5=md5, expected_size=size)
    assert oct(cfg.inbox_dir.stat().st_mode)[-3:] == "700" and oct(published.path.stat().st_mode)[-3:] == "600"


def test_ancestor_directories_are_private_under_wide_umask(cfg, tmp_path):
    old = os.umask(0o022)
    try:
        staged, md5, size = _staged(tmp_path)
        inbox.Inbox(cfg).publish(staged, expected_md5=md5, expected_size=size)
    finally:
        os.umask(old)
    assert _mode(cfg.state_dir / "inbox") == "700"
    assert _mode(cfg.inbox_dir) == "700"


def test_source_index_ancestors_are_private(tmp_path):
    old = os.umask(0o022)
    try:
        inbox.SourceIndex(tmp_path / "state" / "sub" / "sources.json").put("id-1", "ab" * 16, "cd" * 32)
    finally:
        os.umask(old)
    assert _mode(tmp_path / "state") == "700" and _mode(tmp_path / "state" / "sub") == "700"


def test_orphan_tmp_files_are_swept(cfg, tmp_path):
    cfg.inbox_dir.mkdir(parents=True)
    orphan = cfg.inbox_dir / ".deadbeef.tmp"
    orphan.write_bytes(b"left by a crash")
    staged, md5, size = _staged(tmp_path)
    inbox.Inbox(cfg).publish(staged, expected_md5=md5, expected_size=size)
    assert not orphan.exists()


def test_collision_with_different_bytes_is_hard(cfg, tmp_path, monkeypatch):
    staged, md5, size = _staged(tmp_path)
    box = inbox.Inbox(cfg)
    published = box.publish(staged, expected_md5=md5, expected_size=size)
    published.path.write_bytes(b"different")  # simulate a corrupted inbox object under the same name
    staged, md5, size = _staged(tmp_path)
    with pytest.raises(inbox.CollisionError):
        box.publish(staged, expected_md5=md5, expected_size=size)
    assert published.path.read_bytes() == b"different"
    assert staged.exists()  # kept for inspection


def test_source_index_round_trip(cfg):
    index = inbox.SourceIndex(cfg.state_dir / "sources.json")
    assert index.get("id-1", "ab" * 16) is None
    index.put("id-1", "ab" * 16, "cd" * 32)
    assert inbox.SourceIndex(cfg.state_dir / "sources.json").get("id-1", "ab" * 16) == "cd" * 32
    assert oct((cfg.state_dir / "sources.json").stat().st_mode)[-3:] == "600"


@pytest.mark.parametrize("content", ["{trunc", "[]", '{"id-1:' + "ab" * 16 + '": 5}'])
def test_source_index_tolerates_bad_file(tmp_path, content):
    path = tmp_path / "sources.json"
    path.write_text(content, encoding="utf-8")
    index = inbox.SourceIndex(path)
    assert index.get("id-1", "ab" * 16) is None
    index.put("id-1", "ab" * 16, "cd" * 32)
    assert inbox.SourceIndex(path).get("id-1", "ab" * 16) == "cd" * 32


def test_null_store_when_minio_unset(cfg):
    store = inbox.store_for(cfg)
    assert isinstance(store, inbox.NullStore) and store.exists("by-sha/x.pdf")


class StubMinio:
    instances: list = []

    def __init__(self, endpoint, access_key, secret_key, secure):
        self.args = (endpoint, access_key, secret_key, secure)
        self.stat_error: Exception | None = None
        StubMinio.instances.append(self)

    def stat_object(self, bucket, key):
        if self.stat_error is not None:
            raise self.stat_error
        return object()


@pytest.fixture
def stub_minio(monkeypatch):
    StubMinio.instances = []
    monkeypatch.setattr("minio.Minio", StubMinio)
    return StubMinio


def _key_file(tmp_path, text="AKID\nSECRET\n"):
    path = tmp_path / "minio.key"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("endpoint, host, secure", [
    ("minio.internal:9000", "minio.internal:9000", False),
    ("http://minio.internal:9000", "minio.internal:9000", False),
    ("https://minio.internal:9443/", "minio.internal:9443", True),
])
def test_minio_endpoint_forms(stub_minio, tmp_path, endpoint, host, secure):
    inbox.MinioStore(endpoint, "homehub-statements", _key_file(tmp_path))
    assert stub_minio.instances[0].args == (host, "AKID", "SECRET", secure)


def test_minio_rejects_unknown_scheme(stub_minio, tmp_path):
    with pytest.raises(ValueError):
        inbox.MinioStore("ftp://minio:9000", "b", _key_file(tmp_path))


def test_minio_key_file_needs_two_lines(stub_minio, tmp_path):
    with pytest.raises(RuntimeError, match="minio key file needs two lines"):
        inbox.MinioStore("minio:9000", "b", _key_file(tmp_path, "AKID\n"))


def test_minio_exists_maps_missing_codes_to_false(stub_minio, tmp_path):
    store = inbox.MinioStore("minio:9000", "b", _key_file(tmp_path))
    client = stub_minio.instances[0]
    assert store.exists("by-sha/x.pdf") is True
    for code in ("NoSuchKey", "NoSuchObject"):
        client.stat_error = S3Error(code, "missing", "/b/k", "rid", "hid", None)
        assert store.exists("by-sha/x.pdf") is False


@pytest.mark.parametrize("code", ["AccessDenied", "NoSuchBucket"])
def test_minio_exists_reraises_other_codes(stub_minio, tmp_path, code):
    store = inbox.MinioStore("minio:9000", "b", _key_file(tmp_path))
    stub_minio.instances[0].stat_error = S3Error(code, "nope", "/b/k", "rid", "hid", None)
    with pytest.raises(S3Error):
        store.exists("by-sha/x.pdf")


def test_store_for_requires_key_file_when_endpoint_set(cfg):
    cfg_with_endpoint = config.load({"STATEMENT_STATE_DIR": str(cfg.state_dir),
                                     "STATEMENT_MINIO_ENDPOINT": "minio:9000"})
    with pytest.raises(RuntimeError, match="STATEMENT_MINIO_KEY_FILE"):
        inbox.store_for(cfg_with_endpoint)


def test_store_for_builds_minio_store_when_configured(stub_minio, cfg, tmp_path):
    cfg_minio = config.load({"STATEMENT_STATE_DIR": str(cfg.state_dir), "STATEMENT_MINIO_ENDPOINT": "minio:9000",
                             "STATEMENT_MINIO_KEY_FILE": str(_key_file(tmp_path))})
    assert isinstance(inbox.store_for(cfg_minio), inbox.MinioStore)
