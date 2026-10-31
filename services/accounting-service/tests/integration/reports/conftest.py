"""The shared reports ledger: accounts, categories, a project and entries in 2026-10, 2026-09 and 2025-10.

Rated October (TWD + USD at 31.1234; the JPY row has no rate and lands in `missing_rates`):

| root  | rows                                                                         | amount       |
|-------|------------------------------------------------------------------------------|--------------|
| 交通  | 22000 長榮航空 (東京旅行) + 12.34 USD Uber (384.0628)                         | 22384.0628   |
| 餐飲  | 晚餐 2860 一蘭 (東京旅行) + 600 鼎泰豐 (朋友, 生日) + 450; 午餐 120 + 95 + 130 + 45.67 USD (1421.4057) | 5676.4057 |
| 購物  | 1590 無印良品 − 490 refund (10-20)                                            | 1100         |
| (none)| 15 fee                                                                       | 15           |

October totals: expense 27370 TWD + 58.01 USD (1805.4684) = 29175.4684 (Σ rows 29175.4685 — per-root rounding),
income 52000 (薪資), refund 490, count 13; 東京旅行 24860. No-merchant spend: 95 + 130 + 450 + 15 = 690 (4 rows).
Weekend rows: 10-03, 10-04 (+ the JPY row), 10-10, 10-17.

September: 薪資 52000; 晚餐 380 鼎泰豐; 午餐 110; 交通 100 台鐵 and a 300 refund (→ −200); 購物 3000 機場免稅店
(東京旅行). 2025-10: 薪資 50000; 晚餐 520 鼎泰豐; 午餐 150 → expense 670.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models import Account, FxRate, Preference
from tests.helpers import make_entry

USD_RATE = Decimal("31.1234")


@pytest.fixture()
def ledger(client, db_session, today):
    today(date(2026, 10, 15))
    db_session.add_all([
        Preference(main_currency="TWD"),
        FxRate(date=date(2026, 10, 1), base="USD", quote="TWD", rate=USD_RATE, source="test"),
    ])
    db_session.commit()

    def post(path: str, body: dict) -> dict:
        response = client.post(path, json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def account(name: str, currency: str) -> int:
        return post("/accounts", {"name": name, "currency": currency})["id"]

    def category(name: str, kind: str = "expense", parent: int | None = None, color: str | None = None) -> int:
        return post("/categories", {"name": name, "kind": kind, "parent_id": parent, "color": color})["id"]

    wallet, yen, usd = account("錢包", "TWD"), account("日幣", "JPY"), account("美金", "USD")
    food = category("餐飲", color="#ff8800")
    dinner, lunch = category("晚餐", parent=food, color="#aa0000"), category("午餐", parent=food)
    shopping, transport = category("購物", color="#3366ff"), category("交通")
    salary = category("薪資", kind="income", color="#00aa00")
    trip = post("/projects", {"name": "東京旅行"})["id"]

    def entry(account_id: int, amount: str, day: str, *, kind: str = "expense", **fields) -> int:
        body = {"account_id": account_id, "kind": kind, "amount": amount, "entry_date": day, **fields}
        return post("/entries", body)["id"]

    # 2026-10
    entry(wallet, "2860", "2026-10-03", category_id=dinner, project_id=trip, merchant="一蘭", tags=["旅行"])
    entry(wallet, "22000", "2026-10-04", category_id=transport, project_id=trip, merchant="長榮航空", tags=["旅行"])
    entry(yen, "5000", "2026-10-04", category_id=shopping, project_id=trip, merchant="唐吉訶德")
    entry(wallet, "52000", "2026-10-05", kind="income", category_id=salary)
    entry(wallet, "120", "2026-10-06", category_id=lunch, merchant="麥當勞")
    entry(wallet, "95", "2026-10-08", category_id=lunch)
    birthday = entry(wallet, "600", "2026-10-10", category_id=dinner, merchant="鼎泰豐", tags=["朋友", "生日"])
    purchase = entry(wallet, "1590", "2026-10-13", category_id=shopping, merchant="無印良品")
    entry(wallet, "130", "2026-10-14", category_id=lunch)
    post("/transfers", {"from_account_id": wallet, "to_account_id": usd, "out_amount": "3112", "in_amount": "100",
                        "entry_date": "2026-10-15"})
    entry(wallet, "450", "2026-10-17", category_id=dinner)
    entry(usd, "12.34", "2026-10-22", category_id=transport, merchant="Uber")
    entry(usd, "45.67", "2026-10-23", category_id=lunch, merchant="Starbucks")
    # 2026-09
    entry(wallet, "52000", "2026-09-05", kind="income", category_id=salary)
    entry(wallet, "380", "2026-09-12", category_id=dinner, merchant="鼎泰豐")
    entry(wallet, "110", "2026-09-15", category_id=lunch)
    entry(wallet, "100", "2026-09-18", category_id=transport, merchant="台鐵")
    entry(wallet, "3000", "2026-09-28", category_id=shopping, project_id=trip, merchant="機場免稅店")
    # 2025-10
    entry(wallet, "50000", "2025-10-05", kind="income", category_id=salary)
    entry(wallet, "520", "2025-10-11", category_id=dinner, merchant="鼎泰豐")
    entry(wallet, "150", "2025-10-20", category_id=lunch)

    # refund / fee rows are not EditableKind: written directly, as tests/integration/test_entries_api.py does; a
    # refund counts on its own entry_date and category (no join through refunds_entry_id).
    wallet_row = db_session.get(Account, wallet)
    make_entry(db_session, wallet_row, "490", kind="refund", category_id=shopping, merchant="無印良品",
               entry_date=date(2026, 10, 20))
    make_entry(db_session, wallet_row, "-15", kind="fee", entry_date=date(2026, 10, 21))
    make_entry(db_session, wallet_row, "300", kind="refund", category_id=transport, merchant="台鐵",
               entry_date=date(2026, 9, 25))
    db_session.commit()

    return SimpleNamespace(
        wallet=wallet, yen=yen, usd=usd, food=food, dinner=dinner, lunch=lunch, shopping=shopping,
        transport=transport, salary=salary, trip=trip, birthday=birthday, purchase=purchase,
    )
