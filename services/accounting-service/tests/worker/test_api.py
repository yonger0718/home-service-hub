import threading
import time

import pytest

from app import auth
from worker import api
from tests.integration.test_statement_ingest_api import SCOPES, TOKENS  # the same token fixtures

W_TOKEN = "worker-token"


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


@pytest.fixture
def worker_api(client):
    return api.ApiClient(client, W_TOKEN)


def test_run_lifecycle(worker_api):
    lease = worker_api.create_run("owner_cli", "live", "tester")
    assert lease.run_id > 0 and len(lease.lease_token) >= 16 and lease.attempt == 1
    worker_api.renew(lease)
    file = worker_api.register_file(lease, "ab" * 32, 10, "card", "by-sha/" + "ab" * 32 + ".pdf")
    assert worker_api.register_file(lease, "AB" * 32, 10, "card", "by-sha/x.pdf")["id"] == file["id"]
    src = worker_api.register_source(lease, file["id"], "mail", "id-1", "信用卡/國泰世華/2026-09.pdf", "cd" * 16, 10)
    assert src["drive_file_id"] == "id-1"
    assert worker_api.mark_removed(lease, ["id-1"]) == 0
    worker_api.update_file(lease, file["id"], status="failed", failure="password", credential_version="v1")
    assert [f["id"] for f in worker_api.list_files()] == [file["id"]]
    assert worker_api.list_files()[0]["credential_version"] == "v1"
    assert worker_api.sweep(lease)["statements"] == 0
    worker_api.finish(lease, "done", {"files": 1})
    with pytest.raises(api.LeaseLost):
        worker_api.renew(lease)


def test_lease_lost_on_submission_with_a_stale_token(worker_api):
    lease = worker_api.create_run("owner_cli", "live", "tester")
    stale = api.Lease(lease.run_id, "t" * 32, lease.lease_expires_at, lease.attempt)
    with pytest.raises(api.LeaseLost):
        worker_api.register_file(stale, "cd" * 32, 1, "card", "by-sha/y.pdf")


def test_account_map(worker_api, client):
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": 3}, "dirty_enabled": False, "rules": {}})
    mapping, version = worker_api.account_map()
    assert mapping == {"mail/信用卡/國泰世華": 3} and len(version) == 16


def test_guard_blocks_lease_routes_before_sending(client):
    lost = {"v": False}
    guarded = api.ApiClient(client, W_TOKEN, guard=lambda: lost["v"])
    lease = guarded.create_run("owner_cli", "live", "tester")
    lost["v"] = True
    with pytest.raises(api.LeaseLost):
        guarded.register_file(lease, "ab" * 32, 1, "card", "by-sha/x.pdf")
    assert guarded.queued_runs() == []  # non-lease reads still work


def test_run_state_round_trip(tmp_path, worker_api):
    lease = worker_api.create_run("owner_cli", "live", "tester")
    state = api.RunState(tmp_path / "run.json")
    state.save(lease, trigger="owner_cli", mode="live")
    assert oct((tmp_path / "run.json").stat().st_mode)[-3:] == "600"
    assert state.load() == (lease, "owner_cli", "live")
    (tmp_path / "run.json").write_text("{trunc")
    assert state.load() is None and not (tmp_path / "run.json").exists()
    state.clear()
    assert state.load() is None


def test_lease_keeper_flags_transport_errors(worker_api, monkeypatch):
    lease = worker_api.create_run("owner_cli", "live", "tester")

    def boom(l, **kw):
        raise ConnectionError("down")

    monkeypatch.setattr(worker_api, "renew", boom)
    with api.LeaseKeeper(worker_api, lease, interval_s=0.05) as keeper:
        time.sleep(0.2)
        assert keeper.lost


def test_lease_keeper_renews_and_flags_loss(worker_api, monkeypatch):
    lease = worker_api.create_run("owner_cli", "live", "tester")
    calls = []
    real = worker_api.renew

    def renew(l, **kw):
        calls.append(1)
        return real(l, **kw)

    monkeypatch.setattr(worker_api, "renew", renew)
    with api.LeaseKeeper(worker_api, lease, interval_s=0.05) as keeper:
        time.sleep(0.2)
        assert calls and not keeper.lost
        worker_api.finish(lease, "done", {})
        time.sleep(0.2)
        assert keeper.lost
    assert not any(t.name.startswith("lease-keeper") and t.is_alive() for t in threading.enumerate())
