# 期貨明細 page — design (short)

Status: rev 1.1 2026-11-27 — approved by the owner with four additions (folded below: completeness, positions
source, explicit response models, monthly-wins note). Builds on the 永豐期貨 statement import
(`2026-11-25-sinopac-futures-statement-design.md`, rev 1.7), which already stores every daily statement's fills,
closing links and positions as JSON and every 權益帳 figure as a column.

## Intent

The owner can see futures and options trades and their profit and loss in 投資, not only the card's month totals.
Owner ruling 2026-11-27: a **separate page**, not merged into 交易明細 / 已實現損益 — the existing pages drive
holdings, FIFO cost and net worth for stocks, futures already count in net worth through statement equity, and the
two use different P&L sources (FIFO vs the broker's 平倉損益).

## Goals

- One page `/portfolio/futures` (期貨明細), read-only, one month at a time (default: the latest statement month),
  with a month picker over the months that have statements.
- **Month summary**: 本月期貨平倉損益, 權利金收支, 手續費＋期交稅, 存提淨額. Source: the monthly statement's figures
  (K / G / H / B of its month-end block) when the month has one (authoritative, matches the card's 本月期貨平倉損益), else the sum of that month's daily
  statements, labelled 「日對帳單加總」. When the month's monthly statement lists activity days with no daily
  statement, show 「缺 N 天日對帳單，明細不完整」 with 「摘要以月對帳單為準」 beside it (the summary stays complete; the
  missing days only affect the trade list).
- **Completeness, as the card** (`futures_summary_service._month_block`): `summary.complete` / `summary.reason`. A
  month without a monthly statement (`daily_sum`) whose daily statements carry any finding (daily findings are only
  `prev`; an ack does not clear them) is `complete: false, reason: "findings"` and shows 「可能不完整」 like the
  card's pnl_reason; otherwise `complete: true, reason: null`. A monthly-sourced summary is always complete.
  `missing_days` is counted only when a monthly statement exists (its activity dates without a daily row); for a
  `daily_sum` month it is 0.
- **Trades**: one block per statement day, newest first: each fill with 商品 / 月份 / 履約價 / 權別, 動作 (新買 新賣
  平買 平賣), 口數, 成交價, 期交稅, 手續費, and 平倉損益 (futures) or 權利金 (options, signed); the day's totals; a
  closing fill expands to its 平倉 links (date, 口數, price of the opening fill). `無交易` days show 「當日無交易」;
  deposit / withdrawal days show the amounts.
- **Positions**: from the latest daily statement dated on or before the month's `month_end` (no monthly statement:
  the calendar month's last day) — it may belong to an earlier month — with its date shown (「持倉：MM/DD 日對帳單」):
  商品, 買／賣, 口數, 進場價, 行情價, 未沖銷損益 or 權利金市值. When the month has a monthly statement whose
  `open_positions` differs from that daily statement's position count, add 「月底持倉可能不同」. `null` only when no
  daily statement exists on or before that date.
- Entry points: a 「明細 →」 link on the dashboard futures card and a 「期貨明細 →」 link in the 交易明細 page header.

## Non-goals / anti-goals

- No writes, edits or deletes; nothing touches transactions, holdings, FIFO, snapshots or net worth.
- No merged stock + futures timeline (a later 「含期貨」 toggle could reuse this API).
- No P&L recomputation: every amount is the broker's printed figure.
- No 對帳 matching against accounting entries.

## Approaches considered

1. **Separate page + read-only API (chosen).** No change to existing pages or calculations; month totals reconcile
   to the card.
2. Merge rows into 交易明細 / 已實現損益 at read time. Rejected by the owner: cross-table paging and filters, mixed
   column sets, two P&L sources in one total, regression risk on stable pages.
3. Write fills into the `transaction` table. Rejected: futures would be valued as price × lots and double-count
   with statement equity.

## Design

- **API** (stock-portfolio-service, read scope, same router as `GET /api/portfolio/futures/summary`):
  - `GET /api/portfolio/futures/months` → `{"months": ["YYYY-MM", …]}` newest first (months with a daily or monthly
    row).
  - `GET /api/portfolio/futures/detail?month=YYYY-MM` → `{"month", "summary": {"futures_realized", "premium_net",
    "fees_and_tax", "deposits_net", "source": "monthly" | "daily_sum", "complete", "reason": "findings" | null,
    "missing_days"}, "days": [{"date", "no_trade", "fills": [...], "fills_total", "deposits": [...]}],
    "positions": {"date", "month_end_may_differ", "items": [...]} | null}`; amounts as strings like the summary
    endpoint; 404 for a month with no rows, 422 for a bad month.
  - Both endpoints declare explicit pydantic response models (as `FuturesSummary`, new
    `app/schemas/futures_detail.py`). Fills, deposits and positions are mapped field by field to only what the page
    shows — fill: `kind` (`future` | `option`), `order_no`, `product`, `month`, `strike`, `right`, `action`, `qty`,
    `price`, `tax`, `fee`, `pnl`, `closes` (`date`, `qty`, `price`); deposit: `direction`, `amount`; position:
    `product`, `month`, `strike`, `right`, `side`, `qty`, `entry_price`, `mark_price`, `unrealized`,
    `premium_value`. Any other key in the stored JSON never reaches the response.
  - Built from `futures_statement_day` / `futures_statement_month` only; no schema change, no migration.
- **SPA**: new standalone component under `frontend/src/app/components/portfolio/futures-detail/`, route
  `portfolio/futures`; desktop table per day, phone one card per fill; colours follow the futures card (red = up,
  green = down on this card family); amounts via the existing money pipe.
- **Caddy**: `/portfolio/futures` must be added to the `@hub_spa` route allowlist (`~/workspace/vaultwarden/Caddyfile`)
  before the SPA release, or the route 404s on reload.

## Tests the PR must include

- A month with a monthly statement and no daily statements: summary from the monthly figures, `days` empty,
  positions carried from an earlier month's daily statement with that date.
- A `daily_sum` month with a `prev` finding on one daily: `complete: false`, `reason: "findings"`, SPA shows
  「可能不完整」.
- Stored JSON carrying extra keys: the response models drop them (fills, deposits, positions).
- Positions `null` only with no earlier daily; 「月底持倉可能不同」 when the monthly `open_positions` differs.

## Left to the builder

Exact layout, empty / error states (as the futures card: 「期貨資料載入失敗」), pagination is unnecessary (about 4
daily statements a month).

## Delivery

One PR (API + SPA, small), the usual task reviews and one Multica round; then API deploy (release checkout, no
migration), Caddy allowlist, SPA publish with screenshots of a fictional-data month at 1440 / 390.
