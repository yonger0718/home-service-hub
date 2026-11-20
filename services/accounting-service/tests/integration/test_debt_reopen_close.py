"""#391: `POST /entries/{id}/reopen` and `POST /entries/{id}/close` set and clear `is_closed` on a receivable / payable
original. The flag only: amounts, settlements and links stay as they are. Fixtures are fictional."""

from datetime import date
from decimal import Decimal

import pytest

from app import auth
from app.models import LedgerEntry
from app.services import ledger_service

DAY = "2026-09-10"


def _error_fields(response) -> set[str]:
    return {str(error["loc"][-1]) for error in response.json()["detail"]}


def _state(client, entry_id) -> tuple[bool, str, bool]:
    detail = client.get(f"/entries/{entry_id}").json()
    return detail["is_closed"], detail["open_amount"], detail["is_settled"]


def _closed_receivable(seed, db, amount="-420", *, collected=None):
    """A closed receivable of 420 on a card, optionally with one collection → (card, wallet, receivable id)."""
    card, wallet = seed.account("卡"), seed.account("錢包")
    alan = seed.counterparty("Alan")
    receivable = seed.entry(card, amount, kind="receivable", counterparty_id=alan.id, is_closed=True)
    if collected is not None:
        seed.entry(
            wallet, collected, kind="receivable", counterparty_id=alan.id, settles_entry_id=receivable.id,
            is_settlement=True,
        )
    db.commit()
    return card, wallet, receivable.id


def test_reopen_brings_the_remainder_back_and_settle_works_again(client, db_session, seed):
    _, wallet, receivable_id = _closed_receivable(seed, db_session, collected="200")
    assert _state(client, receivable_id) == (True, "0.0000", True)

    response = client.post(f"/entries/{receivable_id}/reopen")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["is_closed"], body["open_amount"], body["is_settled"]) == (
        receivable_id, False, "220.0000", False)
    assert [str(row["amount"]) for row in body["settled_by"]] == ["200.0000"]
    settled = client.post(
        f"/entries/{receivable_id}/settle", json={"account_id": wallet.id, "amount": "220", "entry_date": DAY})
    assert settled.status_code == 201, settled.text
    assert _state(client, receivable_id) == (False, "0.0000", True)


def test_reopen_without_any_settlement_returns_the_whole_amount(client, db_session, seed):
    _, _, receivable_id = _closed_receivable(seed, db_session)

    response = client.post(f"/entries/{receivable_id}/reopen")

    assert response.status_code == 200, response.text
    assert _state(client, receivable_id) == (False, "420.0000", False)


def test_reopen_of_a_debt_whose_settlements_net_stays_settled(client, db_session, seed):
    _, _, receivable_id = _closed_receivable(seed, db_session, collected="420")

    response = client.post(f"/entries/{receivable_id}/reopen")

    assert response.status_code == 200, response.text
    assert _state(client, receivable_id) == (False, "0.0000", True)


def test_close_takes_the_remainder_out_and_settle_is_refused(client, db_session, seed):
    card, wallet = seed.account("卡"), seed.account("錢包")
    receivable = seed.entry(card, "-420", kind="receivable", counterparty_id=seed.counterparty("Alan").id)
    db_session.commit()
    assert _state(client, receivable.id) == (False, "420.0000", False)

    response = client.post(f"/entries/{receivable.id}/close")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["is_closed"], body["open_amount"], body["is_settled"]) == (
        receivable.id, True, "0.0000", True)
    db_session.expire_all()
    assert db_session.get(LedgerEntry, receivable.id).amount == Decimal("-420")
    refused = client.post(
        f"/entries/{receivable.id}/settle", json={"account_id": wallet.id, "amount": "100", "entry_date": DAY})
    assert refused.status_code == 422 and _error_fields(refused) == {"is_closed"}


