## Context

`settlement_service.settle` refuses an account whose currency differs from the target's (`_same_currency_account`). Every place that sums a debt's settlements only adds settlements in the debt's own currency: `settlement_service.open_amount`, `ledger_service._settled_sum` (open-debt filter, list and detail `open_amount`, `is_settled`) and `settings_service._open_amounts` (counterparty totals, which leave cross-currency links out). `schedule_read.loan_summary` adds `abs(amount)` of every settlement whatever its currency. The SPA settle panel lists only accounts in the entry's currency, and its 已收 / 已還 figure adds `amount` across the settled-by rows.

Production data (counts only, 2026-11-13): 11 settlements are linked to a debt in another currency, all from the MOZE backup import, none with `original_amount`, all on closed (`is_closed`) debts. No refund is linked across currencies. 5 settlements carry `original_amount`, none of them across currencies.

## Goals / Non-Goals

**Goals:** settle a receivable or payable from an account in any currency with the owner's two amounts; every open-amount figure nets that settlement in the debt's currency; existing data reads the same.

**Non-goals:** cross-currency refunds (no case yet; refunds stay same-currency); a manual 結清 / write-off; FX rates fetched for settlements (the owner types both amounts); schedule-posted repayments in another currency (schedule lines keep `currency = account currency`); a foreign-transaction fee proposal on settlements.

## Decisions

**D1 — Request shape.** `SettleIn` gains `original_amount` (unsigned `Money`, optional). Same currency: it must be absent, else 422 on `original_amount` (no silent ignore: a client sending it expects a conversion). Other currency: it is required, else 422 on `original_amount`. The old 422 on `currency` goes away. `amount` stays the amount that moved in the chosen account.

**D2 — Stored row.** The settlement keeps `currency = account.currency`, `amount = sign × amount`. Across currencies it also stores `original_amount = sign × original_amount`, `original_currency = target.currency`, `fx_rate = |amount| / |original_amount|` quantized to 10 decimals half-up, `fx_source = 'manual'`. This is the transfer-leg convention (spec "Transfer endpoint"), so the existing FX line renders on the settlement's own detail with no SPA change.

**D3 — Settled value.** One rule for every reader: a settlement's settled value in its target's currency is `amount` when `currency = target.currency`; `original_amount` when `currency ≠ target.currency` and `original_currency = target.currency`; otherwise nothing. The last branch keeps the 11 imported links out of every sum, as today (their debts are closed anyway). The open amount is `|target.amount + Σ settled values|`.

**D4 — Over-settlement.** The cap compares the request's settled value (`original_amount` across currencies) with the open amount, inside the existing `FOR UPDATE` lock.

**D5 — Counterparty open amounts.** A settlement linked across currencies with a settled value is summed into the bucket of its target's currency with that value; without one it stays out. Closed debts and their settlements stay out as today.

**D6 — Loan repaid.** `repaid` sums the absolute settled values. For a same-currency loan this equals today's figure.

**D7 — SPA panel.** The 帳戶 select lists every non-archived account: the entry's currency first (current order), then the others with the currency code after the name. Choosing an account in another currency shows two amount fields: the amount in the account's currency (empty by default) and 抵銷 in the entry's currency (default the open amount, capped by it), plus a read-only conversion line in the entry detail's FX format (`fxConversionLine`: `US$20.00 × 32 = $640`, shown once both amounts are positive). Same-currency stays one field, unchanged. The request sends `original_amount` only across currencies.

**D8 — 已收 / 已還.** The breakdown sums the settled values of `settled_by` with D3's rule, in the entry's currency. Related lines keep each settlement's own amount and currency.

## Risks / Trade-offs

- The owner types both amounts, so a typo in either books a wrong rate. The rate line in the panel makes it visible before saving; the settlement is deleted and re-settled to fix it, like today.
- `original_amount` is also used by the entry form's FX path (`resolve_fx`) for ordinary entries. Settlements never go through it (`_assert_form_kind` refuses them), so the two meanings do not meet.
