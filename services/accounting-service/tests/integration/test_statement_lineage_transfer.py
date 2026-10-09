"""Coverage transfer and quarantine across re-parses (design §4.4–4.5, §5.7)."""
from datetime import date
from decimal import Decimal

import pytest

from app.models import (
    LineLineage,
    ReconciliationAction,
    ReconciliationCase,
    ReconciliationProposal,
    StatementCoverage,
    StatementEvent,
    StatementLine,
)
from app.schemas.statements import LineIn, RevisionIn
from app.services import coverage_service as cov
from app.services import statement_ingest_service as svc
from app.services.statements import matching

DAY = date(2026, 9, 3)


def _rev(run, token, account, merchant="全聯", posted=DAY, total="580"):
    line = LineIn(seq=1, posted_date=posted, merchant_raw=merchant, printed_amount=Decimal("580"), line_kind="purchase")
    return RevisionIn(run_id=run.id, lease_token=token, file_id=None, account_id=account.id, kind="card", parser="test",
                      parser_version="1", currency="TWD", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
                      opening_balance=None, statement_total=Decimal(total), lines=[line], raw={})


@pytest.fixture
def card(seed, db_session):
    account = seed.account("卡", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    return account


@pytest.fixture
def old_card(seed, db_session):
    account = seed.account("舊卡", is_credit=True)  # statement_live_from = None -> historical
    db_session.commit()
    return account


@pytest.fixture
def run(db_session):
    r = svc.create_worker_run(db_session, trigger="timer", initiator_hint=None, mode="live")
    r, token = svc.claim_run(db_session, r.id, label="worker")
    db_session.commit()
    return r, token


def _submit(db, run, account, **kw):
    r, token = run
    return svc.submit_revision(db, r, _rev(r, token, account, **kw), account_map={})


def _line_of(db, result) -> StatementLine:
    return db.query(StatementLine).filter_by(revision_id=result.revision.id).one()


def _claimed(db, seed, run, account, merchant="全聯", total="580"):
    """First revision with one line whose coverage claims one -580 entry."""
    first = _submit(db, run, account, merchant=merchant, total=total)
    line = _line_of(db, first)
    entry = seed.entry(account, "-580", day=DAY)
    db.flush()
    db.refresh(entry)
    cov.write_claim(db, first.statement, line, [matching.Row(entry.id, "principal", Decimal("-580"), None)],
                    entries_by_id={entry.id: entry}, match_kind="auto", match_rule="exact", run_id=run[0].id)
    db.flush()
    return first, line, entry


def _coverage(db, statement_id):
    return db.query(StatementCoverage).filter_by(statement_id=statement_id).order_by(StatementCoverage.id).all()


def _parse_reviews(db, statement_id):
    return db.query(ReconciliationCase).filter_by(statement_id=statement_id, kind="parse_review").order_by(
        ReconciliationCase.id).all()


def test_identical_reparse_moves_active_coverage_to_the_new_line(db_session, seed, card, run):
    first, old_line, entry = _claimed(db_session, seed, run, card)
    second = _submit(db_session, run, card, merchant="全聯 ")
    assert second.lineage_counts["identical"] == 1
    db_session.expire_all()
    new_line = _line_of(db_session, second)
    event = db_session.get(StatementEvent, old_line.event_id)
    assert (event.status, event.current_line_id, event.flag) == ("live", new_line.id, None)
    assert new_line.event_id == event.id
    [row] = _coverage(db_session, first.statement.id)
    assert (row.status, row.line_id, row.event_id, row.entry_id) == ("active", new_line.id, event.id, entry.id)
    lineage_row = db_session.query(LineLineage).one()
    assert (lineage_row.equivalence, lineage_row.transferred) == ("identical", True)
    assert _parse_reviews(db_session, first.statement.id) == []
    cov.assert_conserved(db_session, first.statement)
    assert first.statement.matched_count == 1


def test_normalised_reparse_transfers_coverage_flags_the_event_and_supersedes_pending_proposals(
        db_session, seed, card, run):
    first, old_line, _ = _claimed(db_session, seed, run, card, merchant="PAYPAL *Spotify")
    case = ReconciliationCase(statement_id=first.statement.id, revision_id=first.revision.id, kind="amount_delta",
                              event_id=old_line.event_id, line_id=old_line.id)
    db_session.add(case)
    db_session.flush()
    proposal = ReconciliationProposal(case_id=case.id, case_version=1, event_id=old_line.event_id, action="match",
                                      params={}, rationale="r", confidence=Decimal("0.9"), author="agent")
    db_session.add(proposal)
    db_session.flush()
    second = _submit(db_session, run, card, merchant="SPOTIFY PAYPAL")
    assert second.lineage_counts["normalised"] == 1
    db_session.expire_all()
    new_line = _line_of(db_session, second)
    event = db_session.get(StatementEvent, old_line.event_id)
    assert (event.status, event.current_line_id, event.flag) == ("live", new_line.id, "text_changed")
    [row] = _coverage(db_session, first.statement.id)
    assert (row.status, row.line_id) == ("active", new_line.id)
    assert db_session.query(LineLineage).one().transferred is True
    assert db_session.get(ReconciliationProposal, proposal.id).status == "superseded"
    cov.assert_conserved(db_session, first.statement)


def test_identical_reparse_keeps_pending_proposals(db_session, seed, card, run):
    first, old_line, _ = _claimed(db_session, seed, run, card)
    case = ReconciliationCase(statement_id=first.statement.id, revision_id=first.revision.id, kind="amount_delta",
                              event_id=old_line.event_id, line_id=old_line.id)
    db_session.add(case)
    db_session.flush()
    proposal = ReconciliationProposal(case_id=case.id, case_version=1, event_id=old_line.event_id, action="match",
                                      params={}, rationale="r", confidence=Decimal("0.9"), author="agent")
    db_session.add(proposal)
    db_session.flush()
    _submit(db_session, run, card)
    db_session.expire_all()
    assert db_session.get(ReconciliationProposal, proposal.id).status == "pending"


def test_changed_with_coverage_live_quarantines_releases_and_opens_parse_review(db_session, seed, card, run):
    first, old_line, entry = _claimed(db_session, seed, run, card, merchant="PAYPAL *Spotify")
    second = _submit(db_session, run, card, merchant="PAYPAL *Netflix")
    assert second.lineage_counts["changed"] == 1
    db_session.expire_all()
    new_line = _line_of(db_session, second)
    old_event = db_session.get(StatementEvent, old_line.event_id)
    assert (old_event.status, old_event.current_line_id) == ("quarantined", None)
    new_event = db_session.get(StatementEvent, new_line.event_id)
    assert new_event.id != old_event.id and new_event.status == "live"
    [row] = _coverage(db_session, first.statement.id)
    assert (row.status, row.stale_reason, row.line_id) == ("stale", "lineage:changed", old_line.id)
    assert entry.id not in cov.claimed_entry_ids(db_session)
    [case] = _parse_reviews(db_session, first.statement.id)
    assert (case.status, case.event_id, case.revision_id, case.version) == ("open", old_event.id, second.revision.id, 1)
    assert case.context == {"quarantined_event_id": old_event.id, "equivalence": "changed",
                            "old_line_id": old_line.id, "new_line_id": new_line.id}
    assert case.id in second.case_ids
    lineage_row = db_session.query(LineLineage).one()
    assert (lineage_row.equivalence, lineage_row.transferred) == ("changed", False)
    assert first.statement.open_case_count == 1 and first.statement.matched_count == 0


def test_quarantine_reuses_an_open_parse_review_for_the_same_event(db_session, seed, card, run):
    # A quarantined event's line leaves the current revision, so pairing alone cannot quarantine it twice; an open
    # parse_review on the event (e.g. a resolution that re-linked it) must be reused, not duplicated.
    first, old_line, _ = _claimed(db_session, seed, run, card, merchant="PAYPAL *Spotify")
    prior = ReconciliationCase(statement_id=first.statement.id, revision_id=first.revision.id, kind="parse_review",
                               event_id=old_line.event_id, line_id=old_line.id,
                               context={"quarantined_event_id": old_line.event_id, "equivalence": "changed"})
    db_session.add(prior)
    db_session.flush()
    second = _submit(db_session, run, card, merchant="PAYPAL *Netflix")
    db_session.expire_all()
    new_line = _line_of(db_session, second)
    [case] = _parse_reviews(db_session, first.statement.id)
    assert (case.id, case.status, case.version) == (prior.id, "open", 2)
    assert case.context["new_line_id"] == new_line.id and case.context["quarantined_event_id"] == old_line.event_id
    assert first.statement.open_case_count == 1


def test_changed_with_coverage_historical_quarantines_and_releases_without_a_case(db_session, seed, old_card, run):
    first, old_line, _ = _claimed(db_session, seed, run, old_card, merchant="PAYPAL *Spotify")
    assert first.statement.mode == "historical"
    second = _submit(db_session, run, old_card, merchant="PAYPAL *Netflix")
    db_session.expire_all()
    old_event = db_session.get(StatementEvent, old_line.event_id)
    assert (old_event.status, old_event.current_line_id) == ("quarantined", None)
    [row] = _coverage(db_session, first.statement.id)
    assert (row.status, row.stale_reason) == ("stale", "lineage:changed")
    assert db_session.query(ReconciliationCase).count() == 0 and second.case_ids == []


def test_changed_without_coverage_retires_the_old_event(db_session, card, run):
    first = _submit(db_session, run, card, merchant="PAYPAL *Spotify")
    old_line = _line_of(db_session, first)
    _submit(db_session, run, card, merchant="PAYPAL *Netflix")
    db_session.expire_all()
    old_event = db_session.get(StatementEvent, old_line.event_id)
    assert (old_event.status, old_event.current_line_id, old_event.flag) == ("retired", None, None)
    assert _parse_reviews(db_session, first.statement.id) == []


def test_changed_with_an_applied_action_quarantines_without_coverage(db_session, card, run):
    first = _submit(db_session, run, card, merchant="PAYPAL *Spotify")
    old_line = _line_of(db_session, first)
    case = ReconciliationCase(statement_id=first.statement.id, revision_id=first.revision.id, kind="line_unmatched",
                              event_id=old_line.event_id, line_id=old_line.id, status="resolved")
    db_session.add(case)
    db_session.flush()
    db_session.add(ReconciliationAction(idempotency_key="k1", event_id=old_line.event_id, effect_slot="create",
                                        case_id=case.id, action="create_entry", params={}, actor="owner",
                                        status="applied"))
    db_session.flush()
    _submit(db_session, run, card, merchant="PAYPAL *Netflix")
    db_session.expire_all()
    assert db_session.get(StatementEvent, old_line.event_id).status == "quarantined"
    [review] = _parse_reviews(db_session, first.statement.id)
    assert review.context["quarantined_event_id"] == old_line.event_id


def test_unpaired_old_with_coverage_is_quarantined(db_session, seed, card, run):
    first, old_line, _ = _claimed(db_session, seed, run, card)
    second = _submit(db_session, run, card, posted=date(2026, 9, 4))
    assert second.lineage_counts["unpaired_old"] == 1
    db_session.expire_all()
    old_event = db_session.get(StatementEvent, old_line.event_id)
    assert (old_event.status, old_event.current_line_id) == ("quarantined", None)
    [row] = _coverage(db_session, first.statement.id)
    assert (row.status, row.stale_reason) == ("stale", "lineage:unpaired")
    [case] = _parse_reviews(db_session, first.statement.id)
    assert case.context == {"quarantined_event_id": old_event.id, "equivalence": "unpaired",
                            "old_line_id": old_line.id, "new_line_id": None}


@pytest.mark.parametrize("merchant", ["全聯", "其他"])
def test_non_current_revision_transfers_and_quarantines_nothing(db_session, seed, card, run, merchant):
    first, old_line, _ = _claimed(db_session, seed, run, card)
    bad = _submit(db_session, run, card, merchant=merchant, total="581")  # guardrail failure: not current
    assert bad.revision.guardrail_ok is False and first.statement.current_revision_id == first.revision.id
    db_session.expire_all()
    event = db_session.get(StatementEvent, old_line.event_id)
    assert (event.status, event.current_line_id, event.flag) == ("live", old_line.id, None)
    [row] = _coverage(db_session, first.statement.id)
    assert (row.status, row.line_id) == ("active", old_line.id)
    assert all(not r.transferred for r in db_session.query(LineLineage).all())
    assert all("quarantined_event_id" not in c.context for c in _parse_reviews(db_session, first.statement.id))


def test_correction_keeps_the_quarantine_case_open(db_session, seed, card, run):
    # A failed first revision is current; the passing correction supersedes the guardrail parse_review but must not
    # close the parse_review it opens for the quarantined event.
    first, old_line, _ = _claimed(db_session, seed, run, card, merchant="PAYPAL *Spotify", total="999")
    [guardrail_case] = _parse_reviews(db_session, first.statement.id)
    second = _submit(db_session, run, card, merchant="PAYPAL *Netflix")
    assert first.statement.current_revision_id == second.revision.id
    db_session.expire_all()
    cases = {c.id: c for c in _parse_reviews(db_session, first.statement.id)}
    assert cases.pop(guardrail_case.id).status == "superseded"
    [quarantine] = cases.values()
    assert quarantine.status == "open" and quarantine.event_id == old_line.event_id
    assert db_session.get(StatementEvent, old_line.event_id).status == "quarantined"