def test_close_and_reopen_leave_the_open_debt_list_in_step(client, db_session, seed):
    card = seed.account("卡")
    alan = seed.counterparty("Alan")
    receivable = seed.entry(card, "-420", kind="receivable", counterparty_id=alan.id)
    db_session.commit()

    def open_ids() -> list[int]:
        return [row["id"] for row in client.get("/entries", params={"open": "true"}).json()["items"]]

    assert open_ids() == [receivable.id]
    client.post(f"/entries/{receivable.id}/close")
    assert open_ids() == []
    client.post(f"/entries/{receivable.id}/reopen")
    assert open_ids() == [receivable.id]


def test_both_actions_are_idempotent(client, db_session, seed):
    card = seed.account("卡")
    payable = seed.entry(card, "900", kind="payable", counterparty_id=seed.counterparty("Bea").id)
    db_session.commit()

    again_open = client.post(f"/entries/{payable.id}/reopen")
    first, second = client.post(f"/entries/{payable.id}/close"), client.post(f"/entries/{payable.id}/close")

    assert (again_open.status_code, first.status_code, second.status_code) == (200, 200, 200)
    assert (again_open.json()["is_closed"], first.json()["is_closed"], second.json()["is_closed"]) == (False, True, True)


@pytest.mark.parametrize("action", ["reopen", "close"])
def test_only_a_debt_original_can_be_reopened_or_closed(client, db_session, seed, action):
    card, wallet = seed.account("卡"), seed.account("錢包")
    alan = seed.counterparty("Alan")
    expense = seed.entry(card, "-80")
    receivable = seed.entry(card, "-420", kind="receivable", counterparty_id=alan.id)
    collection = seed.entry(
        wallet, "200", kind="receivable", counterparty_id=alan.id, settles_entry_id=receivable.id, is_settlement=True)
    unlinked = seed.entry(wallet, "50", kind="receivable", counterparty_id=alan.id, is_settlement=True)
    # A row that points at a debt without the settlement flag is still not an original.
    linked = seed.entry(wallet, "-70", kind="receivable", counterparty_id=alan.id, settles_entry_id=receivable.id)
    db_session.commit()
    rows = (expense, collection, unlinked, linked)

    for entry in rows:
        response = client.post(f"/entries/{entry.id}/{action}")
        assert response.status_code == 422 and _error_fields(response) == {"kind"}, response.text
    db_session.expire_all()
    assert [db_session.get(LedgerEntry, e.id).is_closed for e in rows] == [False] * 4


@pytest.mark.parametrize("action", ["reopen", "close"])
def test_an_unknown_entry_is_not_found(client, action):
    assert client.post(f"/entries/999999/{action}").status_code == 404


@pytest.mark.parametrize("action", ["reopen", "close"])
def test_an_imported_debt_is_locked_until_cutover(client, db_session, seed, action):
    card = seed.account("卡")
    receivable = seed.entry(
        card, "-420", kind="receivable", counterparty_id=seed.counterparty("Alan").id, source="moze_backup",
        moze_id="rec-1", is_closed=action == "reopen")
    db_session.commit()

    response = client.post(f"/entries/{receivable.id}/{action}")

    assert (response.status_code, response.json()["message"]) == (409, "locked_until_cutover")
    db_session.expire_all()
    assert db_session.get(LedgerEntry, receivable.id).is_closed is (action == "reopen")


