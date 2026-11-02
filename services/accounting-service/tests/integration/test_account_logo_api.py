from io import BytesIO

from PIL import Image

from app.models import AccountLogoImage


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
