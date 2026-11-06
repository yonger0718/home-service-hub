"""Builders for the e-invoice integration tests. Synthetic data only."""


def invoice(number="AB00000001", issue_date="2026-10-05", seller_tax_id="00000001", seller_name="測試商店股份有限公司",
            total="120", status="開立", items=(("測試品項", "120"),), source="drive-fake-1") -> dict:
    return {"number": number, "issue_date": issue_date, "seller_tax_id": seller_tax_id, "seller_name": seller_name,
            "total_amount": total, "status": status, "source_drive_id": source,
            "items": [{"seq": i, "name": n, "subtotal": t} for i, (n, t) in enumerate(items, start=1)]}


def import_(client, *invoices) -> dict:
    res = client.post("/einvoices/import", json={"invoices": list(invoices)})
    assert res.status_code == 200, res.text
    return res.json()


def enable(client, **einvoice) -> None:
    current = client.get("/settings/reconciliation").json()
    body = {"account_map": current["account_map"], "dirty_enabled": current["dirty_enabled"],
            "auto_pay": current.get("auto_pay") or {}, "einvoice": {"enabled": True, **einvoice}}
    res = client.put("/settings/reconciliation", json=body)
    assert res.status_code == 200, res.text
