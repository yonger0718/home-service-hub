"""The portfolio ledger-feed client: typed parsing, token-safe failures, completeness asserted once."""

from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.services import portfolio_client as pc

TOKEN = "secret-token-value"
WINDOW = dict(settle_from=date(2026, 9, 1), settle_to=date(2026, 9, 30), trade_from=date(2026, 8, 27), trade_to=date(2026, 9, 30))
BOUNDS = {"settle_from": "2026-09-01", "settle_to": "2026-09-30", "from": "2026-08-27", "to": "2026-09-30"}
ROW = {"trade_date": "2026-09-10", "settlement_date": "2026-09-14", "date_source": "statement", "net_amount": "-12305.0000",
       "legs_net": "-12305.0000", "currency": "TWD", "bookable": True, "reason": None, "fingerprint": "cathay:2026-09-10:abc",
       "items": [{"source_id": "ct:7", "type": "fee", "amount": "-5.0000"}, {"source_id": "ct:5", "type": "buy_settle", "amount": "-12300.0000"}]}
VALUATION = {"broker": "cathay", "currency": "TWD", "as_of": "2026-09-30", "priced_through": None, "market_value": "1000.0000",
             "cash": "50.0000", "total": "1050.0000", "complete": False, "missing": ["2330"]}


def test_settlements_parse_and_header_only_with_a_token():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json={"rows": [ROW], "bounds": BOUNDS, "complete": True})

    (row,) = pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(handler)).settlements("cathay", **WINDOW).rows
    pc.PortfolioClient("http://p.test", TOKEN, transport=httpx.MockTransport(handler)).settlements("cathay", **WINDOW)
    assert seen == [("/api/portfolio/ledger-feed/settlements", None), ("/api/portfolio/ledger-feed/settlements", f"Bearer {TOKEN}")]
    assert (row.settlement_date, row.net_amount, row.bookable, row.source_ids) == (date(2026, 9, 14), Decimal("-12305.0000"), True, ("ct:5", "ct:7"))


def test_settlements_send_the_feed_query_names_and_parse_a_null_settlement_date():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"rows": [{**ROW, "settlement_date": None, "reason": "margin_day", "bookable": False}], "bounds": BOUNDS, "complete": True})

    feed = pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(handler)).settlements("cathay", **WINDOW)
    assert seen == [{"broker": "cathay", "settle_from": "2026-09-01", "settle_to": "2026-09-30", "from": "2026-08-27", "to": "2026-09-30"}]
    assert feed.complete is True and feed.bounds.trade_from == date(2026, 8, 27)
    assert (feed.rows[0].settlement_date, feed.rows[0].reason, feed.rows[0].items[0].amount) == (None, "margin_day", Decimal("-5.0000"))


def test_valuation_is_a_bare_object():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=VALUATION)

    v = pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(handler)).valuation("cathay", date(2026, 9, 30))
    assert seen == [{"broker": "cathay", "as_of": "2026-09-30"}]
    assert (v.total, v.priced_through, v.complete, v.missing) == (Decimal("1050.0000"), None, False, ("2330",))


@pytest.mark.parametrize("body", [{"rows": [ROW], "bounds": BOUNDS, "complete": False},
                                  {"rows": [ROW], "bounds": {**BOUNDS, "from": "2026-09-05"}, "complete": True}])
def test_incomplete_or_narrowed_answers_are_unavailable(body):
    with pytest.raises(pc.PortfolioUnavailable) as info:
        pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))).settlements("cathay", **WINDOW)
    assert info.value.code == "incomplete"


@pytest.mark.parametrize("make, code", [(lambda r: httpx.Response(500), "http_500"), (lambda r: httpx.Response(404), "http_404"),
                                        (lambda r: httpx.Response(200, json=[ROW]), "bad_body"),  # a bare list: no envelope
                                        (lambda r: httpx.Response(200, json={**VALUATION, "total": "x"}), "bad_body"),
                                        (lambda r: httpx.Response(200, content=b"not json"), "bad_body"),
                                        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")), "connect"),
                                        (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), "timeout")])
def test_failures_are_unavailable_with_a_code_only(make, code, caplog):
    with pytest.raises(pc.PortfolioUnavailable) as info:
        pc.PortfolioClient("http://p.test", TOKEN, transport=httpx.MockTransport(make)).valuation("cathay", date(2026, 9, 30))
    assert info.value.code == code and str(info.value) == code and TOKEN not in str(info.value) + repr(info.value) + caplog.text


@pytest.mark.parametrize("row", [{**ROW, "bookable": "false"}, {**ROW, "net_amount": "NaN"}, {**ROW, "legs_net": "Infinity"}])
def test_loose_settlement_values_are_bad_body(row):
    body = {"rows": [row], "bounds": BOUNDS, "complete": True}
    with pytest.raises(pc.PortfolioUnavailable) as info:
        pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))).settlements("cathay", **WINDOW)
    assert info.value.code == "bad_body"


@pytest.mark.parametrize("body", [{"rows": [], "bounds": BOUNDS, "complete": "true"}, {**VALUATION, "complete": 1},
                                  {**VALUATION, "missing": "2330"}, {**VALUATION, "missing": [2330]}, {**VALUATION, "cash": "NaN"}])
def test_loose_feed_and_valuation_values_are_bad_body(body):
    client = pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(pc.PortfolioUnavailable) as info:
        client.settlements("cathay", **WINDOW) if "rows" in body else client.valuation("cathay", date(2026, 9, 30))
    assert info.value.code == "bad_body"


def test_repr_does_not_leak_the_token():
    assert TOKEN not in repr(pc.PortfolioClient("http://p.test", TOKEN))


def test_cached_client_memoises_within_the_block():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"rows": [ROW], "bounds": BOUNDS, "complete": True})

    with pc.cached(pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(handler))) as c:
        c.settlements("cathay", **WINDOW)
        c.settlements("cathay", **WINDOW)
    assert calls == [1]


def test_client_for_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_BASE_URL", raising=False)
    monkeypatch.delenv("PORTFOLIO_READ_TOKEN", raising=False)
    assert pc.client_for().base_url == "http://127.0.0.1:8001"


def test_client_for_reads_the_environment_at_call_time(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BASE_URL", "http://p.test/")
    monkeypatch.setenv("PORTFOLIO_READ_TOKEN", TOKEN)
    client = pc.client_for()
    assert client.base_url == "http://p.test" and client.token == TOKEN


def test_cached_client_does_not_share_different_args_and_memoises_valuation():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        if request.url.path.endswith("valuation"):
            return httpx.Response(200, json=VALUATION)
        p = request.url.params
        bounds = {"settle_from": p["settle_from"], "settle_to": p["settle_to"], "from": p["from"], "to": p["to"]}
        return httpx.Response(200, json={"rows": [ROW], "bounds": bounds, "complete": True})

    with pc.cached(pc.PortfolioClient("http://p.test", transport=httpx.MockTransport(handler))) as c:
        c.settlements("cathay", **WINDOW)
        c.settlements("cathay", **{**WINDOW, "settle_to": date(2026, 9, 29)})
        c.settlements("other", **WINDOW)
        c.valuation("cathay", date(2026, 9, 30))
        c.valuation("cathay", date(2026, 9, 30))
        c.valuation("cathay", date(2026, 9, 29))
    assert [p["broker"] for p in seen].count("other") == 1 and len(seen) == 5
