"""Issue #117: the portfolio API's bearer-token auth (shared_lib.token_auth) on the real app. Fictional tokens only."""
import pytest

TOKENS = "spa:spa-test,accounting:feed-test,worker:jobs-test"
SCOPES = "spa=read,write; accounting=read; worker=write"
FEED = "/api/portfolio/ledger-feed/settlements?broker=CATHAY&date_from=2026-01-01&date_to=2026-01-31"
WRITE = "/api/portfolio/fx/refresh"


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_API_TOKENS", TOKENS)
    monkeypatch.setenv("PORTFOLIO_TOKEN_SCOPES", SCOPES)
    monkeypatch.delenv("PORTFOLIO_DOCS_PUBLIC", raising=False)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_without_tokens_the_api_answers_as_before(client, monkeypatch):
    monkeypatch.delenv("PORTFOLIO_API_TOKENS", raising=False)
    monkeypatch.delenv("PORTFOLIO_TOKEN_SCOPES", raising=False)
    assert client.get(FEED).status_code not in (401, 403)


def test_every_portfolio_route_needs_a_token(client, auth_on):
    for method, path in (("get", FEED), ("get", "/api/portfolio/transactions"), ("post", WRITE), ("get", "/docs")):
        response = getattr(client, method)(path)
        assert (response.status_code, response.json()) == (401, {"detail": "unauthorized"}), path


def test_health_stays_open(client, auth_on):
    assert client.get("/health").status_code != 401


def test_read_only_label_reads_the_feed_but_cannot_write(client, auth_on, monkeypatch):
    assert client.get(FEED, headers=_auth("feed-test")).status_code not in (401, 403)
    response = client.post(WRITE, headers=_auth("feed-test"))
    assert (response.status_code, response.json()) == (403, {"detail": "scope"})


def test_write_only_label_cannot_read(client, auth_on):
    assert client.get(FEED, headers=_auth("jobs-test")).status_code == 403


def test_spa_label_passes_auth_for_both(client, auth_on):
    assert client.get(FEED, headers=_auth("spa-test")).status_code not in (401, 403)
    assert client.get("/api/portfolio/transactions", headers=_auth("spa-test")).status_code == 200
