from datetime import date, timedelta
from decimal import Decimal

from app.models.broker_account import BrokerAccount, BrokerEnum
from app.models.cash_transaction import CashTransaction, CashTxnSource, CashTxnType
from app.models.fx_rate import FxRate
from app.routers import accounts as accounts_router


def _account(
    *,
    broker: BrokerEnum = BrokerEnum.FIRSTRADE,
    nickname: str = "Main",
    currency: str = "USD",
    opening_balance: str = "0",
) -> BrokerAccount:
    return BrokerAccount(
        broker=broker,
        nickname=nickname,
        currency=currency,
        opening_balance=Decimal(opening_balance),
        opening_date=date(2026, 1, 1),
        is_active=True,
    )


def test_create_account_persists_and_duplicate_returns_409(client) -> None:
    payload = {
        "broker": "firstrade",
        "nickname": "Firstrade Main",
        "currency": "usd",
        "opening_balance": "12345.67",
        "opening_date": "2026-01-01",
        "is_active": True,
    }

    response = client.post("/api/portfolio/accounts/", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] > 0
    assert body["currency"] == "USD"
    assert body["native_balance"] == "12345.6700"

    duplicate = client.post("/api/portfolio/accounts/", json=payload)
    assert duplicate.status_code == 409


def test_patch_account_updates_allowed_fields_and_ignores_broker_currency(client, db_session) -> None:
    account = _account(currency="USD")
    db_session.add(account)
    db_session.commit()

    response = client.patch(
        f"/api/portfolio/accounts/{account.id}",
        json={
            "nickname": "Updated",
            "broker": "cathay",
            "currency": "TWD",
            "opening_balance": "200",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["nickname"] == "Updated"
    assert body["broker"] == "firstrade"
    assert body["currency"] == "USD"
    assert body["opening_balance"] == "200.0000"


def test_get_accounts_in_currency_computes_target_balance(client, db_session) -> None:
    usd = _account(nickname="US", currency="USD", opening_balance="1000")
    twd = _account(broker=BrokerEnum.CATHAY, nickname="TW", currency="TWD", opening_balance="30000")
    db_session.add_all([usd, twd])
    db_session.add(
        FxRate(
            date=date(2026, 6, 1),
            base_currency="USD",
            quote_currency="TWD",
            rate=Decimal("32.0"),
            source="test",
        )
    )
    db_session.commit()

    response = client.get("/api/portfolio/accounts/", params={"in_currency": "TWD"})

    assert response.status_code == 200
    body = response.json()
    assert body["target_currency"] == "TWD"
    assert Decimal(body["total_target_balance"]) == Decimal("62000")
    assert body["skipped_currencies"] == []
    assert {item["nickname"]: Decimal(item["target_balance"]) for item in body["items"]} == {
        "US": Decimal("32000"),
        "TW": Decimal("30000"),
    }


def test_create_cash_transaction_manual_source_currency_mismatch_and_duplicate(client, db_session) -> None:
    account = _account(currency="USD")
    db_session.add(account)
    db_session.commit()
    payload = {
        "txn_date": "2026-06-01",
        "type": "deposit",
        "amount": "5000",
        "currency": "USD",
        "note": "Wire from bank",
        "source": "csv_import",
    }

    response = client.post(f"/api/portfolio/accounts/{account.id}/cash-transactions", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "manual"
    assert body["currency"] == "USD"
    assert body["amount"] == "5000.0000"

    mismatch = client.post(
        f"/api/portfolio/accounts/{account.id}/cash-transactions",
        json={**payload, "txn_date": "2026-06-02", "currency": "TWD"},
    )
    assert mismatch.status_code == 400

    duplicate = client.post(f"/api/portfolio/accounts/{account.id}/cash-transactions", json=payload)
    assert duplicate.status_code == 409


def test_delete_manual_cash_transaction_returns_200_count_drops_and_balance_shifts(client, db_session) -> None:
    account = _account(opening_balance="100000")
    db_session.add(account)
    db_session.commit()
    deleted = CashTransaction(
        account_id=account.id,
        txn_date=date(2026, 6, 1),
        type=CashTxnType.DEPOSIT,
        amount=Decimal("30000"),
        currency="USD",
        source=CashTxnSource.MANUAL,
        import_fingerprint="delete-manual",
    )
    kept = CashTransaction(
        account_id=account.id,
        txn_date=date(2026, 6, 2),
        type=CashTxnType.DEPOSIT,
        amount=Decimal("5000"),
        currency="USD",
        source=CashTxnSource.MANUAL,
        import_fingerprint="delete-kept",
    )
    db_session.add_all([deleted, kept])
    db_session.commit()

    before_accounts = client.get("/api/portfolio/accounts/")
    before_list = client.get(f"/api/portfolio/accounts/{account.id}/cash-transactions")

    response = client.delete(f"/api/portfolio/accounts/{account.id}/cash-transactions/{deleted.id}")

    after_accounts = client.get("/api/portfolio/accounts/")
    after_list = client.get(f"/api/portfolio/accounts/{account.id}/cash-transactions")
    assert before_accounts.status_code == 200
    assert before_accounts.json()["items"][0]["native_balance"] == "135000.0000"
    assert before_list.status_code == 200
    assert before_list.json()["total"] == 2
    assert response.status_code == 200
    assert response.json() == {"deleted_id": deleted.id}
    assert after_accounts.status_code == 200
    assert after_accounts.json()["items"][0]["native_balance"] == "105000.0000"
    assert after_list.status_code == 200
    after_body = after_list.json()
    assert after_body["total"] == 1
    assert {item["id"] for item in after_body["items"]} == {kept.id}
    assert db_session.get(CashTransaction, deleted.id) is None


def test_delete_auto_derive_cash_transaction_returns_403(client, db_session) -> None:
    account = _account()
    db_session.add(account)
    db_session.commit()
    row = _cash_row(
        account.id,
        date(2026, 6, 1),
        CashTxnType.BUY_SETTLE,
        "-1000",
        "delete-auto",
        related_transaction_id=100,
        source=CashTxnSource.AUTO_DERIVE,
    )
    db_session.add(row)
    db_session.commit()

    response = client.delete(f"/api/portfolio/accounts/{account.id}/cash-transactions/{row.id}")

    assert response.status_code == 403
    assert response.json() == {"detail": "only manual cash transactions can be deleted"}
    assert db_session.get(CashTransaction, row.id) is not None


def test_delete_csv_import_cash_transaction_returns_403(client, db_session) -> None:
    account = _account()
    db_session.add(account)
    db_session.commit()
    row = _cash_row(
        account.id,
        date(2026, 6, 1),
        CashTxnType.BUY_SETTLE,
        "-1000",
        "delete-csv",
        related_transaction_id=100,
        source=CashTxnSource.CSV_IMPORT,
    )
    db_session.add(row)
    db_session.commit()

    response = client.delete(f"/api/portfolio/accounts/{account.id}/cash-transactions/{row.id}")

    assert response.status_code == 403
    assert response.json() == {"detail": "only manual cash transactions can be deleted"}
    assert db_session.get(CashTransaction, row.id) is not None


def test_delete_backfill_cash_transaction_returns_403(client, db_session) -> None:
    account = _account()
    db_session.add(account)
    db_session.commit()
    row = _cash_row(
        account.id,
        date(2026, 6, 1),
        CashTxnType.BUY_SETTLE,
        "-1000",
        "backfill|transactions|100|settle",
        related_transaction_id=100,
        source=CashTxnSource.AUTO_DERIVE,
    )
    db_session.add(row)
    db_session.commit()

    response = client.delete(f"/api/portfolio/accounts/{account.id}/cash-transactions/{row.id}")

    assert response.status_code == 403
    assert response.json() == {"detail": "only manual cash transactions can be deleted"}
    assert db_session.get(CashTransaction, row.id) is not None


def test_delete_missing_cash_transaction_returns_404(client, db_session) -> None:
    account = _account()
    db_session.add(account)
    db_session.commit()

    response = client.delete(f"/api/portfolio/accounts/{account.id}/cash-transactions/99999")

    assert response.status_code == 404


def test_delete_cash_transaction_value_error_returns_422(client, monkeypatch) -> None:
    def fail_delete(db_session, account_id: int, txn_id: int) -> int:
        raise ValueError("invalid delete state")

    monkeypatch.setattr(accounts_router.cash_account_service, "delete_manual_cash_transaction", fail_delete)

    response = client.delete("/api/portfolio/accounts/1/cash-transactions/1")

    assert response.status_code == 422
    assert response.json()["message"] == "invalid delete state"


def test_delete_wrong_account_cash_transaction_returns_404(client, db_session) -> None:
    account = _account(nickname="Main")
    other = _account(nickname="Other")
    db_session.add_all([account, other])
    db_session.commit()
    row = CashTransaction(
        account_id=other.id,
        txn_date=date(2026, 6, 1),
        type=CashTxnType.DEPOSIT,
        amount=Decimal("30000"),
        currency="USD",
        source=CashTxnSource.MANUAL,
        import_fingerprint="wrong-account",
    )
    db_session.add(row)
    db_session.commit()

    response = client.delete(f"/api/portfolio/accounts/{account.id}/cash-transactions/{row.id}")

    assert response.status_code == 404
    assert db_session.get(CashTransaction, row.id) is not None


def test_fx_refresh_invalid_base_currency_returns_422(client) -> None:
    response = client.post("/api/portfolio/fx/refresh", json={"base_currencies": ["../foo"]})

    assert response.status_code == 422
    assert response.json()["message"] == "invalid currency code"


def test_cash_transactions_endpoint_paginates(client, db_session) -> None:
    account = _account()
    db_session.add(account)
    db_session.commit()
    start = date(2026, 1, 1)
    db_session.add_all(
        [
            CashTransaction(
                account_id=account.id,
                txn_date=start + timedelta(days=idx),
                type=CashTxnType.DEPOSIT,
                amount=Decimal("1"),
                currency="USD",
                source=CashTxnSource.MANUAL,
                import_fingerprint=f"seed-{idx}",
            )
            for idx in range(200)
        ]
    )
    db_session.commit()

    response = client.get(
        f"/api/portfolio/accounts/{account.id}/cash-transactions",
        params={"limit": 50, "offset": 50},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 50
    assert body["total"] == 200
    assert body["offset"] == 50
    assert body["limit"] == 50


def _cash_row(
    account_id: int,
    txn_date: date,
    type_: CashTxnType,
    amount: str,
    fingerprint: str,
    *,
    related_transaction_id: int | None = None,
    related_dividend_id: int | None = None,
    source: CashTxnSource = CashTxnSource.AUTO_DERIVE,
) -> CashTransaction:
    return CashTransaction(
        account_id=account_id,
        txn_date=txn_date,
        type=type_,
        amount=Decimal(amount),
        currency="USD",
        related_transaction_id=related_transaction_id,
        related_dividend_id=related_dividend_id,
        source=source,
        import_fingerprint=fingerprint,
    )


def test_cash_transactions_endpoint_merge_related_groups_and_paginates(client, db_session) -> None:
    account = _account()
    db_session.add(account)
    db_session.commit()
    db_session.add_all(
        [
            _cash_row(
                account.id,
                date(2026, 6, 1),
                CashTxnType.BUY_SETTLE,
                "-100000",
                "buy-settle",
                related_transaction_id=100,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 1),
                CashTxnType.FEE,
                "-285",
                "buy-fee",
                related_transaction_id=100,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 1),
                CashTxnType.TAX,
                "-300",
                "buy-tax",
                related_transaction_id=100,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 4),
                CashTxnType.SELL_SETTLE,
                "50000",
                "sell-settle",
                related_transaction_id=101,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 4),
                CashTxnType.FEE,
                "-22",
                "sell-fee",
                related_transaction_id=101,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 4),
                CashTxnType.TAX,
                "-150",
                "sell-tax",
                related_transaction_id=101,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 3),
                CashTxnType.DIVIDEND_CASH,
                "75",
                "dividend",
                related_dividend_id=10,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 2),
                CashTxnType.DEPOSIT,
                "500",
                "manual",
                source=CashTxnSource.MANUAL,
            ),
        ]
    )
    db_session.commit()

    unmerged = client.get(
        f"/api/portfolio/accounts/{account.id}/cash-transactions",
        params={"limit": 20},
    )
    merged = client.get(
        f"/api/portfolio/accounts/{account.id}/cash-transactions",
        params={"merge_related": True, "limit": 3, "offset": 0},
    )
    next_page = client.get(
        f"/api/portfolio/accounts/{account.id}/cash-transactions",
        params={"merge_related": True, "limit": 3, "offset": 3},
    )

    assert unmerged.status_code == 200
    assert unmerged.json()["total"] == 8
    assert merged.status_code == 200
    body = merged.json()
    assert body["total"] == 4
    assert len(body["items"]) == 3
    assert [item["type"] for item in body["items"]] == ["trade", "dividend_cash", "deposit"]
    sell_group = body["items"][0]
    assert sell_group["id"] == -101
    assert sell_group["amount"] == "49828.0000"
    assert sell_group["txn_date"] == "2026-06-04"
    assert [leg["type"] for leg in sell_group["child_legs"]] == ["sell_settle", "fee", "tax"]
    assert body["items"][1]["child_legs"] is None
    assert body["items"][2]["child_legs"] is None
    assert next_page.status_code == 200
    next_body = next_page.json()
    assert next_body["total"] == 4
    assert len(next_body["items"]) == 1
    assert next_body["items"][0]["id"] == -100


