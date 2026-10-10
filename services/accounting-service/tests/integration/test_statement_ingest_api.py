# tests/integration/test_statement_ingest_api.py
from datetime import date

import pytest
from sqlalchemy import text

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


def test_worker_creates_and_claims_its_own_timer_run(client, card):
    created = client.post("/statements/ingest-runs", headers=W, json={"trigger": "timer", "initiator_hint": "systemd"})
    assert created.status_code == 201, created.text
    lease = created.json()
    assert set(lease) == {"run_id", "lease_token", "lease_expires_at", "attempt"} and lease["attempt"] == 1
    runs = client.get("/statements/ingest-runs?status=claimed", headers=W).json()
    assert [(r["id"], r["trigger"], r["principal"], r["mode"], r["claimed_by"]) for r in runs] == [
        (lease["run_id"], "timer", "worker", "live", "worker")]
    body = _revision(lease, card, [{"seq": 1, "posted_date": "2026-09-03", "merchant_raw": "全聯", "printed_amount": "580",
                                    "line_kind": "purchase"}], "580")
    submitted = client.post("/statements/revisions", headers=W, json=body)
    assert submitted.status_code == 201, submitted.text


def test_worker_run_creation_needs_ingest_and_a_worker_trigger(client):
    assert client.post("/statements/ingest-runs", headers=H, json={"trigger": "timer"}).status_code == 403
    assert client.post("/statements/ingest-runs", headers=W, json={"trigger": "enqueue"}).status_code == 422
    assert client.post("/statements/ingest-runs", headers=W, json={"trigger": "owner_cli", "mode": "replay"}).status_code == 422
    assert client.post("/statements/ingest-runs", headers=W,
                       json={"trigger": "owner_cli", "initiator_hint": "x" * 65}).status_code == 422
    backfill = client.post("/statements/ingest-runs", headers=W, json={"trigger": "owner_cli", "mode": "backfill"})
    assert backfill.status_code == 201, backfill.text


def test_settings_carry_the_dirty_switch(client, card, seed, db_session):
    assert client.get("/settings/reconciliation", headers=H).json()["dirty_enabled"] is False
    assert client.put("/settings/reconciliation", headers=S, json={"dirty_enabled": "maybe"}).status_code == 422
    saved = client.put("/settings/reconciliation", headers=S, json={"dirty_enabled": True})
    assert saved.status_code == 200, saved.text
    assert saved.json()["dirty_enabled"] is True and saved.json()["account_map"] == {}
    assert client.get("/settings/reconciliation", headers=H).json() == saved.json()
    seed.entry(card, "-10")  # the triggers read the switch the PUT stored
    db_session.commit()
    assert db_session.execute(text("SELECT count(*) FROM coverage_dirty")).scalar_one() == 1


def test_duplicate_line_seq_is_422_and_stores_nothing(client, card, db_session):
    lease = _claim(client)
    line = {"posted_date": "2026-09-03", "merchant_raw": "全聯", "printed_amount": "290", "line_kind": "purchase"}
    body = _revision(lease, card, [{"seq": 1, **line}, {"seq": 1, **line, "merchant_raw": "家樂福"}], "580")
    response = client.post("/statements/revisions", headers=W, json=body)
    assert response.status_code == 422 and response.json()["detail"][0]["loc"][-1] == "lines", response.text
    assert "duplicate seq" in response.json()["detail"][0]["msg"]
    counts = db_session.execute(text("SELECT (SELECT count(*) FROM account_statement), (SELECT count(*) FROM statement_revision)")).one()
    assert tuple(counts) == (0, 0)


# ---- reconcile, sweep, cases, coverage read model (r1b task 7) ----
LINE = {"seq": 1, "posted_date": "2026-09-03", "merchant_raw": "全聯", "printed_amount": "580", "line_kind": "purchase"}


def _submit(client, account, lines=None, total="580", lease=None):
    lease = lease or _claim(client)
    created = client.post("/statements/revisions", headers=W, json=_revision(lease, account, [LINE] if lines is None else lines, total))
    assert created.status_code == 201, created.text
    return created.json()["statement_id"], lease


def test_reconcile_route_needs_write_and_the_statements_account(client, card, seed, db_session):
    other = seed.account("錢包")
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    statement_id, _ = _submit(client, card)
    url = f"/accounts/{card.id}/statements/{statement_id}/reconcile"
    assert client.post(url, headers=H).status_code == 403
    assert client.post(url, headers=W).status_code == 403
    assert client.post(f"/accounts/{other.id}/statements/{statement_id}/reconcile", headers=S).status_code == 404
    done = client.post(url, headers=S)
    assert done.status_code == 200, done.text
    assert set(done.json()) == {"claims", "cases_opened", "explained", "unmatched_entries", "skipped"}
    assert done.json()["claims"] == 1 and done.json()["skipped"] is None


