"""Reward posting: the per-accrual post, the daily job and the manual endpoint (spec 4.4, 5.1, 6; plan P7, P11,
P12)."""

import re
import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app.models import Account, LedgerEntry, Project, RewardAccrual, RewardRule
from app.schemas.writes import EntryUpdateIn, RefundIn
from app.services import entry_write_service as ews
from app.services import reward_bank_wins, reward_engine, reward_post_job, reward_posting, settlement_service
from app.services.entry_write_service import system_category_id, write_rule_links
from app.services.schedule_locks import REWARD_POST_JOB_LOCK_KEY
from tests.helpers import make_account, make_entry, race
from tests.integration.statement_fixtures import _interleave, _submit, run  # noqa: F401  (run is a fixture)
from tests.integration.test_schedule_job import Held

NOV_POST_ON = date(2027, 1, 5)  # Nov window (11-16..12-15) of an after_window rule, offset 1, day 5
DEC_POST_ON = date(2027, 2, 5)


def _card(db, name="測試卡", closing_day=15, **columns):
    return make_account(db, name, closing_day=closing_day, is_credit=True, **columns)


def _rule(db, card, **columns):
    values = dict(account_id=card.id, name="測試規則", method="percent", rate=Decimal("1"), posting="after_window",
                  post_month_offset=1, post_day=5)
    values.update(columns)
    rule = RewardRule(**values)
    db.add(rule)
    db.flush()
    return rule


def _spend(db, card, amount, day, rules=(), **columns):
    entry = make_entry(db, card, f"-{amount}", entry_date=day, **columns)
    write_rule_links(db, entry.id, [r.id for r in rules])
    reward_engine.sync_entry(db, entry.id)
    return entry


def _accrual(db, **where) -> RewardAccrual:
    db.expire_all()
    query = select(RewardAccrual).order_by(RewardAccrual.id)
    for key, value in where.items():
        query = query.where(getattr(RewardAccrual, key) == value)
    rows = list(db.scalars(query))
    assert len(rows) == 1, rows
    return rows[0]


def _rewards(db) -> list[LedgerEntry]:
    db.expire_all()
    return list(db.scalars(select(LedgerEntry).where(LedgerEntry.kind == "reward").order_by(LedgerEntry.id)))


# --- post_accrual ----------------------------------------------------------------------------------------------


_LOCK = re.compile(r"FOR (UPDATE|SHARE|NO KEY UPDATE)")


def test_post_accrual_lock_order(db_session):
    """P12: the source purchase `SELECT ... FOR UPDATE` first (tier 1: the reward row's FK check share-locks it, and
    an owner PUT holds it before the window), then the accrual locks in ONE `SELECT ... IN (...) ORDER BY id FOR
    UPDATE`, before any reward-row insert or accrual update; the purchase row is never updated."""
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    first = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=first.id).id
    db_session.commit()

    captured: list[tuple[str, object]] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, parameters))

    bind = db_session.connection().engine
    event.listen(bind, "before_cursor_execute", capture)
    try:
        assert reward_posting.post_accrual(db_session, accrual_id) == "posted"
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    db_session.commit()

    sql = [s for s, _ in captured]
    accrual_locks = [i for i, s in enumerate(sql) if _LOCK.search(s) and "FROM reward_accrual" in s]
    assert len(accrual_locks) == 1, sql
    lock = sql[accrual_locks[0]]
    assert " IN (" in lock and "ORDER BY reward_accrual.id" in lock, lock
    purchase_locks = [i for i, (s, params) in enumerate(captured) if _LOCK.search(s) and "FROM ledger_entry" in s]
    assert len(purchase_locks) == 1 and purchase_locks[0] < accrual_locks[0], sql
    assert first.id in _values(captured[purchase_locks[0]][1]), captured[purchase_locks[0]]
    writes = [i for i, s in enumerate(sql)
              if s.lstrip().startswith(("INSERT INTO ledger_entry", "UPDATE ledger_entry", "UPDATE reward_accrual"))]
    assert writes and accrual_locks[0] < min(writes), sql
    assert not any(s.lstrip().startswith("UPDATE ledger_entry") for s in sql), sql


def _values(parameters) -> set:
    if isinstance(parameters, dict):
        return set(parameters.values())
    return set(parameters or ())


