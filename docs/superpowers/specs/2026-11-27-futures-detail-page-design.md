# 期貨明細 page — design (short)

Status: draft 2026-11-27, for owner approval. Builds on the 永豐期貨 statement import
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
  statement, show 「缺 N 天日對帳單，明細不完整」 (the 5 archive gaps the owner acked).
- **Trades**: one block per statement day, newest first: each fill with 商品 / 月份 / 履約價 / 權別, 動作 (新買 新賣
  平買 平賣), 口數, 成交價, 期交稅, 手續費, and 平倉損益 (futures) or 權利金 (options, signed); the day's totals; a
  closing fill expands to its 平倉 links (date, 口數, price of the opening fill). `無交易` days show 「當日無交易」;
  deposit / withdrawal days show the amounts.
- **Positions**: the open positions of the month's last daily statement (商品, 買／賣, 口數, 進場價, 行情價,
  未沖銷損益 or 權利金市值), with its date.
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
    "fees_and_tax", "deposits_net", "source": "monthly" | "daily_sum", "missing_days": n}, "days": [{"date",
    "fills": [...as stored, plus "kind": "future" | "option"], "fills_total", "deposits", "no_trade": bool}],
    "positions": {"date", "items": [...]} | null}`; amounts as strings like the summary endpoint; 404 for a month
    with no rows, 422 for a bad month.
  - Built from `futures_statement_day` / `futures_statement_month` only; no schema change, no migration.
- **SPA**: new standalone component under `frontend/src/app/components/portfolio/futures-detail/`, route
  `portfolio/futures`; desktop table per day, phone one card per fill; colours follow the futures card (red = up,
  green = down on this card family); amounts via the existing money pipe.
- **Caddy**: `/portfolio/futures` must be added to the `@hub_spa` route allowlist (`~/workspace/vaultwarden/Caddyfile`)
  before the SPA release, or the route 404s on reload.

## Left to the builder

Exact layout, empty / error states (as the futures card: 「期貨資料載入失敗」), pagination is unnecessary (about 4
daily statements a month).

## Delivery

One PR (API + SPA, small), the usual task reviews and one Multica round; then API deploy (release checkout, no
migration), Caddy allowlist, SPA publish with screenshots of a fictional-data month at 1440 / 390.
