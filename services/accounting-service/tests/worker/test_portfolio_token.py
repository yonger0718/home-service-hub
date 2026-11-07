"""Issue #117: the worker's flex and cathay jobs send the portfolio API bearer token from STATEMENT_PORTFOLIO_TOKEN_FILE.
Fictional tokens only."""
from pathlib import Path

import pytest

from worker import cathay, cli, config, flex


class _Client:
    made: list[dict] = []

    def __init__(self, **kw):
        _Client.made.append(kw)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def run(tmp_path, monkeypatch):
    _Client.made = []
    monkeypatch.setattr(cli.httpx, "Client", _Client)
    monkeypatch.setattr(flex, "run", lambda c, **kw: {"codes": []})
    monkeypatch.setattr(cathay, "run", lambda c, **kw: {"counts": {}, "codes": []})
    creds = tmp_path / "flex.env"
    creds.write_text("x=y\n")

    def go(job, token_file=None):
        env = {"STATEMENT_STATE_DIR": str(tmp_path / "s"), "STATEMENT_FLEX_CREDENTIALS_FILE": str(creds),
               "STATEMENT_API_TOKEN_FILE": str(tmp_path / "absent")}
        if token_file is not None:
            env["STATEMENT_PORTFOLIO_TOKEN_FILE"] = str(token_file)
        cfg = config.load(env)
        monkeypatch.setattr(config, "load", lambda env=None: cfg)
        _Client.made = []
        return cli.main([job])

    return go


def _portfolio(made):
    return [kw for kw in made if kw.get("base_url") == "http://127.0.0.1:8001"]


def test_config_reads_the_token_file_path():
    assert config.load({}).portfolio_token_file is None
    assert config.load({"STATEMENT_PORTFOLIO_TOKEN_FILE": "~/t"}).portfolio_token_file == Path("~/t").expanduser()


@pytest.mark.parametrize("job", ["flex", "cathay"])
def test_no_token_file_sends_no_header(run, job):
    assert run(job) == 0
    (client,) = _portfolio(_Client.made)
    assert client["headers"] == {}


@pytest.mark.parametrize("job", ["flex", "cathay"])
def test_token_file_sends_the_bearer_header(run, tmp_path, job):
    token = tmp_path / "portfolio-token"
    token.write_text("worker-test-token\n")
    assert run(job, token) == 0
    (client,) = _portfolio(_Client.made)
    assert client["headers"] == {"Authorization": "Bearer worker-test-token"}


@pytest.mark.parametrize("job", ["flex", "cathay"])
@pytest.mark.parametrize("content", [None, "", " \n"])
def test_missing_or_empty_token_file_stops_the_job(run, tmp_path, capsys, job, content):
    token = tmp_path / "portfolio-token"
    if content is not None:
        token.write_text(content)
    assert run(job, token) == 2
    assert _Client.made == []
    assert "portfolio token file is missing, unreadable or empty" in capsys.readouterr().err


@pytest.mark.parametrize("job", ["flex", "cathay"])
@pytest.mark.parametrize("kind", ["not_utf8", "no_permission"])
def test_unreadable_token_file_stops_the_job(run, tmp_path, capsys, job, kind):
    token = tmp_path / "portfolio-token"
    if kind == "not_utf8":
        token.write_bytes(b"\xff\xfe\x00bad")
    else:
        token.write_text("worker-test-token\n")
        token.chmod(0)
    try:
        assert run(job, token) == 2
    finally:
        token.chmod(0o600)
    assert _Client.made == []
    assert "portfolio token file is missing, unreadable or empty" in capsys.readouterr().err