def test_post_accrual_locked_requires_held_window(db_session):
    """`locked=True` takes no lock and refuses to run unless `lock_windows` covered the window."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual = _accrual(db_session, source_entry_id=spend.id)
    db_session.commit()
    with pytest.raises(RuntimeError):
        reward_posting.post_accrual(db_session, accrual.id, locked=True)
    db_session.rollback()

    reward_engine.lock_windows(db_session, [(accrual.rule_id, accrual.window_start)])
    captured: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    bind = db_session.connection().engine
    event.listen(bind, "before_cursor_execute", capture)
    try:
        assert reward_posting.post_accrual(db_session, accrual.id, locked=True) == "posted"
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert not any(_LOCK.search(s) for s in captured), captured
    assert not any(_LOCK.search(s) and "FROM ledger_entry" in s for s in captured), captured  # no purchase lock
    db_session.commit()


def test_post_creates_reward_row(db_session):
    card = _card(db_session)
    points = make_account(db_session, "測試點數", currency="PTS")
    rule = _rule(db_session, card, name="測試回饋", reward_account_id=points.id)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    db_session.commit()

    assert reward_posting.post_accrual(db_session, accrual_id) == "posted"
    db_session.commit()

    accrual = _accrual(db_session, id=accrual_id)
    [row] = _rewards(db_session)
    assert (accrual.state, accrual.ledger_entry_id, accrual.amount) == ("posted", row.id, Decimal("10.0000"))
    assert (row.account_id, row.currency, row.amount) == (points.id, "PTS", Decimal("10.0000"))
    assert (row.entry_date, row.posted_date) == (NOV_POST_ON, NOV_POST_ON)
    assert (row.reward_source_entry_id, row.reward_rule_id) == (spend.id, rule.id)
    assert (row.source, row.name, row.project_id) == ("rule", "測試回饋", None)
    assert row.category_id == system_category_id(db_session, "reward")
    assert row.needs_review is False


def test_post_without_reward_account_books_on_the_card(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    db_session.commit()
    assert reward_posting.post_accrual(db_session, accrual_id, on=date(2027, 1, 3)) == "posted"
    db_session.commit()
    [row] = _rewards(db_session)
    assert (row.account_id, row.currency, row.entry_date, row.posted_date) == (
        card.id, "TWD", date(2027, 1, 3), date(2027, 1, 3))


def test_post_zero_amount_forfeits_without_row(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("10"))
    _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    late = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=late.id).id
    db_session.commit()

    assert reward_posting.post_accrual(db_session, accrual_id) == "forfeited"
    db_session.commit()
    accrual = _accrual(db_session, id=accrual_id)
    assert (accrual.state, accrual.ledger_entry_id, accrual.amount) == ("forfeited", None, Decimal("0"))
    assert _rewards(db_session) == []


def test_post_twice_is_skipped(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    db_session.commit()
    assert reward_posting.post_accrual(db_session, accrual_id) == "posted"
    db_session.commit()
    assert reward_posting.post_accrual(db_session, accrual_id) == "skipped"
    db_session.commit()
    assert len(_rewards(db_session)) == 1


def test_post_recomputes_before_freezing(db_session):
    """The window is recomputed under the lock, so a stale stored amount is never frozen."""
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual = _accrual(db_session, source_entry_id=spend.id)
    accrual.amount = Decimal("3")  # stale
    db_session.commit()
    assert reward_posting.post_accrual(db_session, accrual.id) == "posted"
    db_session.commit()
    assert _rewards(db_session)[0].amount == Decimal("10.0000")


def test_post_never_commits(db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    db_session.commit()
    reward_posting.post_accrual(db_session, accrual_id)
    db_session.rollback()
    assert _accrual(db_session, id=accrual_id).state == "estimated"
    assert _rewards(db_session) == []


def test_post_missing_accrual_is_not_found(db_session):
    from app.services.errors import NotFoundError

    with pytest.raises(NotFoundError):
        reward_posting.post_accrual(db_session, 9999)


def test_post_project_inheritance(db_session):
    card = _card(db_session)
    rule_project = Project(name="測試專案A")
    purchase_project = Project(name="測試專案B")
    db_session.add_all([rule_project, purchase_project])
    db_session.flush()
    with_project = _rule(db_session, card, name="測試規則A", reward_project_id=rule_project.id)
    without = _rule(db_session, card, name="測試規則B")
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [with_project, without],
                   project_id=purchase_project.id)
    ids = {rule.id: _accrual(db_session, source_entry_id=spend.id, rule_id=rule.id).id
           for rule in (with_project, without)}
    db_session.commit()
    for accrual_id in ids.values():
        assert reward_posting.post_accrual(db_session, accrual_id) == "posted"
    db_session.commit()
    projects = {row.reward_rule_id: row.project_id for row in _rewards(db_session)}
    assert projects == {with_project.id: rule_project.id, without.id: purchase_project.id}


# --- daily job -------------------------------------------------------------------------------------------------


def test_job_posts_due_only(pg_engine, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("10"))
    manual = _rule(db_session, card, name="測試手動", posting="manual")
    due = _spend(db_session, card, 1000, date(2026, 11, 18), [rule, manual])
    capped = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    future = _spend(db_session, card, 1000, date(2026, 12, 20), [rule])  # Dec window, posts 2027-02-05
    db_session.commit()

    report = reward_post_job.run(pg_engine, "manual", today=date(2027, 1, 10))
    assert {k: report[k] for k in ("status", "posted", "forfeited", "skipped", "failed")} == {
        "status": "ok", "posted": 1, "forfeited": 1, "skipped": 0, "failed": 0}
    assert _accrual(db_session, source_entry_id=due.id, rule_id=rule.id).state == "posted"
    assert _accrual(db_session, source_entry_id=due.id, rule_id=manual.id).state == "estimated"
    assert _accrual(db_session, source_entry_id=capped.id).state == "forfeited"
    assert _accrual(db_session, source_entry_id=future.id).state == "estimated"
    [row] = _rewards(db_session)
    assert (row.reward_source_entry_id, row.entry_date) == (due.id, NOV_POST_ON)

    again = reward_post_job.run(pg_engine, "manual", today=date(2027, 1, 10))
    assert (again["posted"], again["forfeited"], again["skipped"]) == (0, 0, 0)  # nothing due is left
    assert len(_rewards(db_session)) == 1


def test_job_busy_when_lock_held(pg_engine, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    db_session.commit()
    with Held(pg_engine, REWARD_POST_JOB_LOCK_KEY):
        report = reward_post_job.run(pg_engine, "cron", today=date(2027, 1, 10))
    assert (report["status"], report["posted"], report["forfeited"], report["skipped"]) == ("busy", 0, 0, 0)
    assert _accrual(db_session, source_entry_id=spend.id).state == "estimated"
    assert _rewards(db_session) == []


def test_job_one_failing_accrual_does_not_stop_the_others(pg_engine, db_session, monkeypatch):
    card = _card(db_session)
    rule = _rule(db_session, card)
    first = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    second = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    broken = _accrual(db_session, source_entry_id=first.id).id
    db_session.commit()
    real = reward_posting.post_due_accrual

    def flaky(db, accrual_id, today):
        if accrual_id == broken:
            raise RuntimeError("fictional")
        return real(db, accrual_id, today)

    monkeypatch.setattr(reward_posting, "post_due_accrual", flaky)
    report = reward_post_job.run(pg_engine, "manual", today=date(2027, 1, 10))
    assert (report["status"], report["posted"], report["failed"]) == ("ok", 1, 1)
    assert _accrual(db_session, source_entry_id=first.id).state == "estimated"
    assert _accrual(db_session, source_entry_id=second.id).state == "posted"


def test_job_skips_an_accrual_moved_out_of_due(pg_engine, db_session):
    """The due check is repeated under the window lock: an accrual whose post_on moved past today is left."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    db_session.commit()
    assert reward_posting.post_due_accrual(db_session, accrual_id, date(2027, 1, 4)) == "skipped"
    assert reward_posting.post_due_accrual(db_session, accrual_id, date(2027, 1, 5)) == "posted"
    db_session.commit()