def _loan(seed, db):
    bank = seed.account("薪轉")
    payable = seed.entry(
        bank, "300000", kind="payable", counterparty_id=seed.counterparty("範例銀行").id, name="信貸",
        day=date(2026, 10, 3))
    definition = seed.definition(
        [seed.line("repayment", bank, "8333", loan_entry_id=payable.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸 每月還款", anchor=date(2026, 11, 9), times=36, total_amount=Decimal("300000"),
    )
    db.commit()
    return payable, definition


def test_close_is_refused_while_a_live_schedule_repays_the_loan(client, db_session, seed):
    payable, _ = _loan(seed, db_session)

    response = client.post(f"/entries/{payable.id}/close")

    assert response.status_code == 422 and _error_fields(response) == {"loan_schedule"}, response.text
    assert _state(client, payable.id)[0] is False
    assert _description(client, payable.id) is None


def test_close_is_allowed_once_the_schedule_is_ended_and_reopen_is_always_allowed(client, db_session, seed):
    payable, definition = _loan(seed, db_session)
    definition.status = "ended"
    db_session.commit()

    closed = client.post(f"/entries/{payable.id}/close")
    assert closed.status_code == 200 and closed.json()["is_closed"] is True, closed.text

    definition.status = "active"
    db_session.commit()
    reopened = client.post(f"/entries/{payable.id}/reopen")
    assert reopened.status_code == 200 and reopened.json()["is_closed"] is False, reopened.text


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(ledger_service, "_today", lambda: date(2026, 11, 20))


def _description(client, entry_id) -> str | None:
    return client.get(f"/entries/{entry_id}").json()["description"]


def _open_receivable(seed, db, *, collected=None, description=None):
    """An open receivable of 420, optionally with one collection → (card, receivable id)."""
    card, wallet = seed.account("卡"), seed.account("錢包")
    alan = seed.counterparty("Alan")
    receivable = seed.entry(card, "-420", kind="receivable", counterparty_id=alan.id, description=description)
    if collected is not None:
        seed.entry(
            wallet, collected, kind="receivable", counterparty_id=alan.id, settles_entry_id=receivable.id,
            is_settlement=True,
        )
    db.commit()
    return card, receivable.id


def test_close_with_a_remainder_adds_a_line_to_the_description(client, db_session, seed, today):
    card, receivable_id = _open_receivable(seed, db_session, collected="200", description="晚餐代墊")

    response = client.post(f"/entries/{receivable_id}/close")

    assert response.status_code == 200, response.text
    assert response.json()["description"] == f"晚餐代墊\n2026-11-20 標記結清，當時剩餘 {card.currency} 220"


def test_the_line_stands_alone_when_the_debt_had_no_description(client, db_session, seed, today):
    card, receivable_id = _open_receivable(seed, db_session)

    client.post(f"/entries/{receivable_id}/close")

    assert _description(client, receivable_id) == f"2026-11-20 標記結清，當時剩餘 {card.currency} 420"


def test_close_with_nothing_left_adds_no_line(client, db_session, seed, today):
    _, receivable_id = _open_receivable(seed, db_session, collected="420", description="晚餐代墊")

    response = client.post(f"/entries/{receivable_id}/close")

    assert (response.status_code, response.json()["is_closed"]) == (200, True)
    assert _description(client, receivable_id) == "晚餐代墊"


def test_a_repeated_close_adds_no_second_line_and_reopen_keeps_the_line(client, db_session, seed, today):
    card, receivable_id = _open_receivable(seed, db_session)
    line = f"2026-11-20 標記結清，當時剩餘 {card.currency} 420"

    first, second = client.post(f"/entries/{receivable_id}/close"), client.post(f"/entries/{receivable_id}/close")
    assert (first.status_code, second.status_code, second.json()["is_closed"]) == (200, 200, True)
    assert _description(client, receivable_id) == line

    reopened = client.post(f"/entries/{receivable_id}/reopen")
    assert (reopened.status_code, reopened.json()["is_closed"]) == (200, False)
    assert _description(client, receivable_id) == line


@pytest.fixture
def scoped(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, "wr:wr-token,rd:rd-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "wr=write; rd=read")  # write alone must be enough


def test_write_scope_may_do_both_and_read_scope_neither(client, db_session, seed, scoped):
    card = seed.account("卡")
    receivable = seed.entry(card, "-420", kind="receivable", counterparty_id=seed.counterparty("Alan").id)
    db_session.commit()
    write, read = {"Authorization": "Bearer wr-token"}, {"Authorization": "Bearer rd-token"}

    refused = [client.post(f"/entries/{receivable.id}/{action}", headers=read) for action in ("close", "reopen")]
    assert [r.status_code for r in refused] == [403, 403]

    closed = client.post(f"/entries/{receivable.id}/close", headers=write)
    reopened = client.post(f"/entries/{receivable.id}/reopen", headers=write)
    assert (closed.status_code, closed.json()["is_closed"]) == (200, True)
    assert (reopened.status_code, reopened.json()["is_closed"]) == (200, False)
