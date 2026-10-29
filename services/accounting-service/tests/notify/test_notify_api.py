from datetime import date

import httpx
import pytest
from notify.api import ApiError, CappedRead, ReadClient, fetch_daily, fetch_weekly
from notify_fakes import make_client

TODAY = date(2026, 10, 27)
ACCOUNTS = [
    {"id": 100, "is_credit": True, "is_archived": False, "combined_account_id": None, "auto_pay_account_id": 1},
    {"id": 104, "is_credit": True, "is_archived": False, "combined_account_id": 100, "auto_pay_account_id": 1},
    {"id": 1, "is_credit": False, "is_archived": False, "combined_account_id": None, "auto_pay_account_id": None},
]
DAILY = {
    "/settings/reconciliation": {"auto_pay": {}},
    "/calendar/business-days": {"holidays": ["2026-10-26"], "workdays": [], "last_known_day": "2027-12-31"},
    "/accounts": ACCOUNTS,
    "/accounts/100/statements": [{"id": 1}], "/accounts/104/statements": [],
    "/accounts/1/statements": [],
    "/entries": {"items": [], "total": 0, "limit": 500, "offset": 0},
    "/schedules/instances": [],
    "/reconciliation/audit": [],
    "/reconciliation/inbox": {"accounts": [{"id": 100, "items": [{"case_id": 1}]}]},
    "/reconciliation/cases": [{"id": 1}],
}


def test_only_get_and_bearer():
    client, log = make_client({"/settings/reconciliation": {"auto_pay": {}}})
    ReadClient(client, "secret-token").settings()
    assert [(r.method, r.headers["authorization"]) for r in log] == [("GET", "Bearer secret-token")]


def test_entries_pages_until_total():
    client, log = make_client({"/entries": {"__pages__": [
        {"items": [{"id": 1}], "total": 2, "limit": 1, "offset": 0},
        {"items": [{"id": 2}], "total": 2, "limit": 1, "offset": 1}]}})
    got = ReadClient(client, "t").entries([3, 4], "transfer_in", date(2026, 7, 1), TODAY)
    assert [e["id"] for e in got] == [1, 2]
    assert log[0].url.params.get_list("account_id") == ["3", "4"] and log[1].url.params["offset"] == "1"


def test_entries_early_end_is_capped():
    client, _ = make_client({"/entries": {"items": [], "total": 5, "limit": 500, "offset": 0}})
    with pytest.raises(CappedRead) as e:
        ReadClient(client, "t").entries([3], "expense", date(2026, 7, 1), TODAY)
    assert e.value.status is None and str(e.value) == "/entries: entries page ended early"


def test_error_hides_body():
    client, _ = make_client({"/accounts": httpx.Response(401, json={"detail": "bad token secret-token"})})
    with pytest.raises(ApiError) as e:
        ReadClient(client, "secret-token").accounts(TODAY)
    assert "secret-token" not in str(e.value) and e.value.status == 401 and str(e.value) == "401 /accounts"


def test_no_non_get_method():
    names = [n for n in dir(ReadClient) if n in {"post", "put", "patch", "delete", "request"}]
    assert names == []


def test_fetch_daily_groups_children():
    client, log = make_client(DAILY)
    snap = fetch_daily(ReadClient(client, "t"), TODAY)
    assert set(snap.payments) == {100} and set(snap.source_entries) == {1}
    assert snap.calendar.holidays == frozenset({date(2026, 10, 26)}) and snap.calendar.last_known_day == date(2027, 12, 31)
    assert set(snap.statements) == {100, 104} and snap.inbox is None and snap.open_cases == {}
    pay = [r for r in log if r.url.path == "/entries" and r.url.params.get("kind") == "transfer_in"][0]
    assert pay.url.params.get_list("account_id") == ["100", "104"]
    assert {r.method for r in log} == {"GET"}


def test_fetch_weekly_adds_inbox_cases_and_bank_statements():
    client, _ = make_client(DAILY)
    snap = fetch_weekly(ReadClient(client, "t"), TODAY)
    assert set(snap.statements) == {100, 104, 1} and snap.open_cases == {100: [{"id": 1}]}
    assert snap.inbox is not None


def test_full_audit_page_inside_window_is_capped():
    rows = [{"id": 2000 - i, "event": "auto_pay_skipped", "created_at": "2026-10-27T06:00:00+08:00", "detail": {}}
            for i in range(1000)]
    client, _ = make_client(DAILY | {"/reconciliation/audit": rows})
    with pytest.raises(CappedRead):
        fetch_daily(ReadClient(client, "t"), TODAY)


def test_audit_is_read_inside_the_window_for_the_skip_event_only():
    """#201: the daily asks the API for `auto_pay_skipped` rows since the window start (06:00 the day before)."""
    client, log = make_client(DAILY)
    fetch_daily(ReadClient(client, "t"), TODAY)
    audit = [r for r in log if r.url.path.endswith("/reconciliation/audit")]
    assert len(audit) == 1
    params = audit[0].url.params
    assert (params["limit"], params["event"], params["since"]) == (
        "1000", "auto_pay_skipped", "2026-10-26T06:00:00+08:00")


def test_full_audit_page_older_than_window_is_fine():
    rows = [{"id": i, "created_at": "2026-10-20T06:00:00+08:00"} for i in range(1000)]
    client, _ = make_client(DAILY | {"/reconciliation/audit": rows})
    assert len(fetch_daily(ReadClient(client, "t"), TODAY).audit) == 1000


def test_full_inbox_is_capped():
    inbox = {"accounts": [{"id": 100, "items": [{}] * 1000}]}
    client, _ = make_client(DAILY | {"/reconciliation/inbox": inbox})
    with pytest.raises(CappedRead):
        fetch_weekly(ReadClient(client, "t"), TODAY)


def test_full_cases_page_is_capped():
    client, _ = make_client(DAILY | {"/reconciliation/cases": [{}] * 1000})
    with pytest.raises(CappedRead):
        fetch_weekly(ReadClient(client, "t"), TODAY)


def test_fetch_reads_every_kind_of_card_entry_per_group():
    client, log = make_client(DAILY)
    snap = fetch_daily(ReadClient(client, "t"), TODAY)
    assert snap.card_activity == {100: []}
    every = [r for r in log if r.url.path == "/entries" and "kind" not in r.url.params]
    assert [r.url.params.get_list("account_id") for r in every] == [["100", "104"]]
    assert every[0].url.params["date_from"] == "2026-06-29" and every[0].url.params["date_to"] == "2026-10-27"