def test_detail_shows_coverage_and_matched(client, card, seed, db_session):
    entry = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    statement_id, _ = _submit(client, card, [LINE, {**LINE, "seq": 2, "merchant_raw": "家樂福", "printed_amount": "77"}], "657")
    detail = client.get(f"/accounts/{card.id}/statements/{statement_id}", headers=H).json()
    first, second = detail["lines"]
    assert first["matched"] is True and second["matched"] is False and second["coverage"] == []
    assert [c["entry_id"] for c in first["coverage"]] == [entry.id]
    assert set(first["coverage"][0]) == {"entry_id", "group_id", "role", "flow", "match_rule", "match_kind", "status"}
    assert first["coverage"][0]["status"] == "active"
    assert detail["matched_count"] == 1 and "conflict_open" in detail and "needs_recheck" in detail


def test_stale_events_follow_the_sweep_relevance(client, card, seed, db_session):
    from tests.helpers import set_dirty
    set_dirty(db_session, True)
    statement_id, _ = _submit(client, card)
    assert client.get(f"/accounts/{card.id}/statements/{statement_id}", headers=H).json()["stale_events_pending"] is False
    seed.entry(card, "-5", day=date(2026, 9, 4))
    db_session.commit()
    assert client.get(f"/accounts/{card.id}/statements/{statement_id}", headers=H).json()["stale_events_pending"] is True


def test_sweep_needs_ingest_and_a_lease_and_reports_the_batch(client, card, seed, db_session):
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    statement_id, lease = _submit(client, card)
    lease = {"run_id": lease["run_id"], "lease_token": lease["lease_token"]}
    db_session.execute(text("UPDATE account_statement SET needs_recheck = true"))
    db_session.commit()
    assert client.post("/reconciliation/sweep", headers=H, json=lease).status_code == 403
    assert client.post("/reconciliation/sweep", headers=W, json=lease | {"lease_token": "x" * 48}).status_code == 409
    done = client.post("/reconciliation/sweep", headers=W, json=lease)
    assert done.status_code == 200, done.text
    assert done.json() == {"statements": 1, "claims": 1, "cases_opened": 0, "errors": [], "links_filled": 0}
    assert db_session.execute(text("SELECT needs_recheck FROM account_statement")).scalar_one() is False


