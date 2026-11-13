## Why

A receivable or payable can only be settled from an account in its own currency. A debt in one currency that is paid back in another (a USD card purchase made for someone who repays in TWD to a TWD bank account) has no clean booking today: the settle endpoint answers 422, and HomeHub has no manual 結清 either. The workarounds route the money through an unrelated account or count it twice. The add-accounting-entry design deferred this ("Cross-currency settlement is a 2b/3 item"); issue #382.

## What Changes

- API: `POST /entries/{id}/settle` accepts an account in another currency together with `original_amount`, the amount settled in the debt's currency. The settlement row is booked in the account's currency and carries `original_amount` / `original_currency` / `fx_rate` / `fx_source = manual`, the convention cross-currency transfer legs already use.
- Netting: a settlement counts against its debt with its **settled value** in the debt's currency (`amount` in the same currency, `original_amount` across currencies). Open amount, `is_settled`, the open-debt list filter, counterparty `open_amounts` and a loan's `repaid` all use it. Imported cross-currency links without an `original_amount` keep their current behaviour (linked, never net).
- SPA: 新增收款 / 新增還款 offers accounts in every currency; for another currency it asks for both amounts and shows the derived rate. The 已收 / 已還 figure sums settled values instead of adding amounts across currencies.

## Impact

- API: `SettleIn`, `settlement_service`, `ledger_service` (settled sum), `settings_service` (counterparty open amounts), `schedule_read` (loan repaid). No migration: the FX columns already exist on `ledger_entry`.
- SPA: entry detail settle panel and breakdown.
- Hermes `homehub-api` skill text (outside the repo) after the API deploy.
- OpenSpec: `accounting-ledger` (Settlement endpoint, Counterparty model), `accounting-schedules` (Loan summary), `frontend-accounting-ledger` (Entry detail view).
