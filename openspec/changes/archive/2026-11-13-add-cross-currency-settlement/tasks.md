## 1. API

- [x] 1.1 `SettleIn.original_amount`; `settle()` accepts an account in another currency and stores the FX fields (D1, D2, D4)
- [x] 1.2 One settled-value rule (D3) used by `open_amount`, `_settled_sum`, counterparty `open_amounts` (D5) and loan `repaid` (D6)
- [x] 1.3 Tests: collection and repayment across currencies, partial, over-settlement, 422s, imported link without `original_amount`, counterparty and loan figures

## 2. SPA

- [x] 2.1 Settle panel: accounts in every currency, two amounts and the rate line across currencies (D7)
- [x] 2.2 已收 / 已還 sums settled values (D8)
- [x] 2.3 Tests

## 3. Rollout

- [x] 3.1 API deploy, Hermes `homehub-api` skill text, SPA release