def test_sweep_skips_guardrail_failures_and_lists_errors(client, card, seed, db_session, monkeypatch):
    from app.services import reconciliation_service as rec
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    second_card = seed.account("卡二", is_credit=True, statement_live_from=date(2026, 9, 1))
    third_card = seed.account("卡三", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    good, lease = _submit(client, card)
    broken, _ = _submit(client, second_card, lease=lease)
    failing, _ = _submit(client, third_card, [LINE], "999", lease=lease)  # total does not add up: guardrail fails
    lease = {"run_id": lease["run_id"], "lease_token": lease["lease_token"]}
    db_session.execute(text("UPDATE account_statement SET needs_recheck = true"))
    db_session.commit()
    real = rec.reconcile

    def flaky(db, statement_id, **kwargs):
        if statement_id == broken:
            raise RuntimeError("boom")
        return real(db, statement_id, **kwargs)

    monkeypatch.setattr(rec, "reconcile", flaky)
    out = client.post("/reconciliation/sweep", headers=W, json=lease)
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["errors"] == [{"statement_id": broken, "error": "RuntimeError"}]
    assert body["statements"] == 2 and body["claims"] == 1  # the skipped statement is processed, not an error
    assert good != failing


def test_cases_listing_needs_read_and_validates_status(client, card, seed, db_session):
    statement_id, _ = _submit(client, card)  # an unmatched line opens a case
    assert client.get("/reconciliation/cases", headers=W).status_code == 403
    assert client.get("/reconciliation/cases?status=bogus", headers=H).status_code == 422
    listing = client.get("/reconciliation/cases", headers=H)
    assert listing.status_code == 200, listing.text
    cases = listing.json()
    assert cases and cases[0]["status"] == "open" and "candidates" in cases[0]
    assert [c["id"] for c in cases] == sorted((c["id"] for c in cases), reverse=True)
    assert client.get(f"/reconciliation/cases?status=resolved", headers=H).json() == []
    assert client.get(f"/reconciliation/cases?account_id={card.id}&kind=line_unmatched", headers=H).json() == cases
    assert client.get(f"/reconciliation/cases?account_id={card.id + 99}", headers=H).json() == []
    assert client.get("/reconciliation/cases?kind=bogus", headers=H).status_code == 422


def test_settings_expose_rules_and_validate_rule_keys(client, card):
    current = client.get("/settings/reconciliation", headers=H).json()
    assert set(current) == {"account_map", "dirty_enabled", "rules", "rules_version", "version"}
    assert current["rules"]["accept"] == "0.80" and "period_end" not in current["rules"]
    for bad in ({"nope": 1}, {"period_end": "2026-09-30"}, {"deferral_days": "x"}, {"deferral_days": True},
                {"accept": 0.8}, {"bank_only_patterns": "年費"}, {"bank_only_patterns": [1]}):
        refused = client.put("/settings/reconciliation", headers=S, json={"rules": bad})
        assert refused.status_code == 422 and refused.json()["detail"][0]["loc"][-1] == "rules", bad
    for out_of_range in ({"accept": "1.5"}, {"margin": "-0.1"}, {"ambiguous_floor": "2"}, {"near_tolerance_pct": "-1"},
                         {"near_tolerance_abs": "-5"}):
        refused = client.put("/settings/reconciliation", headers=S, json={"rules": out_of_range})
        assert refused.status_code == 422 and refused.json()["detail"][0]["loc"][-1] == "rules", out_of_range
    saved = client.put("/settings/reconciliation", headers=S, json={"rules": {"deferral_days": 3, "accept": "0.90"}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["rules"]["deferral_days"] == 3 and saved.json()["rules"]["accept"] == "0.90"
    assert saved.json()["rules_version"] == f"r1b-{current['version'] + 1}"
    again = client.put("/settings/reconciliation", headers=S, json={"rules": {"deferral_days": 3, "accept": "0.90"}})
    assert again.json()["rules_version"] == saved.json()["rules_version"]  # unchanged rules keep the version
    edges = {"accept": "1", "margin": "0", "ambiguous_floor": "0.5", "near_tolerance_pct": "0", "near_tolerance_abs": "0"}
    assert client.put("/settings/reconciliation", headers=S, json={"rules": edges}).status_code == 200


def test_cases_listing_limit_defaults_to_200_and_caps_at_1000(client, card, seed, db_session):
    _submit(client, card, [{**LINE, "seq": n} for n in (1, 2, 3)], "1740")  # three unmatched lines: three cases
    assert len(client.get("/reconciliation/cases", headers=H).json()) == 3
    assert len(client.get("/reconciliation/cases?limit=2", headers=H).json()) == 2
    newest = client.get("/reconciliation/cases?limit=1", headers=H).json()
    assert newest[0]["id"] == max(c["id"] for c in client.get("/reconciliation/cases", headers=H).json())
    for bad in ("0", "1001", "x"):
        assert client.get(f"/reconciliation/cases?limit={bad}", headers=H).status_code == 422, bad
    assert client.get("/reconciliation/cases?limit=1000", headers=H).status_code == 200


def test_sweep_stops_with_409_lease_when_the_lease_expires_between_items(client, card, seed, db_session, monkeypatch):
    from datetime import timedelta
    from app.services import reconciliation_service as rec
    from app.services import statement_ingest_service as svc
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    second_card = seed.account("卡二", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    first, lease = _submit(client, card)
    second, _ = _submit(client, second_card, lease=lease)
    lease = {"run_id": lease["run_id"], "lease_token": lease["lease_token"]}
    db_session.execute(text("UPDATE account_statement SET needs_recheck = true"))
    db_session.commit()
    real, real_now, done = rec.reconcile, svc._now, []

    def reconcile_then_expire(db, statement_id, **kwargs):
        out = real(db, statement_id, **kwargs)
        done.append(statement_id)
        monkeypatch.setattr(svc, "_now", lambda: real_now() + svc.LEASE + timedelta(minutes=1))
        return out

    monkeypatch.setattr(rec, "reconcile", reconcile_then_expire)
    refused = client.post("/reconciliation/sweep", headers=W, json=lease)
    assert refused.status_code == 409 and refused.json()["message"] == "lease", refused.text
    assert done == [first]  # the second statement was never started
    flags = dict(db_session.execute(text("SELECT id, needs_recheck FROM account_statement")).all())
    assert flags[first] is False and flags[second] is True  # the finished item stays committed


def test_reconcile_and_sweep_routes_report_lock_conflicts(client, card, seed, db_session, pg_engine, monkeypatch):
    """reconcile_busy (a candidate row is locked) and sweep_barrier_busy (a writer holds the dirty barrier past the
    lock timeout) are 409 on the reconcile route and per-statement errors of the sweep route."""
    from app.services import coverage_service
    from tests.helpers import set_dirty
    entry = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    statement_id, lease = _submit(client, card)
    lease = {"run_id": lease["run_id"], "lease_token": lease["lease_token"]}
    url = f"/accounts/{card.id}/statements/{statement_id}/reconcile"
    db_session.execute(text("UPDATE account_statement SET needs_recheck = true"))
    db_session.commit()
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT id FROM ledger_entry WHERE id = :id FOR UPDATE"), {"id": entry.id})
        busy = client.post(url, headers=S)
        swept = client.post("/reconciliation/sweep", headers=W, json=lease)
        holder.rollback()
    assert busy.status_code == 409 and busy.json()["message"].startswith("reconcile_busy"), busy.text
    assert swept.status_code == 200 and swept.json()["errors"] == [{"statement_id": statement_id, "error": "reconcile_busy"}]
    set_dirty(db_session, True)
    monkeypatch.setattr(coverage_service, "BARRIER_LOCK_TIMEOUT", "200ms")
    with pg_engine.connect() as writer:
        writer.execute(text("UPDATE ledger_entry SET name = 'renamed' WHERE id = :id"), {"id": entry.id})
        barrier = client.post(url, headers=S)
        swept = client.post("/reconciliation/sweep", headers=W, json=lease)
        writer.rollback()
    assert barrier.status_code == 409 and barrier.json()["message"].startswith("sweep_barrier_busy"), barrier.text
    assert swept.status_code == 200 and swept.json()["errors"] == [{"statement_id": statement_id, "error": "sweep_barrier_busy"}]
    done = client.post(url, headers=S)
    assert done.status_code == 200 and done.json()["claims"] == 1


def test_read_only_settings_reader_never_inserts(db_session):
    from app.services import settings_service
    db_session.execute(text("SET TRANSACTION READ ONLY"))
    data = settings_service.read_reconciliation_settings(db_session)
    assert data["account_map"] == {} and data["dirty_enabled"] is False and "rules" in data
    with pytest.raises(Exception):
        settings_service.get_reconciliation_settings(db_session)  # INSERT ... ON CONFLICT is refused read-only
    db_session.rollback()


def test_account_map_route_is_ingest_scoped(client):
    put = client.put("/settings/reconciliation", headers=S, json={"account_map": {"mail/信用卡/國泰世華": 1},
                                                                  "dirty_enabled": False, "rules": {}})
    assert put.status_code == 200
    assert client.get("/statements/account-map", headers=H).status_code == 403
    got = client.get("/statements/account-map", headers=W)
    assert got.status_code == 200
    assert got.json()["account_map"] == {"mail/信用卡/國泰世華": 1} and len(got.json()["mapping_version"]) == 16


def test_settings_put_accepts_manual_and_mail_key_shapes_only(client, card):
    ok = client.put("/settings/reconciliation", headers=S, json={"account_map": {"manual/國泰世華": card.id, "mail/信用卡/國泰世華": card.id}})
    assert ok.status_code == 200, ok.text
    for bad in ("manual/a/b", "mail/a", "other/a/b"):
        assert client.put("/settings/reconciliation", headers=S, json={"account_map": {bad: card.id}}).status_code == 422, bad


def test_manual_root_file_folder_map(client, card, seed, db_session):
    other = seed.account("另一張卡", is_credit=True)
    db_session.commit()

    def attempt(mapped_id, sha):
        assert client.put("/settings/reconciliation", headers=S, json={"account_map": {"manual/國泰世華": mapped_id}}).status_code == 200
        lease = _claim(client)
        ids = {"run_id": lease["run_id"], "lease_token": lease["lease_token"]}
        file = client.post("/statements/files", headers=W, json=ids | {"sha256": sha * 64, "size": 10, "kind": "card", "object_key": sha * 64}).json()
        source = client.post("/statements/sources", headers=W, json=ids | {"file_id": file["id"], "root": "manual", "drive_file_id": "m" + sha,
                                                                         "drive_path": "國泰世華/2510.pdf", "drive_md5": "b" * 32, "drive_size": 10})
        assert source.status_code == 201, source.text
        response = client.post("/statements/revisions", headers=W, json=_revision(lease, card, [], "0") | {"file_id": file["id"]})
        assert client.post(f"/statements/ingest-runs/{lease['run_id']}/finish", headers=W, json=ids | {"status": "done", "summary": {}}).status_code == 200
        return response

    assert attempt(card.id, "c").status_code == 201
    wrong = attempt(other.id, "d")
    assert wrong.status_code == 422 and wrong.json()["detail"][0]["loc"][-1] == "account_id"