def test_cash_transactions_endpoint_merge_related_type_filter_fee(client, db_session) -> None:
    account = _account()
    db_session.add(account)
    db_session.commit()
    db_session.add_all(
        [
            _cash_row(
                account.id,
                date(2026, 6, 1),
                CashTxnType.BUY_SETTLE,
                "-100000",
                "filter-buy-settle",
                related_transaction_id=100,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 1),
                CashTxnType.FEE,
                "-285",
                "filter-buy-fee",
                related_transaction_id=100,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 4),
                CashTxnType.SELL_SETTLE,
                "50000",
                "filter-sell-settle",
                related_transaction_id=101,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 4),
                CashTxnType.FEE,
                "-22",
                "filter-sell-fee",
                related_transaction_id=101,
            ),
            _cash_row(
                account.id,
                date(2026, 6, 5),
                CashTxnType.FEE,
                "-5",
                "filter-standalone-fee",
                source=CashTxnSource.MANUAL,
            ),
        ]
    )
    db_session.commit()

    response = client.get(
        f"/api/portfolio/accounts/{account.id}/cash-transactions",
        params={"merge_related": True, "type": "fee", "limit": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["type"] for item in body["items"]] == ["fee", "trade", "trade"]
    assert [item["id"] for item in body["items"][1:]] == [-101, -100]
    assert all("fee" in {leg["type"] for leg in item["child_legs"]} for item in body["items"][1:])


def test_cash_transactions_endpoint_rejects_trade_type_filter(client, db_session) -> None:
    account = _account()
    db_session.add(account)
    db_session.commit()

    response = client.get(
        f"/api/portfolio/accounts/{account.id}/cash-transactions",
        params={"merge_related": True, "type": "trade"},
    )

    assert response.status_code == 422


def test_balance_history_endpoint_shape(client, db_session) -> None:
    account = _account(opening_balance="10")
    db_session.add(account)
    db_session.commit()
    db_session.add(
        CashTransaction(
            account_id=account.id,
            txn_date=date(2026, 6, 2),
            type=CashTxnType.DEPOSIT,
            amount=Decimal("100"),
            currency="USD",
            source=CashTxnSource.MANUAL,
            import_fingerprint="history-seed",
        )
    )
    db_session.commit()

    response = client.get(
        f"/api/portfolio/accounts/{account.id}/balance-history",
        params={"date_from": "2026-06-01", "date_to": "2026-06-03"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == account.id
    assert body["currency"] == "USD"
    assert body["points"] == [
        {"date": "2026-06-01", "balance": "10.0000"},
        {"date": "2026-06-02", "balance": "110.0000"},
        {"date": "2026-06-03", "balance": "110.0000"},
    ]