def _second_purchase(card_id, rule_id, day):
    def write(db):
        entry = make_entry(db, db.get(Account, card_id), "-1000", entry_date=day)
        write_rule_links(db, entry.id, [rule_id])
        reward_engine.sync_entry(db, entry.id)
    return write


@pytest.mark.parametrize("job_first", [True, False])
def test_job_and_reconcile_same_window_no_deadlock(pg_engine, db_session, job_first):
    """P12: the job's per-accrual post and a concurrent engine write on the same window (a new purchase's
    sync_entry, which recomputes the window) serialise on the window lock: both commit, no deadlock. Task 8 adds
    the reconcile (bank wins) version of this race."""
    card = _card(db_session)
    rule = _rule(db_session, card, total_cap=Decimal("15"))
    spend = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    card_id, rule_id = card.id, rule.id
    db_session.commit()

    def post(db):
        assert reward_posting.post_due_accrual(db, accrual_id, date(2027, 1, 10)) == "posted"

    other = _second_purchase(card_id, rule_id, date(2026, 11, 25))
    first, second = (post, other) if job_first else (other, post)
    assert race(pg_engine, first, second) == "committed"

    assert _accrual(db_session, id=accrual_id).state == "posted"
    late = _accrual(db_session, rule_id=rule_id, state="estimated")
    assert late.amount == Decimal("5.0000")  # the cap left after the posted 10
    assert [row.amount for row in _rewards(db_session)] == [Decimal("10.0000")]


