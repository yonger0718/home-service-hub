# tests/integration/test_statement_ingest_api.py
from datetime import date

import pytest

from app import auth

TOKENS = "spa:spa-token,hermes:hermes-token,worker:worker-token"
SCOPES = "spa=legacy; hermes=read,propose,enqueue; worker=ingest"
W = {"Authorization": "Bearer worker-token"}
H = {"Authorization": "Bearer hermes-token"}
S = {"Authorization": "Bearer spa-token"}


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


@pytest.fixture
def card(seed, db_session):
    account = seed.account("卡", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    return account


def _claim(client):
    run = client.post("/statements/ingest/run", headers=H).json()
    claim = client.post(f"/statements/ingest-runs/{run['run_id']}/claim", headers=W)
    assert claim.status_code == 200
    return claim.json()


def _revision(lease, account, lines, total):
    return {"run_id": lease["run_id"], "lease_token": lease["lease_token"], "account_id": account.id, "kind": "card",
            "parser": "test", "parser_version": "1", "currency": "TWD", "period_start": "2026-09-01", "period_end": "2026-09-30",
            "statement_total": total, "lines": lines, "raw": {}}


def test_enqueue_needs_enqueue_scope_and_returns_202(client):
    assert client.post("/statements/ingest/run", headers=W).status_code == 403
    assert client.post("/statements/ingest/run", headers=S).status_code == 202
    assert client.post("/statements/ingest/run", headers=H).status_code == 202


def test_feature_off_hides_routes(client, monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "false")
    assert client.post("/statements/ingest/run", headers=H).status_code == 404


def test_worker_submits_revision_and_owner_reads_it(client, card):
    lease = _claim(client)
    body = _revision(lease, card, [{"seq": 1, "posted_date": "2026-09-03", "merchant_raw": "全聯", "printed_amount": "580", "line_kind": "purchase"}], "580")
    created = client.post("/statements/revisions", headers=W, json=body)
    assert created.status_code == 201, created.text
    out = created.json()
    assert out["guardrail_ok"] is True and out["mode"] == "live" and out["lineage"]["new"] == 1
    listing = client.get(f"/accounts/{card.id}/statements", headers=S)
    assert listing.status_code == 200 and listing.json()[0]["statement_total"] == "580.0000"
    detail = client.get(f"/accounts/{card.id}/statements/{out['statement_id']}", headers=S)
    assert detail.status_code == 200
    assert [l["flow_amount"] for l in detail.json()["lines"]] == ["-580.0000"]
    assert detail.json()["stale_events_pending"] is False


def test_hermes_cannot_submit_revisions(client, card):
    lease = _claim(client)
    assert client.post("/statements/revisions", headers=H, json=_revision(lease, card, [], "0")).status_code == 403


def test_wrong_lease_is_409(client, card):
    lease = _claim(client)
    body = _revision(lease, card, [], "0") | {"lease_token": "x" * 48}
    response = client.post("/statements/revisions", headers=W, json=body)
    assert (response.status_code, response.json()["message"]) == (409, "lease")


def test_folder_map_is_enforced_for_file_backed_revisions(client, card, seed, db_session):
    other = seed.account("另一張卡", is_credit=True)
    db_session.commit()
    assert client.put("/settings/reconciliation", headers=S, json={"account_map": {"mail/信用卡/國泰世華": other.id}}).status_code == 200
    lease = _claim(client)
    registered = client.post("/statements/files", headers=W, json={"run_id": lease["run_id"], "lease_token": lease["lease_token"],
                                                                      "sha256": "a" * 64, "size": 10, "kind": "card", "object_key": "a" * 64})
    assert registered.status_code == 201, registered.text
    file = registered.json()
    source = client.post("/statements/sources", headers=W, json={"run_id": lease["run_id"], "lease_token": lease["lease_token"], "file_id": file["id"],
                                                                 "root": "mail", "drive_file_id": "d1", "drive_path": "信用卡/國泰世華/2026-09.pdf", "drive_md5": "b" * 32, "drive_size": 10})
    assert source.status_code == 201, source.text
    body = _revision(lease, card, [], "0") | {"file_id": file["id"]}
    response = client.post("/statements/revisions", headers=W, json=body)
    assert response.status_code == 422 and response.json()["detail"][0]["loc"][-1] == "account_id"


def test_mark_removed_refuses_empty_without_allow_empty(client):
    lease = _claim(client)
    base = {"run_id": lease["run_id"], "lease_token": lease["lease_token"]}
    refused = client.post("/statements/sources/mark-removed", headers=W, json=base | {"seen_drive_file_ids": []})
    assert refused.status_code == 422 and refused.json()["detail"][0]["loc"][-1] == "seen_drive_file_ids"
    allowed = client.post("/statements/sources/mark-removed", headers=W,
                          json=base | {"seen_drive_file_ids": [], "allow_empty": True})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json() == {"removed": 0}


def test_settings_put_validates_account_map(client, card):
    bad_key = client.put("/settings/reconciliation", headers=S, json={"account_map": {"信用卡": card.id}})
    assert bad_key.status_code == 422 and bad_key.json()["detail"][0]["loc"][-1] == "account_map"
    bad_value = client.put("/settings/reconciliation", headers=S, json={"account_map": {"mail/信用卡/國泰世華": "x"}})
    assert bad_value.status_code == 422
    assert client.put("/settings/reconciliation", headers=H, json={"account_map": {}}).status_code == 403
    first = client.get("/settings/reconciliation", headers=H).json()
    saved = client.put("/settings/reconciliation", headers=S, json={"account_map": {"mail/信用卡/國泰世華": card.id}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["account_map"] == {"mail/信用卡/國泰世華": card.id}
    assert saved.json()["version"] == first["version"] + 1
    assert client.get("/settings/reconciliation", headers=H).json() == saved.json()


def test_worker_run_lifecycle_renew_list_finish(client):
    lease = _claim(client)
    run_id, token = lease["run_id"], lease["lease_token"]
    renewed = client.post(f"/statements/ingest-runs/{run_id}/renew", headers=W, json={"lease_token": token})
    assert renewed.status_code == 200, renewed.text
    assert [run["id"] for run in client.get("/statements/ingest-runs?status=running", headers=W).json()] == [run_id]
    assert client.get("/statements/ingest-runs", headers=H).status_code == 403
    assert client.get("/statements/files?status=new", headers=W).json() == []
    file = client.post("/statements/files", headers=W, json={"run_id": run_id, "lease_token": token, "sha256": "c" * 64,
                                                                "size": 5, "kind": "bank", "object_key": "c" * 64}).json()
    patched = client.patch(f"/statements/files/{file['id']}", headers=W,
                           json={"run_id": run_id, "lease_token": token, "status": "parsed", "pages": 2})
    assert patched.status_code == 200, patched.text
    assert (patched.json()["status"], patched.json()["pages"]) == ("parsed", 2) and patched.json()["parsed_at"]
    assert [f["id"] for f in client.get("/statements/files?status=parsed", headers=W).json()] == [file["id"]]
    mismatch = client.post(f"/statements/ingest-runs/{run_id + 1}/finish", headers=W,
                           json={"run_id": run_id, "lease_token": token, "status": "done"})
    assert mismatch.status_code == 422
    finished = client.post(f"/statements/ingest-runs/{run_id}/finish", headers=W,
                           json={"run_id": run_id, "lease_token": token, "status": "done", "summary": {"files": 0}})
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "done" and finished.json()["summary"]["files"] == 0
    again = client.post(f"/statements/ingest-runs/{run_id}/renew", headers=W, json={"lease_token": token})
    assert (again.status_code, again.json()["message"]) == (409, "lease")


def test_statement_reads_need_read_scope_and_matching_account(client, card, seed, db_session):
    other = seed.account("錢包")
    db_session.commit()
    assert client.get(f"/accounts/{card.id}/statements", headers=W).status_code == 403
    assert client.get(f"/accounts/{card.id}/statements", headers=H).json() == []
    lease = _claim(client)
    statement_id = client.post("/statements/revisions", headers=W, json=_revision(lease, card, [], "0")).json()["statement_id"]
    assert client.get(f"/accounts/{other.id}/statements/{statement_id}", headers=S).status_code == 404
    assert client.get(f"/accounts/{card.id}/statements/{statement_id}", headers=H).status_code == 200
