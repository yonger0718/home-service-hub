import hashlib

import pytest

from worker import config, inbox


@pytest.fixture
def cfg(tmp_path):
    return config.load({"STATEMENT_STATE_DIR": str(tmp_path)})


def _staged(tmp_path, data=b"%PDF-1.4 abc"):
    p = tmp_path / "staging" / "x.tmp"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p, hashlib.md5(data).hexdigest(), len(data)


def test_publish_is_atomic_and_idempotent(cfg, tmp_path):
    staged, md5, size = _staged(tmp_path)
    box = inbox.Inbox(cfg)
    first = box.publish(staged, expected_md5=md5, expected_size=size)
    assert first.path == cfg.inbox_dir / f"{first.sha256}.pdf" and first.path.read_bytes() == b"%PDF-1.4 abc"
    assert first.object_key == f"by-sha/{first.sha256}.pdf"
    staged2, _, _ = _staged(tmp_path)
    assert box.publish(staged2, expected_md5=md5, expected_size=size).sha256 == first.sha256
    assert not list(cfg.staging_dir.iterdir()) or all(p.suffix != ".tmp" for p in cfg.staging_dir.iterdir())


def test_md5_or_size_mismatch_refuses(cfg, tmp_path):
    staged, md5, size = _staged(tmp_path)
    with pytest.raises(inbox.VerifyError):
        inbox.Inbox(cfg).publish(staged, expected_md5="00" * 16, expected_size=size)
    staged, md5, size = _staged(tmp_path)
    with pytest.raises(inbox.VerifyError):
        inbox.Inbox(cfg).publish(staged, expected_md5=md5, expected_size=size + 1)
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


def test_collision_with_different_bytes_is_hard(cfg, tmp_path, monkeypatch):
    staged, md5, size = _staged(tmp_path)
    box = inbox.Inbox(cfg)
    published = box.publish(staged, expected_md5=md5, expected_size=size)
    published.path.write_bytes(b"different")  # simulate a corrupted inbox object under the same name
    staged, md5, size = _staged(tmp_path)
    with pytest.raises(inbox.CollisionError):
        box.publish(staged, expected_md5=md5, expected_size=size)


def test_source_index_round_trip(cfg):
    index = inbox.SourceIndex(cfg.state_dir / "sources.json")
    assert index.get("id-1", "ab" * 16) is None
    index.put("id-1", "ab" * 16, "cd" * 32)
    assert inbox.SourceIndex(cfg.state_dir / "sources.json").get("id-1", "ab" * 16) == "cd" * 32
    assert oct((cfg.state_dir / "sources.json").stat().st_mode)[-3:] == "600"


def test_null_store_when_minio_unset(cfg):
    store = inbox.store_for(cfg)
    assert isinstance(store, inbox.NullStore) and store.exists("by-sha/x.pdf")
