import asyncio
import threading
import time
from io import BytesIO

import anyio
import httpx
import pytest
from PIL import Image
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import AccountLogoImage
from app.services.account_logo_service import MAX_LOGO_UPLOAD_BYTES, delete_logo, get_logo, normalise_logo, put_logo
from app.services.errors import NotFoundError


def _png(w: int, h: int, mode: str = "RGBA", color=(200, 30, 30, 255)) -> bytes:
    buf = BytesIO()
    Image.new(mode, (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _account(client, **fields) -> dict:
    body = {"name": "測試帳戶", "currency": "TWD"}
    body.update(fields)
    return client.post("/accounts", json=body).json()


def test_account_delete_cascades_logo_image(client, db_session):
    acct = _account(client)
    db_session.add(
        AccountLogoImage(
            account_id=acct["id"], png=_png(256, 256), sha256="a" * 64, source_media_type="image/png", source_bytes=1
        )
    )
    db_session.commit()
    assert client.delete(f"/accounts/{acct['id']}").status_code == 204
    db_session.expire_all()
    assert db_session.get(AccountLogoImage, acct["id"]) is None


def test_logo_hash_is_null_without_image(client):
    acct = _account(client)
    assert client.get("/accounts").json()[0]["logo_hash"] is None
    assert client.get(f"/accounts/{acct['id']}").json()["logo_hash"] is None


def test_logo_hash_is_sha256_prefix_on_list_detail_and_put(client, db_session):
    acct = _account(client)
    put_logo(db_session, acct["id"], normalise_logo(_png(300, 100)))
    db_session.commit()
    sha = db_session.get(AccountLogoImage, acct["id"]).sha256
    assert client.get("/accounts").json()[0]["logo_hash"] == sha[:16]
    assert client.get(f"/accounts/{acct['id']}").json()["logo_hash"] == sha[:16]
    body = {"name": acct["name"], "currency": "TWD"}
    assert client.put(f"/accounts/{acct['id']}", json=body).json()["logo_hash"] == sha[:16]


def test_list_query_does_not_select_png(client, db_session):
    acct = _account(client)
    put_logo(db_session, acct["id"], normalise_logo(_png(64, 64)))
    db_session.commit()
    statements: list[str] = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        assert client.get("/accounts").status_code == 200
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    account_selects = [s for s in statements if "FROM account" in s]
    assert account_selects
    assert all("png" not in s for s in account_selects)


def test_put_logo_unknown_account_raises(db_session):
    with pytest.raises(NotFoundError):
        put_logo(db_session, 999999, normalise_logo(_png(64, 64)))


def test_delete_logo_returns_false_when_absent(client, db_session):
    acct = _account(client)
    assert delete_logo(db_session, acct["id"]) is False
    put_logo(db_session, acct["id"], normalise_logo(_png(64, 64)))
    db_session.commit()
    assert get_logo(db_session, acct["id"]) is not None
    assert delete_logo(db_session, acct["id"]) is True
    db_session.commit()
    assert get_logo(db_session, acct["id"]) is None
    with pytest.raises(NotFoundError):
        delete_logo(db_session, 999999)


def test_put_logo_replaces_existing_row(client, db_session):
    acct = _account(client)
    put_logo(db_session, acct["id"], normalise_logo(_png(64, 64)))
    db_session.commit()
    second = normalise_logo(_png(64, 64, color=(0, 0, 255, 255)))
    put_logo(db_session, acct["id"], second)
    db_session.commit()
    assert get_logo(db_session, acct["id"]).sha256 == second.sha256


# --- Task 4: routes ---------------------------------------------------------------------------------------------

def _gif() -> bytes:
    buf = BytesIO()
    Image.new("P", (64, 64), 1).save(buf, format="GIF")
    return buf.getvalue()


def _put(client, acct_id: int, body: bytes, ctype: str = "image/png"):
    return client.put(f"/accounts/{acct_id}/logo", content=body, headers={"Content-Type": ctype})


def test_put_logo_round_trip(client):
    acct = _account(client)
    r = _put(client, acct["id"], _png(300, 100))
    assert r.status_code == 200 and len(r.json()["logo_hash"]) == 16
    img = client.get(f"/accounts/{acct['id']}/logo.png?v={r.json()['logo_hash']}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert img.headers["content-disposition"] == 'inline; filename="logo.png"'
    assert img.headers["etag"].strip('"').startswith(r.json()["logo_hash"])
    assert Image.open(BytesIO(img.content)).size == (256, 256)


def test_get_without_v_or_with_stale_v_is_no_store(client):
    acct = _account(client)
    a = _put(client, acct["id"], _png(300, 100)).json()["logo_hash"]
    b = _put(client, acct["id"], _png(100, 300)).json()["logo_hash"]
    assert a != b
    stale = client.get(f"/accounts/{acct['id']}/logo.png?v={a}")
    assert stale.status_code == 200 and stale.headers["cache-control"] == "no-store"
    assert stale.headers["etag"].strip('"').startswith(b)
    assert client.get(f"/accounts/{acct['id']}/logo.png").headers["cache-control"] == "no-store"
    assert _put(client, acct["id"], _png(300, 100)).json()["logo_hash"] == a
    assert client.get(f"/accounts/{acct['id']}/logo.png?v={a}").headers["cache-control"].endswith("immutable")


def test_get_if_none_match_returns_304(client):
    acct = _account(client)
    h = _put(client, acct["id"], _png(300, 100)).json()["logo_hash"]
    first = client.get(f"/accounts/{acct['id']}/logo.png?v={h}")
    r = client.get(f"/accounts/{acct['id']}/logo.png?v={h}", headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 304 and r.content == b"" and r.headers["etag"] == first.headers["etag"]


def test_get_404_without_image_and_for_unknown_account(client):
    acct = _account(client)
    r = client.get(f"/accounts/{acct['id']}/logo.png")
    assert r.status_code == 404 and r.json()["message"] == "logo not found"
    r = client.get("/accounts/999999/logo.png")
    assert r.status_code == 404 and r.json()["message"] == "account not found"


def test_put_rejects_content_length_over_cap_before_reading(client):
    acct = _account(client)
    r = client.put(f"/accounts/{acct['id']}/logo", content=b"x",
                   headers={"Content-Length": str(MAX_LOGO_UPLOAD_BYTES + 1), "Content-Type": "image/png"})
    assert r.status_code == 413 and r.json()["message"] == "logo_too_large"


def test_streamed_body_over_cap_stops_reading(client):
    acct = _account(client)
    consumed = 0

    def gen():
        nonlocal consumed
        while True:
            consumed += 1
            yield b"x" * (1024 * 1024)
            if consumed > 50:
                return

    r = client.put(f"/accounts/{acct['id']}/logo", content=gen(), headers={"Content-Type": "image/png"})
    # chunked body (no Content-Length) is refused by the streamed cap; the TestClient itself drains the generator
    # before the app runs, so the consumption bound is asserted on _read_body_capped directly below.
    assert r.status_code == 413 and r.json()["message"] == "logo_too_large"


@pytest.mark.anyio
async def test_read_body_capped_stops_pulling_chunks_at_the_cap():
    from starlette.requests import Request

    from app.routers.accounts import _read_body_capped
    from app.services.account_logo_service import LogoError

    pulled = 0

    async def receive():
        nonlocal pulled
        pulled += 1
        return {"type": "http.request", "body": b"x" * (1024 * 1024), "more_body": True}

    request = Request({"type": "http", "method": "PUT", "headers": [], "path": "/"}, receive)
    with pytest.raises(LogoError) as info:
        await _read_body_capped(request, MAX_LOGO_UPLOAD_BYTES)
    assert info.value.code == "logo_too_large" and info.value.status == 413
    assert pulled <= 6, pulled


def test_put_empty_body_is_422_logo_empty(client):
    acct = _account(client)
    r = _put(client, acct["id"], b"")
    assert r.status_code == 422 and r.json()["message"] == "logo_empty"


@pytest.mark.parametrize("kind,token", [("svg", "logo_unreadable"), ("gif", "logo_format"), ("small", "logo_dimensions")])
def test_put_validation_tokens(client, kind, token):
    body = {"svg": b"<svg/>", "gif": _gif(), "small": _png(15, 15)}[kind]
    acct = _account(client)
    r = _put(client, acct["id"], body)
    assert r.status_code == 422 and r.json()["message"] == token


def test_put_unknown_account_404(client):
    r = _put(client, 999999, _png(300, 100))
    assert r.status_code == 404 and r.json()["message"] == "account not found"


def test_error_envelope_shape(client):
    acct = _account(client)
    r = _put(client, acct["id"], b"<svg/>", "image/svg+xml")
    assert r.status_code == 422 and set(r.json()) == {"code", "message", "trace_id"}
    assert r.json()["code"] == 422 and r.json()["message"] == "logo_unreadable"


def test_stored_and_served_bytes_are_the_normalised_png_not_the_upload(client, db_session):
    acct = _account(client)
    upload = _png(300, 100)
    h = _put(client, acct["id"], upload).json()["logo_hash"]
    db_session.expire_all()
    row = db_session.get(AccountLogoImage, acct["id"])
    expected = normalise_logo(upload)
    assert row.png == expected.png and row.sha256 == expected.sha256 and row.png != upload
    assert client.get(f"/accounts/{acct['id']}/logo.png?v={h}").content == expected.png


def test_delete_logo_204_twice_and_falls_back_to_slug(client):
    acct = _account(client, logo="cathay-united")
    _put(client, acct["id"], _png(300, 100))
    assert client.delete(f"/accounts/{acct['id']}/logo").status_code == 204
    assert client.delete(f"/accounts/{acct['id']}/logo").status_code == 204
    d = client.get(f"/accounts/{acct['id']}").json()
    assert d["logo_hash"] is None and d["logo"] == "cathay-united"


def test_delete_logo_unknown_account_404(client):
    assert client.delete("/accounts/999999/logo").status_code == 404


def test_put_same_bytes_is_idempotent(client, db_session):
    acct = _account(client)
    h1 = _put(client, acct["id"], _png(300, 100)).json()["logo_hash"]
    db_session.expire_all()
    t1 = db_session.get(AccountLogoImage, acct["id"]).updated_at
    h2 = _put(client, acct["id"], _png(300, 100)).json()["logo_hash"]
    db_session.expire_all()
    t2 = db_session.get(AccountLogoImage, acct["id"]).updated_at
    assert h1 == h2 and t2 > t1


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_slow_worker_does_not_block_health_and_all_sync_work_is_on_one_worker_thread(client, monkeypatch):
    from app.main import app
    import app.routers.accounts as accounts_router

    seen: dict[str, int] = {}

    def spy(name, fn):
        def wrapped(*a, **kw):
            seen[name] = threading.get_ident()
            return fn(*a, **kw)
        return wrapped

    acct = _account(client)
    svc = accounts_router.account_logo_service
    monkeypatch.setattr(svc, "normalise_logo", spy("normalise", lambda d, **kw: (time.sleep(1.0), normalise_logo(d, **kw))[1]))
    monkeypatch.setattr(svc, "put_logo", spy("put", svc.put_logo))
    monkeypatch.setattr(accounts_router.ledger_service, "get_account", spy("detail", accounts_router.ledger_service.get_account))
    monkeypatch.setattr(Session, "commit", spy("commit", Session.commit))
    loop_thread = threading.get_ident()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as ac:
        async def upload():
            return await ac.put(f"/accounts/{acct['id']}/logo", content=_png(300, 100),
                                headers={"Content-Type": "image/png"})

        async def health():
            await anyio.sleep(0.1)
            t0 = time.perf_counter()
            r = await ac.get("/health")
            return time.perf_counter() - t0, r.status_code

        up, (elapsed, status) = await asyncio.gather(upload(), health())
    print("threads", seen, "loop", loop_thread, "health_elapsed", elapsed)
    assert up.status_code == 200 and status == 200 and elapsed < 0.5
    assert set(seen) == {"normalise", "put", "commit", "detail"}
    assert len(set(seen.values())) == 1 and loop_thread not in seen.values()
