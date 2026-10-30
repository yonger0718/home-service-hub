"""Reconciliation routes (R1c Task 8): case detail, owner actions, dismiss, revert, action and audit listings.
Synthetic data only."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text

from app import auth
from app.models import AccountStatement, LedgerEntry, ReconciliationAudit, ReconciliationCase, StatementCoverage
from app.services import coverage_service, settings_service
from app.services import reconciliation_service as rec
from tests.helpers import set_dirty
from tests.integration.settlement_fixtures import (  # noqa: F401  (fixtures)
    feed, portfolio_stub, settle_line, settlement_bank, stand_ins_on, submit_bank,
)
from tests.integration.test_settlement_reconcile import _cases as _settlement_cases
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    OCT, _card, _cases, _open_beside_gap, _line, _reconcile, _submit, run,
)

TOKENS = "spa:spa-token,hermes:hermes-token,worker:worker-token"
SCOPES = "spa=legacy; hermes=read,propose,enqueue; worker=ingest"
S = {"Authorization": "Bearer spa-token"}
H = {"Authorization": "Bearer hermes-token"}
W = {"Authorization": "Bearer worker-token"}
DAY = date(2026, 9, 3)


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


@pytest.fixture
def unmatched(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "神秘", 77)])
    (case,) = _open_beside_gap(db_session, stmt.id, "-77.0000")
    return card, stmt, line, case


@pytest.fixture
def stray(seed, db_session, run):
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=DAY, merchant="全聯")
    entry = seed.entry(card, "-77", day=date(2026, 9, 10), merchant="早餐")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _open_beside_gap(db_session, stmt.id, "77.0000")
    return card, stmt, entry, case


def test_case_detail_line_unmatched(client, unmatched):
    _, stmt, line, case = unmatched
    assert client.get(f"/reconciliation/cases/{case.id}", headers=W).status_code == 403
    body = client.get(f"/reconciliation/cases/{case.id}", headers=H).json()
    assert body["id"] == case.id and body["statement_id"] == stmt.id
    assert body["revision_id"] == stmt.current_revision_id
    assert body["line"]["id"] == line.id and body["line"]["matched"] is False
    assert body["pending_proposals"] == []
    assert body["resolved_by"] is None and body["note"] is None
    assert set(body["actions_allowed"]) >= {"dismiss", "create_entry"}


def test_case_detail_names_the_owner_twins_the_policy_left_the_line_for(client, seed, db_session, run):
    """Widened twin guard (2026-10-17): the detail of an open line_unmatched lists the owner entries that may record
    the line (here a near-amount one the matcher opened no case for), so the owner sees why the policy skipped it
    and which entry to look at. Nothing is stored."""
    card = _card(seed, db_session)
    near = seed.entry(card, "-197.9931", day=DAY - timedelta(days=1))
    far = seed.entry(card, "-150", day=DAY)
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "JPN", 199)])
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched"]
    body = client.get(f"/reconciliation/cases/{case.id}", headers=H).json()
    assert body["owner_twins"] == [near.id] and far.id not in body["owner_twins"]


def test_case_detail_lists_child_and_split_twins_too(client, seed, db_session, run):
    card = _card(seed, db_session)
    parent = seed.entry(card, "-310", day=DAY, merchant="USA")
    group = seed.group("split")
    a = seed.entry(card, "-986", day=DAY, group_id=group.id)
    b = seed.entry(card, "-2958", kind="receivable", day=DAY, group_id=group.id)
    seed.entry(card, "-59", kind="fee", day=DAY, group_id=group.id)  # printed as its own fee line (not here)
    db_session.flush()
    child = seed.entry(card, "-4.7401", kind="fee", day=DAY, parent_entry_id=parent.id)
    db_session.commit()
    # MYS prints 1 % over the owner's expense + receivable (an FX fee the TWD estimate missed): since 2026-10-19 an
    # exact 3944 would be claimed by the group; the near one stays open and its members are the twins
    stmt, (purchase, fee, mys), _ = _submit(db_session, run, card, [
        _line(1, DAY, "USA", 310), _line(2, DAY, "國外交易服務費", 5, "fee"), _line(3, DAY, "MYS", 3983)])
    cases = {c.line_id: c for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched"}
    assert client.get(f"/reconciliation/cases/{cases[fee.id].id}", headers=H).json()["owner_twins"] == [child.id]
    assert client.get(f"/reconciliation/cases/{cases[mys.id].id}", headers=H).json()["owner_twins"] == sorted([a.id, b.id])


def test_case_detail_owner_twins_is_empty_without_a_twin(client, unmatched):
    _, _, _, case = unmatched
    assert client.get(f"/reconciliation/cases/{case.id}", headers=H).json()["owner_twins"] == []


def test_case_detail_entry_unmatched_has_no_line_and_other_actions(client, stray):
    _, _, entry, case = stray
    body = client.get(f"/reconciliation/cases/{case.id}", headers=S).json()
    assert body["line"] is None and body["entry_id"] == entry.id
    assert "dismiss" in body["actions_allowed"] and "create_entry" not in body["actions_allowed"]


def test_case_detail_unknown_is_404(client):
    assert client.get("/reconciliation/cases/999999", headers=S).status_code == 404


def test_dismiss_then_replay_then_revert(client, db_session, unmatched):
    _, stmt, _, case = unmatched
    url = f"/reconciliation/cases/{case.id}/dismiss"
    assert client.post(url, headers=H, json={"note": "x"}).status_code == 403
    assert client.post(url, headers=W, json={"note": "x"}).status_code == 403
    first = client.post(url, headers={**S, "x-request-id": "req-abc"}, json={"note": "not mine"})
    assert first.status_code == 201, first.text
    row = first.json()
    assert row["action"] == "dismiss" and row["actor"] == "owner" and row["replayed"] is False
    detail = client.get(f"/reconciliation/cases/{case.id}", headers=S).json()
    assert detail["status"] == "dismissed" and detail["resolved_by"] == "owner"
    assert detail["resolved_action_id"] == row["id"] and detail["actions_allowed"] == []
    # revert
    assert client.post(f"/reconciliation/actions/{row['id']}/revert", headers=H).status_code == 403
    rev = client.post(f"/reconciliation/actions/{row['id']}/revert", headers=S)
    assert rev.status_code == 201, rev.text
    assert rev.json()["id"] != row["id"]
    assert client.get(f"/reconciliation/cases/{case.id}", headers=S).json()["status"] == "open"
    assert client.post("/reconciliation/actions/999999/revert", headers=S).status_code == 404
    # request id in the audit row
    db_session.expire_all()
    audits = db_session.execute(select(ReconciliationAudit).where(ReconciliationAudit.action_id == row["id"])).scalars().all()
    assert any(a.request_id == "req-abc" for a in audits)


def test_action_replay_returns_200_with_replayed_flag(client, stray):
    _, _, entry, case = stray
    url = f"/reconciliation/cases/{case.id}/actions"
    body = {"action": "defer_next_period", "params": {"entry_id": entry.id}}
    first = client.post(url, headers=S, json=body)
    assert first.status_code == 201 and first.json()["replayed"] is False, first.text
    second = client.post(url, headers=S, json=body)
    assert second.status_code == 200, second.text
    assert second.json()["replayed"] is True and second.json()["id"] == first.json()["id"]


def test_action_errors(client, unmatched):
    _, _, _, case = unmatched
    assert client.post("/reconciliation/cases/999999/actions", headers=S,
                       json={"action": "dismiss", "params": {"note": "x"}}).status_code == 404
    assert client.post("/reconciliation/cases/999999/dismiss", headers=S, json={"note": "x"}).status_code == 404
    bad = client.post(f"/reconciliation/cases/{case.id}/actions", headers=S, json={"action": "nope", "params": {}})
    assert bad.status_code == 422
    for who in (H, W):
        assert client.post(f"/reconciliation/cases/{case.id}/actions", headers=who,
                           json={"action": "dismiss", "params": {"note": "x"}}).status_code == 403


def test_action_and_audit_listings(client, db_session, unmatched):
    _, stmt, _, case = unmatched
    assert client.get("/reconciliation/actions", headers=W).status_code == 403
    assert client.get("/reconciliation/audit", headers=W).status_code == 403
    row = client.post(f"/reconciliation/cases/{case.id}/dismiss", headers=S, json={"note": "n"}).json()
    client.post(f"/reconciliation/actions/{row['id']}/revert", headers=S)
    by_case = client.get(f"/reconciliation/actions?case_id={case.id}", headers=H).json()
    assert [a["id"] for a in by_case][0] != row["id"] or len(by_case) == 2
    assert row["id"] in {a["id"] for a in by_case} and len(by_case) == 2
    by_stmt = client.get(f"/reconciliation/actions?statement_id={stmt.id}", headers=H).json()
    assert {a["id"] for a in by_stmt} == {a["id"] for a in by_case}
    other = client.get(f"/reconciliation/actions?statement_id={stmt.id + 1000}", headers=H).json()
    assert other == []
    assert len(client.get("/reconciliation/actions?limit=1", headers=H).json()) == 1
    audit = client.get(f"/reconciliation/audit?statement_id={stmt.id}", headers=H).json()
    ids = [a["id"] for a in audit]
    assert ids == sorted(ids, reverse=True) and len(ids) >= 2  # newest first, as the actions listing
    assert [a["id"] for a in client.get(f"/reconciliation/audit?statement_id={stmt.id}&limit=1",
                                        headers=H).json()] == ids[:1]
    assert client.get("/reconciliation/audit?limit=0", headers=H).status_code == 422
    # #201: since / event / offset narrow the page; newest first throughout
    newest = client.get("/reconciliation/audit?limit=1", headers=H).json()[0]
    assert [a["id"] for a in client.get("/reconciliation/audit", params={"since": newest["created_at"]},
                                        headers=H).json()] == [newest["id"]]
    assert client.get("/reconciliation/audit?since=2999-01-01T00:00:00Z", headers=H).json() == []
    by_event = client.get(f"/reconciliation/audit?event={newest['event']}", headers=H).json()
    assert by_event and {a["event"] for a in by_event} == {newest["event"]}
    assert client.get("/reconciliation/audit?event=no_such_event", headers=H).json() == []
    page = [a["id"] for a in client.get("/reconciliation/audit?limit=50", headers=H).json()]
    assert [a["id"] for a in client.get("/reconciliation/audit?limit=1&offset=1", headers=H).json()] == page[1:2]
    assert client.get("/reconciliation/audit?offset=-1", headers=H).status_code == 422
    assert client.get("/reconciliation/audit?since=yesterday", headers=H).status_code == 422


def _two_statements(seed, db_session, run):
    card = _card(seed, db_session)
    sep, _, _ = _submit(db_session, run, card, [_line(1, DAY, "甲", 10)])
    octo, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 3), "乙", 20)], period=OCT)
    return card, sep, octo


def _defer(db_session, sep, octo, *, processed):
    (case,) = _open_beside_gap(db_session, sep.id, "-10.0000")
    ctx = '{"resolved_in_statement_id": 1}' if processed else "{}"
    db_session.execute(text(
        "UPDATE reconciliation_case SET status='resolved', explanation='deferred_next_period', "
        "deferred_to_statement_id=:t, context=CAST(:c AS jsonb) WHERE id=:i"), {"t": octo.id, "c": ctx, "i": case.id})
    db_session.commit()


def test_incoming_deferrals_pending_on_both_get_shapes(client, seed, db_session, run):
    card, sep, octo = _two_statements(seed, db_session, run)

    def counts():
        listing = {s["id"]: s for s in client.get(f"/accounts/{card.id}/statements", headers=S).json()}
        detail = client.get(f"/accounts/{card.id}/statements/{octo.id}", headers=S).json()
        assert listing[octo.id]["incoming_deferrals_pending"] == detail["incoming_deferrals_pending"]
        return detail["incoming_deferrals_pending"], listing[sep.id]["incoming_deferrals_pending"]

    assert counts() == (0, 0)
    _defer(db_session, sep, octo, processed=False)
    assert counts() == (1, 0)
    db_session.execute(text("UPDATE reconciliation_case SET context=CAST('{\"resolved_in_statement_id\": 5}' AS jsonb) "
                            "WHERE deferred_to_statement_id=:t"), {"t": octo.id})
    db_session.commit()
    assert counts() == (0, 0)


# --- ruling 36: coverage-only effect slots are per case id and version (Multica AGENT-107) ------------------------

def test_a_match_on_the_replacement_case_after_a_dirty_edit_claims_again(client, seed, db_session, run):
    """An owner sibling match, a dirty merchant edit on the claimed entry, then a reconcile: the sweep releases the
    claim and reopens the case, and `_write_cases` replaces it with a NEW case (version 1) on the same event. A match
    on the replacement is a new effect (slot `c<case.id>v<version>`), not a duplicate of the old case's v1 match; a
    second match with other params on the same case version still is."""
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (old,) = _open_beside_gap(db_session, stmt.id, "-580.0000")  # a sibling's entry never fills the card balance
    sibling = seed.account("姊妹")
    theirs = seed.entry(sibling, "-580", day=DAY, merchant="全聯")
    other = seed.entry(sibling, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    settings_service.update_reconciliation_settings(db_session, {"account_siblings": {str(card.id): [sibling.id]}})
    db_session.commit()
    set_dirty(db_session, True)  # after the settings write, which replaces the settings document

    def match(case_id, entry_id):
        return client.post(f"/reconciliation/cases/{case_id}/actions", headers=S, json={
            "action": "match", "params": {"line_id": line.id, "entry_id": entry_id, "allow_sibling": True}})

    first = match(old.id, theirs.id)
    assert first.status_code == 201, first.text

    db_session.expire_all()
    db_session.get(LedgerEntry, theirs.id).posted_date = DAY + timedelta(days=1)  # a structural (dirty) edit
    db_session.commit()
    _reconcile(db_session, stmt.id)

    db_session.expire_all()
    assert db_session.get(ReconciliationCase, old.id).status == "superseded"
    (new,) = [c for c in _cases(db_session, stmt.id, "open") if c.event_id == line.event_id]
    assert (new.id != old.id, new.version, new.event_id) == (True, 1, old.event_id)

    again = match(new.id, theirs.id)
    assert again.status_code == 201, again.text
    assert again.json()["replayed"] is False
    assert (first.json()["effect_slot"], again.json()["effect_slot"]) == (f"c{old.id}v1", f"c{new.id}v1")
    db_session.expire_all()
    active = db_session.execute(select(StatementCoverage).where(StatementCoverage.statement_id == stmt.id,
                                                                StatementCoverage.status == "active")).scalars().all()
    assert [(c.line_id, c.entry_id, c.match_kind) for c in active] == [(line.id, theirs.id, "manual")]

    # same case, same version, other params: still one effect per slot
    locked = rec._lock_statement(db_session, stmt.id)
    coverage_service.release_line(db_session, locked, line.id, reason="test")
    db_session.get(ReconciliationCase, new.id).status = "open"  # open again WITHOUT a version bump
    db_session.commit()
    duplicate = match(new.id, other.id)
    assert duplicate.status_code == 422 and "duplicate_effect" in duplicate.text, duplicate.text


# ---- settlement re-reconcile triggers (R7 Task 10) ----
def test_settlement_run_books_flags_and_reports_counts_only(client, seed, db_session, run, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])  # feed empty -> settlement_pending
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    res = client.post("/reconciliation/settlements/run", headers=W, json={"source": "portfolio:cathay"})
    assert res.status_code == 200 and (res.json()["status"], res.json()["booked"], res.json()["flagged"]) == ("completed", 1, 1)
    assert "12305" not in res.text and db_session.get(AccountStatement, sid).needs_recheck is True


def test_settlement_run_needs_the_ingest_scope(client):
    assert client.post("/reconciliation/settlements/run", headers=H, json={"source": "portfolio:cathay"}).status_code == 403
    assert client.post("/reconciliation/settlements/run", headers=W, json={"source": "other"}).status_code == 422


def test_sweep_rechecks_statements_with_open_settlement_cases(client, seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])  # pending: nothing dirty, nothing needs_recheck
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    ingest_run, token = run  # the fixture's claimed ingest run holds the lease the sweep needs
    res = client.post("/reconciliation/sweep", headers=W, json={"run_id": ingest_run.id, "lease_token": token})
    assert res.status_code == 200 and res.json()["statements"] == 1
    assert [c.kind for c in _settlement_cases(db_session, sid, "open")] == ["settlement"]
