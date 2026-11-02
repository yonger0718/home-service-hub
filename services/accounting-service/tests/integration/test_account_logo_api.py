from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import event

from app.models import AccountLogoImage
from app.services.account_logo_service import delete_logo, get_logo, normalise_logo, put_logo
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