def test_two_posts_of_one_accrual_race_to_one_row(pg_engine, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    db_session.commit()
    results = []

    def post(db):
        results.append(reward_posting.post_due_accrual(db, accrual_id, date(2027, 1, 10)))

    assert race(pg_engine, post, post) == "committed"
    assert results == ["posted", "skipped"]
    assert len(_rewards(db_session)) == 1


# --- manual endpoint -------------------------------------------------------------------------------------------


def test_manual_endpoint_posts_manual_rule(client, db_session, today):
    today(date(2026, 12, 3))
    card = _card(db_session)
    rule = _rule(db_session, card, posting="manual")
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    other = _spend(db_session, card, 2000, date(2026, 11, 21), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    other_id = _accrual(db_session, source_entry_id=other.id).id
    db_session.commit()

    response = client.post(f"/reward-accruals/{accrual_id}/post", json={"on": "2026-12-01"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == "posted"
    row = db_session.get(LedgerEntry, body["ledger_entry_id"])
    assert (row.entry_date, row.amount, row.reward_rule_id) == (date(2026, 12, 1), Decimal("10.0000"), rule.id)

    response = client.post(f"/reward-accruals/{other_id}/post", json={})
    assert response.status_code == 200, response.text
    assert db_session.get(LedgerEntry, response.json()["ledger_entry_id"]).entry_date == date(2026, 12, 3)

    again = client.post(f"/reward-accruals/{accrual_id}/post", json={"on": None})
    assert again.status_code == 409 and again.json()["message"].startswith("accrual_not_estimated"), again.text
    assert len(_rewards(db_session)) == 2

    missing = client.post("/reward-accruals/9999/post", json={})
    assert missing.status_code == 404, missing.text


def test_manual_endpoint_refuses_non_manual(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    db_session.commit()
    response = client.post(f"/reward-accruals/{accrual_id}/post", json={"on": "2026-12-01"})
    assert response.status_code == 409, response.text
    assert response.json()["message"].startswith(reward_posting.RULE_NOT_MANUAL)
    assert _accrual(db_session, id=accrual_id).state == "estimated"
    assert _rewards(db_session) == []


def test_manual_endpoint_forfeits_a_zero_allotment(client, db_session):
    card = _card(db_session)
    rule = _rule(db_session, card, posting="manual", total_cap=Decimal("10"))
    _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    late = _spend(db_session, card, 1000, date(2026, 11, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=late.id).id
    db_session.commit()
    response = client.post(f"/reward-accruals/{accrual_id}/post", json={"on": "2026-12-01"})
    assert response.status_code == 200, response.text
    assert response.json() == {"result": "forfeited", "ledger_entry_id": None}



def test_manual_post_sets_post_on(db_session, run):
    """`post_accrual(..., on=d)` records `post_on = d`, so a manual rule's posted row is a bank-wins candidate (a) on
    the statement whose period contains `d`."""
    card = _card(db_session)
    rule = _rule(db_session, card, posting="manual")
    spend = _spend(db_session, card, 1000, date(2026, 10, 20), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    assert _accrual(db_session, id=accrual_id).post_on is None
    db_session.commit()

    assert reward_posting.post_accrual(db_session, accrual_id, on=date(2026, 10, 30)) == "posted"
    db_session.commit()
    accrual = _accrual(db_session, id=accrual_id)
    assert (accrual.state, accrual.post_on) == ("posted", date(2026, 10, 30))
    assert db_session.get(LedgerEntry, accrual.ledger_entry_id).entry_date == date(2026, 10, 30)

    stmt, _, _ = _submit(db_session, run, card, [], period=(date(2026, 10, 16), date(2026, 11, 15)))
    assert [a.id for a in reward_bank_wins.candidates(db_session, stmt, [card.id])] == [accrual_id]


def _put_amount(card_id, rule_id, entry_id, amount, day):
    payload = EntryUpdateIn(account_id=card_id, kind="expense", amount=Decimal(amount), entry_date=day,
                            posted_date=day, reward_rule_ids=[rule_id])

    def put(db):
        ews.update_entry(db, entry_id, payload)
    return put


@pytest.mark.parametrize("job_first", [True, False])
def test_job_and_owner_put_race_no_deadlock(pg_engine, db_session, job_first):
    """P12: the job takes the purchase before the window, as an owner PUT of that purchase does: the second waits on
    the purchase row and both commit."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    db_session.commit()

    def post(db):
        assert reward_posting.post_due_accrual(db, accrual_id, date(2027, 1, 10)) == "posted"

    put = _put_amount(card.id, rule.id, spend.id, "2000", date(2026, 11, 18))
    first, second = (post, put) if job_first else (put, post)
    assert race(pg_engine, first, second) == "committed"

    accrual = _accrual(db_session, id=accrual_id)
    (row,) = _rewards(db_session)
    assert accrual.state == "posted" and row.id == accrual.ledger_entry_id
    assert row.amount == (Decimal("10.0000") if job_first else Decimal("20.0000"))
    purchase = db_session.get(LedgerEntry, spend.id)
    assert purchase.review_reason == ("reward_posted" if job_first else None)


def test_job_paused_before_insert_and_owner_put_no_deadlock(pg_engine, db_session, monkeypatch):
    """The cycle of the controller's finding: the job holds the window and pauses before its reward-row INSERT (whose
    FK check share-locks the purchase); an owner PUT of that purchase then starts. Before the fix the PUT took the
    purchase and waited on the window, and the INSERT closed the cycle (40P01). Now the job already holds the
    purchase, the PUT waits on it, and both commit."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    spend = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    accrual_id = _accrual(db_session, source_entry_id=spend.id).id
    db_session.commit()
    paused, go = threading.Event(), threading.Event()
    real = reward_posting._book

    def book_after_pause(db, accrual, on):
        paused.set()
        assert go.wait(15)
        return real(db, accrual, on)

    monkeypatch.setattr(reward_posting, "_book", book_after_pause)

    def post(db):
        assert reward_posting.post_due_accrual(db, accrual_id, date(2027, 1, 10)) == "posted"

    out = _interleave(pg_engine, post, _put_amount(card.id, rule.id, spend.id, "2000", date(2026, 11, 18)),
                      paused, go)
    assert out == {"first": "committed", "second": "committed"}, out
    assert _accrual(db_session, id=accrual_id).state == "posted"


def test_job_refund_post_vs_purchase_delete_no_deadlock(pg_engine, db_session, monkeypatch):
    """A refund's accrual has the refund row as its source: the job locks the refund, then the window. Deleting the
    purchase holds the purchase, takes the window (its refunds' estimated accruals go) and nulls the refund's
    `refunds_entry_id`. The refunds of the doomed purchases are tier-1 rows of the delete, locked before any window,
    so the delete waits on the refund the paused job holds and both commit (fix round 2; was 40P01)."""
    card = _card(db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 11, 18), [rule])
    db_session.commit()
    refund_id = settlement_service.refund(db_session, buy.id, RefundIn(amount=Decimal("200"),
                                                                        entry_date=date(2026, 11, 20)))
    db_session.commit()
    accrual = _accrual(db_session, source_entry_id=refund_id)
    accrual_id, buy_id = accrual.id, buy.id
    assert (accrual.state, accrual.amount) == ("estimated", Decimal("-2.0000"))

    paused, go = threading.Event(), threading.Event()
    real = reward_engine.lock_windows
    calls = {"n": 0}

    def lock_windows(db, pairs, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # the job: after its source (refund) row lock, before the window
            paused.set()
            assert go.wait(15)
        return real(db, pairs, **kwargs)

    monkeypatch.setattr(reward_engine, "lock_windows", lock_windows)

    def post(db):
        assert reward_posting.post_due_accrual(db, accrual_id, date(2027, 1, 10)) == "posted"

    out = _interleave(pg_engine, post, lambda db: ews.delete_entry(db, buy_id), paused, go)
    assert out == {"first": "committed", "second": "committed"}, out
    db_session.expire_all()
    assert db_session.get(LedgerEntry, buy_id) is None
    refund = db_session.get(LedgerEntry, refund_id)
    assert refund is not None and refund.refunds_entry_id is None
    assert _accrual(db_session, id=accrual_id).state == "posted"  # frozen before the delete; it survives
