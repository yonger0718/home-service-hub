# Portfolio Integration Phase 2 — IB statements by Flex Query: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Status: rev 4 (2026-10-20) after the AGENT-184 scoped re-check of rev 3 `d2b2a0e` (CHANGES REQUIRED, 3 Musts): the
candidates figure counts committable rows only (Must 1); the legacy pass is fingerprint-first, pairs one-to-one,
and a paired existing row ADOPTS the Flex fingerprint, so no later import can re-create a paired event and a rerun
is plain fingerprint dedupe (Must 2); the redacting filter formats `exc_info` itself before any handler does (Must
3); Shoulds: CLI tests drain stdout per call, the worker's Flex response parser shares Task 2's UTF-8 + DTD guard,
the scope tests separate date from currency / fee / tax. Lead ruling (AGENT-183, 2026-10-20): the Flex fingerprint
note `"<description> ib:<id>"` is accepted as a change of fingerprint INPUT with the functions unchanged; IB CSV
uploads and Flex are mutually exclusive once Phase 2 is live (plan + spec line below).
Rev 3 (2026-10-20) after the AGENT-184 review of rev 2 `3eb4441` (CHANGES REQUIRED, 6 Musts): cash amounts
keep the source sign (Must 1); the legacy pass runs only with an explicit `legacy_until`, pairs one-to-one on a key
with currency / fee / tax (cash: symbol), reports ambiguity, and Flex fingerprints carry the Flex id (Must 2); the
route reports the original `parsed` (Must 3); the summary carries an allowlist of error codes with counts, never
reason text (Must 4); 429 / 408 are transient and IB status codes follow a table (Must 5); the empty-token test
case is fixed (Must 6); Shoulds: whole-document DTD refusal + UTF-8 only, child-logger / traceback token tests and a
redacting filter, a Flex lock + unique file names + a narrow unlatch rule, dry-run acceptance by `candidates`.
Rev 2 (2026-10-20): market from Flex `listingExchange`. Rev 1 (2026-10-20): first draft. Phase 2 touches only `services/stock-portfolio-service` and the statement worker
(`services/accounting-service/worker`); it does not touch the ledger, so it may run before the 2026-10-24 cutover.

**Goal:** A daily worker timer fetches the owner's IB Flex Query statement (XML, last 30 calendar days) and imports
it into the portfolio through the same persistence path as the manual IB CSV, idempotently, with the token never
leaving the credentials file and the secret-free run summary on the journal.

**Architecture:** (1) A new portfolio adapter `broker_ib_flex_service.parse(raw_bytes) -> ParseResult` maps the
Flex XML sections onto the existing `ParsedRow` payload shapes of `broker_ib_service` (the CSV parser), so the
existing `import_service.commit_transactions` → `_commit_broker_rows` / `cash_flow_service.write_cash_flows`
path persists and de-duplicates by the existing fingerprints; for the one-off backfill over the manual-import era
a pre-pass `legacy_duplicates` (only when the caller passes `legacy_until`) pairs rows one-to-one with existing
rows by business key and drops the matches. (2) A new route
`POST /api/portfolio/imports/ib-flex` wraps it with the same dry-run and recalc-scheduling as `/imports/csv`.
(3) A new worker module `worker/flex.py` + subcommand `python -m worker flex` does SendRequest → GetStatement,
stores the XML under the worker's state dir, POSTs it to the portfolio, latches non-transient failures in
`flex-disabled.json`, and prints one JSON line of codes and counts. (4) systemd user units
`homehub-portfolio-flex.{service,timer}` run it daily.

**Tech Stack:** Python 3.13, FastAPI 0.134, SQLAlchemy, stdlib `xml.etree.ElementTree` (no new dependency;
`lxml` is in requirements but unused — stay with the stdlib), httpx (worker), `httpx.MockTransport` in worker tests,
pytest.

**Spec:** `docs/superpowers/specs/2026-10-19-portfolio-integration-design.md` §7 (rev 3 amends §7: XML instead of
CSV, the token travels only in the Flex `t` query parameter, the section list, the legacy-duplicate pass); §10
"Worker" and "Portfolio: Flex adapter" tests; §11 step 3.

Verified facts this plan rests on (2026-10-20, prod and the owner's live query):
- The owner's Flex Query is XML, `Last30CalendarDays`, date `yyyy-MM-dd`, time separator `;`, currency rates
  off, sections `OpenPositions`, `Trades` (level `EXECUTION`), `CorporateActions`, `CashTransactions` (level
  `DETAIL`), `Transfers`. Root `<FlexQueryResponse queryName type>` → `<FlexStatements count>` →
  `<FlexStatement accountId fromDate toDate period whenGenerated>` → one element per section → row elements
  `<Trade …/>`, `<CashTransaction …/>`, `<OpenPosition …/>`, …, every value an attribute.
- A `Trade` carries `tradeID`, `transactionID`, `buySell` (`BUY`/`SELL`), `quantity` (negative on SELL),
  `tradePrice`, `proceeds`, `ibCommission` (negative), `ibCommissionCurrency`, `taxes`, `currency`,
  `fxRateToBase`, `assetCategory` (`STK`), `subCategory` (`ETF`), `listingExchange` (`LSEETF`), `symbol`,
  `description` (the same 28-character instrument text IB prints as 說明 in the Activity CSV), `tradeDate`,
  `settleDateTarget`, `dateTime` (`yyyy-MM-dd;HH:mm:ss`), `levelOfDetail`.
- A `CashTransaction` carries `type` (`Deposits/Withdrawals`, `Dividends`, `Withholding Tax`, `Broker Interest
  Received`, `Broker Interest Paid`, `Other Fees`, …), `amount` (signed: withdrawals, taxes and fees negative),
  `currency`, `dateTime` (date or date;time), `settleDate`, `reportDate`, `description`, `symbol`, `levelOfDetail`
  (`DETAIL`), `transactionID` (unique per event). `broker_cash_flows.amount` is stored SIGNED by the existing
  parsers (`broker_schwab_service.py:96` writes a withdrawal as `-abs(amount)`) and `cash_flow_service.list_balances`
  sums it (`:224-228`), so the adapter keeps the source sign; `type` only classifies.
- Fingerprint parity: recomputing `import_service._broker_transaction_fingerprint` from the Flex sample's
  2026-06-02 trades with `market = "US"` (the CSV parser's guess) and `note = description` reproduces the three
  fingerprints stored by the June CSV import exactly (3/3) — so Flex `description` IS the CSV 說明 text. The
  stored rows carry `market = LSE` (corrected by hand after the import) with the `US` fingerprint, and
  `symbol_map` has no row for the IB symbols. The Flex row knows the venue (`listingExchange="LSEETF"`), so the
  adapter sets `market` from it; the resulting fingerprint differs from the stored `US` one, and the business-key
  pass (market-free key) is what keeps the overlap from duplicating.
- The IB deposit imported from the CSV carries the CSV 說明 as `note`; the Flex row's `description` is a
  different (English) text, so the cash-flow fingerprints differ: hence the business-key pass covers cash flows.
- `fx_rates` holds USD and GBP daily rows from yfinance through the previous business day; `_exact_fx_rate`
  wants the exact trade date, else the row is skipped with `missing FX rate for <date> <CUR>` — the 30-day window
  re-imports it the next day.
- Flex Web Service: `GET {base}/SendRequest?t=<token>&q=<queryId>&v=3` →
  `<FlexStatementResponse><Status>Success</Status><ReferenceCode>…</ReferenceCode><Url>…</Url></FlexStatementResponse>`
  (or `<Status>Fail</Status><ErrorCode>1020</ErrorCode><ErrorMessage>…</ErrorMessage>`); then
  `GET {base}/GetStatement?q=<referenceCode>&t=<token>&v=3` → the `FlexQueryResponse` document, or a
  `FlexStatementResponse` with `ErrorCode` `1019` ("Statement generation in progress") to retry. IB accepts the
  token only as the `t` query parameter. Base `https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService`.

## Global Constraints

- Token (`IB_FLEX_TOKEN`) and query id live in `~/.config/homehub-portfolio-flex.env` (mode 0600, the worker
  user's) and nowhere else: never in the repo, logs, exception text, latch files, run summaries, API bodies or
  the portfolio request. The token appears in exactly one place at runtime: the `t` query parameter of the two
  Flex requests (spec §7 rev 3).
- Run summary: one JSON line of codes and counts (no text, names, paths, amounts, symbols). Exit codes as the
  worker's convention: 0 success, 1 failed / latched, 2 refused or config error (service README "exit codes").
- Files the worker writes: 0600 in 0700 directories (`os.umask(0o077)` is set by `cli.main`; state writes use
  `os.open(…, O_EXCL, 0o600)` as `parser.Gate._write` does).
- Portfolio: no new dependency; XML parsed with `xml.etree.ElementTree`; a document containing `<!DOCTYPE` is
  refused before parsing. Upload limit 5 MiB (`_MAX_CSV_BYTES`). The route is unauthenticated like the other
  import routes (localhost only, behind the gateway).
- Fingerprints are the existing functions: `import_service._broker_transaction_fingerprint` and
  `cash_flow_service.cash_flow_fingerprint`, called with `note = "<description> ib:<tradeID|transactionID>"` (so two
  identical same-day fills stay two rows — the CSV import could not tell them apart, Flex can) and market `LSE` when
  `listingExchange` is `LSE`, `LSEETF` or `LSEIOB`, else `broker_ib_service._market_for(symbol, currency)`. The
  stored `name` is the plain `description`. Never introduce a third fingerprint function. Flex rows therefore never
  share a fingerprint with CSV-imported rows; the overlap with the manual-import era is handled once, by the
  `legacy_until` pass, and never by the daily run.
- Cash amounts are signed as IB reports them; `cash_flow_type` classifies. No `abs()` on cash.
- Once Phase 2 is live, the IB Transfer-History CSV is never uploaded again (`/imports/csv` for IB and
  `/imports/ib-flex` are mutually exclusive): a CSV row and the same Flex row carry different notes, so a CSV
  re-upload would duplicate what Flex imported (and what the legacy pass adopted). README and spec say so.
- Summary and journal: codes from a fixed allowlist plus counts; a reason string from the API is mapped to a code,
  never copied.
- Fixtures are synthetic: the Flex sample in the repo mirrors the existing `tests/unit/fixtures/ib_sample.csv`
  rows (ACWD 1 @ 325.05, AVGS 30 @ 27.915, ACWD 6 @ 325.2, fee 1.78, 2026-06-02; deposit 3000 on 2026-06-01) with
  invented ids; real exports stay on the worker host.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; the PR body ends with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Branch `feat/portfolio-phase2-ib-flex` (worktree `/home/opc/workspace/home-hub-ibflex`), base `origin/main`.

## Review Focus

1. **A trade dated after the latest `fx_rates` row** (yfinance lags a day): the row is skipped with `missing FX rate
   for <date> USD`, nothing else in the file is lost, and the next day's run (30-day window) imports it — Task 3
   `test_ib_flex_import_retries_a_row_once_its_fx_rate_exists`.
2. **A query configured with `ORDER` as well as `EXECUTION` rows** (level of detail misconfigured): only
   `EXECUTION` rows import; nothing is double-counted — Task 1 `test_parse_trades_imports_execution_rows_only`.
3. **GetStatement stays at 1019 past the poll budget**: a transient failure (exit 1, no latch, nothing stored,
   nothing posted), tomorrow retries — Task 5 `test_fetch_gives_up_after_the_poll_budget_without_latching`.
4. **The token is rotated or revoked** (SendRequest `Fail` 1020): the latch `flex-disabled.json` is written with the
   code only, the summary says `flex_disabled`, every later run exits 1 without calling IB until
   `python -m worker flex --unlatch` succeeds — Task 6 `test_run_latches_on_a_failed_send_request_and_only_a_plain_unlatch_run_clears_it`.
5. **The portfolio service is down when the XML is posted**: the XML is already saved under the state dir, the
   failure is transient (no latch), `--from-file <saved>` re-posts it later — Task 6
   `test_run_keeps_the_saved_xml_when_the_portfolio_post_fails`.

---

## File structure

Portfolio (`services/stock-portfolio-service`):
- Create `app/services/broker_ib_flex_service.py` — the adapter: XML guards, section walk, Trade and
  CashTransaction mapping onto `ParsedRow` payloads, unsupported sections as `ParseError`s, and
  `legacy_duplicates(db, parsed)`.
- Modify `app/routers/imports.py` — route `POST /ib-flex` (multipart `file`, `dry_run`), reusing `_read_upload`,
  `import_service.commit_transactions`, `_touched_symbols_and_min_trade_date`, `_maybe_schedule_chain`,
  `_serialize_result`.
- Create `tests/unit/fixtures/ib_flex_sample.xml` and `tests/unit/test_broker_ib_flex_service.py`; create
  `tests/integration/test_ib_flex_import.py`.

Worker (`services/accounting-service`):
- Modify `worker/config.py` — six `STATEMENT_FLEX_*` / `STATEMENT_PORTFOLIO_API_URL` keys.
- Modify `worker/parser.py` — lift `Gate._write` into a module function `write_private_json(state_dir, path, data)`
  (Gate keeps calling it).
- Create `worker/flex.py` — `Credentials`, `load_credentials`, `FlexError`, `FlexDisabled`, `fetch`, `run`.
- Modify `worker/cli.py` — subcommand `flex`.
- Create `tests/worker/test_flex.py`.
- Create `deploy/statements/homehub-portfolio-flex.service`, `deploy/statements/homehub-portfolio-flex.timer`;
  modify `deploy/statements/README.md`, `README.md` (service), the spec §7.

Interfaces shared across tasks (exact names):
- `broker_ib_flex_service.parse(raw_bytes: bytes) -> import_service.ParseResult`
- `broker_ib_flex_service.legacy_duplicates(db: Session, parsed: ParseResult, *, until: date, dry_run: bool) -> LegacyResult` (`LegacyResult(pending: ParseResult, pairs: list[LegacyPair])`, `LegacyPair(row_index: int, kind: str, existing_id: int)`)
- `flex.Credentials(token: str, query_id: str)`; `flex.load_credentials(path: Path) -> Credentials`
- `flex.FlexError(code: str, *, transient: bool)`; `flex.FlexDisabled(RuntimeError)`
- `flex.fetch(http: httpx.Client, creds: Credentials, *, base_url: str, attempts: int, interval_s: float, sleep=time.sleep) -> bytes`
- `flex.run(cfg: WorkerConfig, *, flex_http: httpx.Client, portfolio_http: httpx.Client, dry_run: bool = False, query_id: str | None = None, from_file: Path | None = None, legacy_until: date | None = None, unlatch: bool = False, sleep=time.sleep) -> dict`

---

### Task 1: Flex adapter — trades, payload-compatible with the CSV parser

**Files:**
- Create: `services/stock-portfolio-service/app/services/broker_ib_flex_service.py`
- Create: `services/stock-portfolio-service/tests/unit/fixtures/ib_flex_sample.xml`
- Test: `services/stock-portfolio-service/tests/unit/test_broker_ib_flex_service.py`

**Interfaces:**
- Consumes: `import_service.ParsedRow / ParseError / ParseResult / _broker_transaction_fingerprint`
  (`app/services/import_service.py:91-115, 284-314`), `broker_ib_service._market_for(symbol, currency)`
  (`broker_ib_service.py:34`), `models.Broker.IB`, `models.PositionSide.LONG`.
- Produces: `parse(raw_bytes: bytes) -> ParseResult` where every transaction row's payload has exactly the keys
  the CSV parser emits (`broker_ib_service.py:88-104`): `_kind="transaction"`, `broker="IB"`, `symbol`, `market`,
  `name` (= Flex `description` or None), `type`, `position_side="LONG"`, `quantity` (abs), `price`, `currency`,
  `trade_date` (UTC midnight datetime from `tradeDate`), `fee` (abs `ibCommission`), `tax` (abs `taxes`);
  `market` = `"LSE"` when `listingExchange` ∈ `LSE_EXCHANGES = {"LSE", "LSEETF", "LSEIOB"}`, else
  `broker_ib_service._market_for(symbol, currency)` (the CSV rule: `LSE` for GBP, `US` otherwise); plus one extra
  key `fingerprint_note = f"{description} ib:{tradeID}"` (what the fingerprint was computed with; Task 3 teaches
  the commit's market-override re-hash to read it). A `Trade` with an empty `tradeID` at `EXECUTION` level →
  `ParseError("trade without tradeID")`.
  Row index = the 1-based position of the row element in document order across all sections.

- [ ] **Step 1: Write the fixture** `tests/unit/fixtures/ib_flex_sample.xml` (synthetic; ids invented):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<FlexQueryResponse queryName="homehub-daily" type="AF">
<FlexStatements count="1">
<FlexStatement accountId="U0000000" fromDate="2026-06-01" toDate="2026-06-30" period="Last30CalendarDays" whenGenerated="2026-07-01;03:00:00">
<Trades>
<Trade accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="STK" subCategory="ETF" symbol="ACWD" description="SS SPD MS AL CO WO UC ET-USD" conid="100001" listingExchange="LSEETF" tradeID="900001" reportDate="2026-06-02" dateTime="2026-06-02;09:30:01" tradeDate="2026-06-02" settleDateTarget="2026-06-04" transactionType="ExchTrade" exchange="LSEETF" quantity="1" tradePrice="325.05" tradeMoney="325.05" proceeds="-325.05" taxes="0" ibCommission="-1.78" ibCommissionCurrency="USD" netCash="-326.83" buySell="BUY" openCloseIndicator="O" levelOfDetail="EXECUTION" transactionID="800001" ibOrderID="700001"/>
<Trade accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="STK" subCategory="ETF" symbol="AVGS" description="AVANTIS GBSMCAPVAL UCITS ETF" conid="100002" listingExchange="LSEETF" tradeID="900002" reportDate="2026-06-02" dateTime="2026-06-02;09:31:00" tradeDate="2026-06-02" settleDateTarget="2026-06-04" transactionType="ExchTrade" exchange="LSEETF" quantity="30" tradePrice="27.915" tradeMoney="837.45" proceeds="-837.45" taxes="0" ibCommission="-1.78" ibCommissionCurrency="USD" netCash="-839.23" buySell="BUY" openCloseIndicator="O" levelOfDetail="EXECUTION" transactionID="800002" ibOrderID="700002"/>
<Trade accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="STK" subCategory="ETF" symbol="ACWD" description="SS SPD MS AL CO WO UC ET-USD" conid="100001" listingExchange="LSEETF" tradeID="900003" reportDate="2026-06-02" dateTime="2026-06-02;09:32:00" tradeDate="2026-06-02" settleDateTarget="2026-06-04" transactionType="ExchTrade" exchange="LSEETF" quantity="6" tradePrice="325.2" tradeMoney="1951.2" proceeds="-1951.2" taxes="0" ibCommission="-1.78" ibCommissionCurrency="USD" netCash="-1952.98" buySell="BUY" openCloseIndicator="O" levelOfDetail="EXECUTION" transactionID="800003" ibOrderID="700003"/>
<Trade accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="STK" subCategory="ETF" symbol="AVGS" description="AVANTIS GBSMCAPVAL UCITS ETF" conid="100002" listingExchange="LSEETF" tradeID="900004" reportDate="2026-06-05" dateTime="2026-06-05;10:00:00" tradeDate="2026-06-05" settleDateTarget="2026-06-09" transactionType="ExchTrade" exchange="LSEETF" quantity="-10" tradePrice="28.1" tradeMoney="-281" proceeds="281" taxes="0.5" ibCommission="-1.78" ibCommissionCurrency="USD" netCash="278.72" buySell="SELL" openCloseIndicator="C" levelOfDetail="EXECUTION" transactionID="800004" ibOrderID="700004"/>
<Trade accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="STK" subCategory="ETF" symbol="AVGS" description="AVANTIS GBSMCAPVAL UCITS ETF" conid="100002" listingExchange="LSEETF" tradeID="" reportDate="2026-06-05" dateTime="2026-06-05;10:00:00" tradeDate="2026-06-05" settleDateTarget="2026-06-09" transactionType="ExchTrade" exchange="LSEETF" quantity="-10" tradePrice="28.1" tradeMoney="-281" proceeds="281" taxes="0.5" ibCommission="-1.78" ibCommissionCurrency="USD" netCash="278.72" buySell="SELL" openCloseIndicator="C" levelOfDetail="ORDER" transactionID="" ibOrderID="700004"/>
<Trade accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="OPT" subCategory="C" symbol="XYZ 260619C00100000" description="XYZ 19JUN26 100 C" conid="100003" listingExchange="CBOE" tradeID="900005" reportDate="2026-06-03" dateTime="2026-06-03;11:00:00" tradeDate="2026-06-03" settleDateTarget="2026-06-04" transactionType="ExchTrade" exchange="CBOE" quantity="1" tradePrice="1.5" tradeMoney="150" proceeds="-150" taxes="0" ibCommission="-0.65" ibCommissionCurrency="USD" netCash="-150.65" buySell="BUY" openCloseIndicator="O" levelOfDetail="EXECUTION" transactionID="800005" ibOrderID="700005"/>
</Trades>
<CashTransactions>
<CashTransaction accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="" symbol="" description="CASH RECEIPTS / ELECTRONIC FUND TRANSFERS" dateTime="2026-06-01" settleDate="2026-06-01" reportDate="2026-06-01" amount="3000" type="Deposits/Withdrawals" transactionID="800010" levelOfDetail="DETAIL"/>
<CashTransaction accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="" symbol="" description="DISBURSEMENT INITIATED BY CLIENT" dateTime="2026-06-20;12:00:00" settleDate="2026-06-20" reportDate="2026-06-20" amount="-500" type="Deposits/Withdrawals" transactionID="800011" levelOfDetail="DETAIL"/>
<CashTransaction accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="STK" symbol="AVGS" description="AVGS(IE0000000001) CASH DIVIDEND USD 0.20 PER SHARE (Ordinary Dividend)" dateTime="2026-06-25" settleDate="2026-06-25" reportDate="2026-06-25" amount="6" type="Dividends" transactionID="800012" levelOfDetail="DETAIL"/>
<CashTransaction accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="STK" symbol="AVGS" description="AVGS(IE0000000001) CASH DIVIDEND USD 0.20 PER SHARE - IE TAX" dateTime="2026-06-25" settleDate="2026-06-25" reportDate="2026-06-25" amount="-0.9" type="Withholding Tax" transactionID="800013" levelOfDetail="DETAIL"/>
<CashTransaction accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="" symbol="" description="USD CREDIT INT FOR MAY-2026" dateTime="2026-06-03" settleDate="2026-06-03" reportDate="2026-06-03" amount="0.12" type="Broker Interest Received" transactionID="800014" levelOfDetail="DETAIL"/>
<CashTransaction accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="" symbol="" description="SNAPSHOT MARKET DATA FEE" dateTime="2026-06-10" settleDate="2026-06-10" reportDate="2026-06-10" amount="-1.5" type="Other Fees" transactionID="800015" levelOfDetail="DETAIL"/>
<CashTransaction accountId="U0000000" currency="USD" fxRateToBase="1" assetCategory="" symbol="" description="SOMETHING NEW" dateTime="2026-06-11" settleDate="2026-06-11" reportDate="2026-06-11" amount="1" type="Commission Adjustments" transactionID="800016" levelOfDetail="DETAIL"/>
</CashTransactions>
<Transfers>
<Transfer accountId="U0000000" currency="USD" assetCategory="STK" symbol="ACWD" description="SS SPD MS AL CO WO UC ET-USD" conid="100001" reportDate="2026-06-15" date="2026-06-15" type="ACATS" direction="IN" quantity="5" transferPrice="0" positionAmount="1600" transactionID="800020" levelOfDetail="DETAIL"/>
</Transfers>
<CorporateActions>
<CorporateAction accountId="U0000000" currency="USD" assetCategory="STK" symbol="ACWD" description="ACWD SPLIT 2 FOR 1" conid="100001" reportDate="2026-06-16" dateTime="2026-06-16;20:25:00" amount="0" proceeds="0" value="0" quantity="7" type="FS" transactionID="800021" levelOfDetail="DETAIL"/>
</CorporateActions>
<OpenPositions>
<OpenPosition accountId="U0000000" currency="USD" assetCategory="STK" symbol="ACWD" description="SS SPD MS AL CO WO UC ET-USD" conid="100001" reportDate="2026-06-30" position="7" markPrice="330" positionValue="2310" costBasisPrice="325.18" costBasisMoney="2276.26" levelOfDetail="SUMMARY" side="Long"/>
</OpenPositions>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>
```

- [ ] **Step 2: Write the failing tests** in `tests/unit/test_broker_ib_flex_service.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import broker_ib_flex_service, broker_ib_service

FIXTURE = Path(__file__).parent / "fixtures/ib_flex_sample.xml"
CSV_FIXTURE = Path(__file__).parent / "fixtures/ib_sample.csv"


def _transactions(parsed):
    return [r for r in parsed.rows if r.payload["_kind"] == "transaction"]


def test_parse_trades_mirror_the_csv_parser_payload():
    parsed = broker_ib_flex_service.parse(FIXTURE.read_bytes())
    first = _transactions(parsed)[0].payload
    assert first == {
        "_kind": "transaction", "broker": "IB", "symbol": "ACWD", "market": "LSE",
        "name": "SS SPD MS AL CO WO UC ET-USD", "type": "BUY", "position_side": "LONG",
        "quantity": Decimal("1"), "price": Decimal("325.05"), "currency": "USD",
        "trade_date": datetime(2026, 6, 2, tzinfo=timezone.utc), "fee": Decimal("1.78"), "tax": Decimal("0"),
        "fingerprint_note": "SS SPD MS AL CO WO UC ET-USD ib:900001",
    }


def test_parse_trade_fingerprint_is_the_broker_fingerprint_with_the_trade_id_in_the_note():
    from app.services.import_service import _broker_transaction_fingerprint
    first = _transactions(broker_ib_flex_service.parse(FIXTURE.read_bytes()))[0]
    assert first.fingerprint == _broker_transaction_fingerprint(
        broker="IB", symbol="ACWD", market="LSE", type_="BUY", quantity=Decimal("1"), price=Decimal("325.05"),
        trade_date=datetime(2026, 6, 2, tzinfo=timezone.utc), fee=Decimal("1.78"), tax=Decimal("0"), currency="USD",
        note="SS SPD MS AL CO WO UC ET-USD ib:900001")


def test_parse_two_identical_same_day_fills_are_two_rows():
    raw = FIXTURE.read_bytes().replace(b'tradeID="900001"', b'tradeID="900009"', 1)  # same trade, another execution id
    raw = FIXTURE.read_bytes().replace(b"</Trades>", raw[raw.index(b"<Trade "):raw.index(b"/>") + 2] + b"</Trades>", 1)
    rows = _transactions(broker_ib_flex_service.parse(raw))
    assert len(rows) == 5 and len({r.fingerprint for r in rows}) == 5


def test_parse_trades_take_the_market_from_the_listing_exchange():
    parsed = broker_ib_flex_service.parse(FIXTURE.read_bytes())
    assert {r.payload["market"] for r in _transactions(parsed)} == {"LSE"}
    us = broker_ib_flex_service.parse(FIXTURE.read_bytes().replace(b'listingExchange="LSEETF"', b'listingExchange="NASDAQ"'))
    assert {r.payload["market"] for r in _transactions(us)} == {"US"}


def test_parse_sell_uses_absolute_quantity_and_taxes():
    sell = [r.payload for r in _transactions(broker_ib_flex_service.parse(FIXTURE.read_bytes())) if r.payload["type"] == "SELL"]
    assert len(sell) == 1
    assert (sell[0]["quantity"], sell[0]["price"], sell[0]["fee"], sell[0]["tax"]) == (Decimal("10"), Decimal("28.1"), Decimal("1.78"), Decimal("0.5"))


def test_parse_trades_imports_execution_rows_only():
    parsed = broker_ib_flex_service.parse(FIXTURE.read_bytes())
    assert len(_transactions(parsed)) == 4  # three buys, one sell; the ORDER-level twin and the OPT row are not rows


def test_parse_rejects_non_stock_trades_with_a_row_error_naming_the_category():
    parsed = broker_ib_flex_service.parse(FIXTURE.read_bytes())
    assert any(e.message == "unsupported assetCategory OPT" for e in parsed.errors)


def test_parse_rejects_a_commission_in_another_currency():
    raw = FIXTURE.read_bytes().replace(b'ibCommissionCurrency="USD" netCash="-326.83"', b'ibCommissionCurrency="GBP" netCash="-326.83"', 1)
    parsed = broker_ib_flex_service.parse(raw)
    assert any(e.message == "ibCommissionCurrency GBP differs from currency USD" for e in parsed.errors)
    assert len(_transactions(parsed)) == 3


def test_parse_gbp_trade_maps_to_lse_like_the_csv_parser():
    raw = FIXTURE.read_bytes().replace(b'currency="USD" fxRateToBase="1" assetCategory="STK" subCategory="ETF" symbol="ACWD"', b'currency="GBP" fxRateToBase="1.27" assetCategory="STK" subCategory="ETF" symbol="ACWD"', 1)
    raw = raw.replace(b'ibCommissionCurrency="USD" netCash="-326.83"', b'ibCommissionCurrency="GBP" netCash="-326.83"', 1)
    first = _transactions(broker_ib_flex_service.parse(raw))[0].payload
    assert (first["market"], first["currency"]) == ("LSE", "GBP")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd services/stock-portfolio-service && pytest tests/unit/test_broker_ib_flex_service.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.broker_ib_flex_service`.

- [ ] **Step 4: Implement `parse` for trades** in `app/services/broker_ib_flex_service.py`:

Signatures:
```python
def parse(raw_bytes: bytes) -> ParseResult: ...
def _root(raw_bytes: bytes) -> ET.Element: ...          # Task 2 adds the guards; for now ET.fromstring
def _trade(row_index: int, el: ET.Element) -> ParsedRow: ...   # raises ValueError with the messages pinned above
def _decimal(value: str | None) -> Decimal: ...          # "" and None → Decimal("0"); no thousands separators in Flex
def _utc_midnight(value: str) -> datetime: ...           # "%Y-%m-%d" → tz=UTC, as broker_ib_service._trade_date
```
Rules the tests pin: iterate `root.iter("FlexStatement")`, then each section element's children in document
order, counting `row_index` from 1 across all sections; a `Trade` with `levelOfDetail != "EXECUTION"` is skipped
silently (not a row, not an error); `assetCategory != "STK"` → `ParseError("unsupported assetCategory <cat>")`;
`ibCommissionCurrency` present and ≠ `currency` → `ParseError("ibCommissionCurrency <x> differs from currency <y>")`;
`type_ = "BUY" if buySell == "BUY" else "SELL"`; `quantity = abs(_decimal(quantity))`; `price =
_decimal(tradePrice)`; `fee = abs(_decimal(ibCommission))`; `tax = abs(_decimal(taxes))`; `currency =
currency.upper()`; `market = "LSE" if listingExchange in LSE_EXCHANGES else broker_ib_service._market_for(symbol, currency)`;
`name = description.strip() or None`; `fingerprint_note = f"{name or ''} ib:{tradeID}".strip()`; fingerprint via `_broker_transaction_fingerprint(broker="IB", symbol, market, type_, quantity, price,
trade_date, fee, tax, currency, note=fingerprint_note)` — the same call as `broker_ib_service.py:74-86` with the
id-bearing note. Sections other than
`Trades` are ignored in this task. `ValueError`/`InvalidOperation` inside a row → `ParseError(row_index, str(exc))`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/unit/test_broker_ib_flex_service.py -v` — Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add app/services/broker_ib_flex_service.py tests/unit/fixtures/ib_flex_sample.xml tests/unit/test_broker_ib_flex_service.py
git commit -m "feat(portfolio): parse IB Flex trades onto the broker import rows"
```

---

### Task 2: Flex adapter — cash transactions, unsupported sections, XML guards

**Files:**
- Modify: `services/stock-portfolio-service/app/services/broker_ib_flex_service.py`
- Test: `services/stock-portfolio-service/tests/unit/test_broker_ib_flex_service.py`

**Interfaces:**
- Consumes: `cash_flow_service.cash_flow_fingerprint(broker, date_, type_, amount, currency, note)`
  (`cash_flow_service.py:34`), `models.BrokerCashFlowType` values `deposit|withdrawal|interest|dividend_cash|fee`.
- Produces: cash-flow rows with the CSV parser's payload keys (`broker_ib_service.py:120-132`): `_kind="cash_flow"`,
  `broker="IB"`, `date` (a `date` from the first 10 characters of `dateTime`), `cash_flow_type`, `amount` (SIGNED, as
  reported), `currency`, `note` (= `description` or None), plus `symbol` (may be empty) and
  `fingerprint_note = f"{description} ib:{transactionID}"` (the note the fingerprint was computed with). Mapping
  table `CASH_TYPES` (the sign decides only the `Deposits/Withdrawals` split; a refund under a fee type stays a
  positive `fee`, a reversed dividend a negative `dividend_cash`):

| Flex `type` | sign | `cash_flow_type` |
|---|---|---|
| `Deposits/Withdrawals` | amount > 0 | `deposit` |
| `Deposits/Withdrawals` | amount < 0 | `withdrawal` |
| `Dividends`, `Payment In Lieu Of Dividends` | any | `dividend_cash` |
| `Withholding Tax`, `Other Fees`, `Broker Interest Paid`, `Commission Adjustments` | any | `fee` |
| `Broker Interest Received` | any | `interest` |
| anything else | | `ParseError("unsupported cash transaction type <type>")` |

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_broker_ib_flex_service.py`):

```python
from datetime import date


def _cash(parsed):
    return [r.payload for r in parsed.rows if r.payload["_kind"] == "cash_flow"]


def test_parse_cash_transactions_map_types_and_signs():
    flows = _cash(broker_ib_flex_service.parse(FIXTURE.read_bytes()))
    assert [(f["date"], f["cash_flow_type"], f["amount"]) for f in flows] == [
        (date(2026, 6, 1), "deposit", Decimal("3000")),
        (date(2026, 6, 20), "withdrawal", Decimal("-500")),
        (date(2026, 6, 25), "dividend_cash", Decimal("6")),
        (date(2026, 6, 25), "fee", Decimal("-0.9")),
        (date(2026, 6, 3), "interest", Decimal("0.12")),
        (date(2026, 6, 10), "fee", Decimal("-1.5")),
        (date(2026, 6, 11), "fee", Decimal("1")),  # a positive adjustment under a fee type keeps its sign
    ]
    assert flows[0]["note"] == "CASH RECEIPTS / ELECTRONIC FUND TRANSFERS" and flows[0]["currency"] == "USD"
    assert flows[2]["symbol"] == "AVGS" and flows[0]["symbol"] == ""
    assert sum(f["amount"] for f in flows) == Decimal("2505.72")  # the balance a SUM(amount) reader would see


def test_parse_cash_fingerprint_is_the_existing_cash_flow_fingerprint():
    from app.services.cash_flow_service import cash_flow_fingerprint
    row = [r for r in broker_ib_flex_service.parse(FIXTURE.read_bytes()).rows if r.payload["_kind"] == "cash_flow"][0]
    assert row.fingerprint == cash_flow_fingerprint(broker="IB", date_=date(2026, 6, 1), type_="deposit", amount=Decimal("3000"), currency="USD", note="CASH RECEIPTS / ELECTRONIC FUND TRANSFERS ib:800010")


def test_parse_reports_an_unknown_cash_type_as_a_row_error():
    raw = FIXTURE.read_bytes().replace(b'type="Commission Adjustments"', b'type="Bond Coupon"', 1)
    parsed = broker_ib_flex_service.parse(raw)
    assert any(e.message == "unsupported cash transaction type Bond Coupon" for e in parsed.errors)
    assert len(_cash(parsed)) == 6


def test_parse_reports_transfers_and_corporate_actions_as_row_errors_not_rows():
    parsed = broker_ib_flex_service.parse(FIXTURE.read_bytes())
    messages = sorted(e.message for e in parsed.errors)
    assert "unsupported section Transfers" in messages and "unsupported section CorporateActions" in messages
    assert len(parsed.rows) == 4 + 7  # positions and conversion rates are silently ignored


@pytest.mark.parametrize("raw, message", [
    (b"<!DOCTYPE x [<!ENTITY a 'b'>]><FlexQueryResponse/>", "flex: DTD refused"),
    (b"<!--" + b" " * 6000 + b"--><!DOCTYPE x><FlexQueryResponse/>", "flex: DTD refused"),  # after a long leading comment
    (b"<FlexQueryResponse><!ENTITY a 'b'></FlexQueryResponse>", "flex: DTD refused"),
    ("<FlexQueryResponse/>".encode("utf-16"), "flex: not UTF-8"),
    (b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1020</ErrorCode><ErrorMessage>x</ErrorMessage></FlexStatementResponse>", "flex: 1020"),
    (b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>DROP TABLE</ErrorCode></FlexStatementResponse>", "flex: error"),  # a code is 4 digits or it is not a code
    (b"<Other/>", "flex: not a FlexQueryResponse"),
    (b"<FlexQueryResponse", "flex: malformed XML"),
])
def test_parse_refuses_bad_documents_with_a_value_error(raw, message):
    with pytest.raises(ValueError, match=message):
        broker_ib_flex_service.parse(raw)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_broker_ib_flex_service.py -v` — Expected: the new tests FAIL (cash rows absent; no
ValueError).

- [ ] **Step 3: Implement** in `broker_ib_flex_service.py`:
  - `_root`: `raw_bytes.decode("utf-8")` (UTF-8 BOM allowed via `utf-8-sig`; `UnicodeDecodeError` →
    `ValueError("flex: not UTF-8")`); then over the WHOLE text `if "<!DOCTYPE" in text or "<!ENTITY" in text: raise
    ValueError("flex: DTD refused")` (checked before any parsing; the upload guard caps the text at 5 MiB so the scan is
    bounded); `ET.ParseError` → `ValueError("flex: malformed XML")`; root tag `FlexStatementResponse` →
    `ValueError(f"flex: {code}")` where `code = ErrorCode` only if it matches `^[0-9]{4}$`, else the literal `error`
    (never `ErrorMessage`); any other tag than `FlexQueryResponse` → `ValueError("flex: not a FlexQueryResponse")`.
  - `CASH_TYPES: dict[str, str | None]` per the table (`None` for `Deposits/Withdrawals`, resolved by sign);
    `_cash(row_index, el) -> ParsedRow`; `amount = _decimal(amount)` (signed); `date_ = _utc_midnight(dateTime[:10]).date()`;
    `symbol = (symbol or "").strip().upper()`; `fingerprint_note = f"{description} ib:{transactionID}"`; an empty
    `transactionID` → `ParseError("cash transaction without transactionID")`.
  - Section dispatch: `Trades` → `_trade`, `CashTransactions` → `_cash`, `Transfers` / `CorporateActions` → one
    `ParseError(row_index, f"unsupported section {tag}")` per row, `OpenPositions` / `ConversionRates` /
    `AccountInformation` and any other section → skipped (positions derive from transactions).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_broker_ib_flex_service.py -v` — Expected: 21 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/broker_ib_flex_service.py tests/unit/test_broker_ib_flex_service.py
git commit -m "feat(portfolio): map IB Flex cash transactions and refuse bad Flex documents"
```

---

### Task 3: the `legacy_until` pass and `POST /api/portfolio/imports/ib-flex`

**Files:**
- Modify: `services/stock-portfolio-service/app/services/broker_ib_flex_service.py`
- Modify: `services/stock-portfolio-service/app/routers/imports.py` (after `import_csv`, `:241-259`)
- Modify: `services/stock-portfolio-service/app/services/import_service.py:597-609` (the market-override re-hash reads `fingerprint_note`)
- Test: `services/stock-portfolio-service/tests/integration/test_ib_flex_import.py`

**Interfaces:**
- Consumes: `parse` (Tasks 1–2); `import_service.commit_transactions(db, parsed, *, dry_run)` (`:710`);
  `_read_upload`, `_touched_symbols_and_min_trade_date`, `_maybe_schedule_chain`, `_serialize_result`
  (`imports.py:36, 116, 150, 62`); `models.Transaction`, `models.BrokerCashFlow`.
- Produces: `@dataclass class LegacyResult: pending: ParseResult; pairs: list[LegacyPair]` with
  `LegacyPair(row_index: int, kind: str, existing_id: int)`, and
  `legacy_duplicates(db: Session, parsed: ParseResult, *, until: date, dry_run: bool) -> LegacyResult` — `pending` is
  a NEW `ParseResult` (the input is never mutated) holding the rows to commit; `pairs` lists every parsed row that
  was paired with an existing row (ids only). The algorithm, in order:
  1. **Fingerprint first.** `known` = the stored `import_fingerprint`s of IB rows (both tables). A parsed row whose
     fingerprint is in `known` is already identified: it goes straight to `pending` (the commit counts it as
     `skipped_duplicates`) and takes no part in pairing. An existing row whose fingerprint equals ANY parsed row's
     fingerprint is likewise identified and is removed from the legacy set.
  2. **Scope.** Legacy candidates are the remaining parsed rows dated `<= until` and the remaining existing IB rows
     dated `<= until` (transactions by `trade_date::date`, cash flows by `date`). Everything later is untouched; the
     daily run never passes `until`, so it never pairs.
  3. **Keys.** Transaction `(symbol, type, f"{quantity:.4f}", f"{price:.4f}", trade_date::date, currency,
     f"{fee:.4f}", f"{tax:.4f}")`; cash flow `(date, type, f"{amount:.4f}" signed, currency)` — `broker_cash_flows`
     has no symbol column, so the cash key has no symbol slot; the key carries neither `market` nor `name`/`note`, so
     a row stored as `US` by the CSV import and corrected to `LSE` by hand, or imported before fingerprints existed,
     is still the same event, and two cash events of one type, day, amount and currency are indistinguishable by
     design (their totals are equal; the pair list shows them).
  4. **One-to-one pairing.** Per key, `n_old` existing candidates and `n_new` parsed candidates: `n_old == 0` → keep
     all parsed rows; `n_old == n_new` → pair them in `row_index` / `id` order and drop the parsed rows; otherwise
     (one old + two new, two old + one new) → drop every parsed row of that key with ONE
     `ParseError(row_index, "ambiguous legacy match")` each (the owner resolves by hand; nothing is guessed).
  5. **Adoption.** Unless `dry_run`, each paired existing row's `import_fingerprint` is set to the parsed row's
     fingerprint (`db.commit()` once at the end): the old row now carries the Flex identity, so every later import —
     daily, backfill, with or without `until` — dedupes it by fingerprint and can never re-create the event. A dry
     run reports the pairs and adopts nothing. (The old CSV fingerprint is replaced; a CSV re-upload would then
     duplicate — hence the mutual-exclusion rule in Global Constraints.)
  Route response = `_serialize_result(original_parsed, result, csv_format="ib-flex")` with `parsed` overridden to
  `len(original_parsed.rows)`, plus `"legacy_duplicates": len(pairs)`, `"legacy_pairs": [{"row_index", "kind",
  "existing_id"}]`, `"error_counts": {"parse": len(original.errors), "ambiguous": len(pending.errors) - len(original.errors),
  "commit": len(result.errors) - len(pending.errors)}` and `"candidates": len(pending.rows) - result.skipped_duplicates -
  error_counts["commit"]` — the rows a real run would create: committable rows minus fingerprint duplicates minus
  commit rejections (missing FX). Parse errors and ambiguities are not rows and are never subtracted.
- Modify `import_service._commit_broker_rows` re-hash (`:597-609`): `note=payload.get("fingerprint_note", payload.get("name"))`
  — CSV rows have no `fingerprint_note`, so nothing changes for them.

- [ ] **Step 1: Write the failing tests** `tests/integration/test_ib_flex_import.py`:

```python
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from app.models import portfolio as models
from app.models.fx_rate import FXRate

FIXTURES = Path(__file__).parents[1] / "unit/fixtures"


def _seed_fx(db_session, *dates):
    for d in dates:
        db_session.merge(FXRate(currency="USD", date=d, rate_to_twd=Decimal("31.42000000"), source="test"))
    db_session.commit()


JUNE = [date(2026, 6, d) for d in range(1, 31)]


def _post(client, raw: bytes, **params):
    return client.post("/api/portfolio/imports/ib-flex", params=params, files={"file": ("flex.xml", BytesIO(raw), "application/xml")})


FLEX = (FIXTURES / "ib_flex_sample.xml").read_bytes()
TWIN = FLEX.replace(b"</Trades>", FLEX[FLEX.index(b"<Trade "):FLEX.index(b"/>") + 2].replace(b'tradeID="900001"', b'tradeID="900009"') + b"</Trades>", 1)


def _legacy_row(**over) -> models.Transaction:
    base = dict(symbol="ACWD", market="LSE", name="x", type=models.TransactionType.BUY, quantity=Decimal("1"), price=Decimal("325.05"), currency="USD",
                fx_rate_to_twd=Decimal("31.42"), trade_date=datetime(2026, 6, 2, tzinfo=timezone.utc), fee=Decimal("1.78"), tax=Decimal("0"), broker="IB", import_fingerprint=None)
    return models.Transaction(**{**base, **over})


def _acwd_one(db_session):
    return db_session.query(models.Transaction).filter_by(broker="IB", symbol="ACWD", quantity=Decimal("1"))


def test_ib_flex_import_creates_rows_once_and_dedupes_by_fingerprint_on_the_second_upload(client, db_session):
    _seed_fx(db_session, *JUNE)
    first = _post(client, FLEX).json()
    assert (first["parsed"], first["created"], first["skipped_duplicates"], first["legacy_duplicates"], first["candidates"], first["csv_format"]) == (11, 11, 0, 0, 11, "ib-flex")
    assert first["error_counts"] == {"parse": 3, "ambiguous": 0, "commit": 0}
    assert sorted(e["reason"] for e in first["errors"]) == ["unsupported assetCategory OPT", "unsupported section CorporateActions", "unsupported section Transfers"]
    assert db_session.query(models.Transaction).filter_by(broker="IB").count() == 4
    assert db_session.query(models.BrokerCashFlow).filter_by(broker="IB").count() == 7
    second = _post(client, FLEX).json()  # the daily case: no legacy_until, fingerprints alone
    assert (second["parsed"], second["created"], second["skipped_duplicates"], second["legacy_duplicates"], second["candidates"], second["recalc_scheduled"]) == (11, 0, 11, 0, 0, False)


def test_ib_flex_import_with_legacy_until_pairs_the_manual_era_rows_and_they_adopt_the_flex_fingerprint(client, db_session):
    _seed_fx(db_session, *JUNE)
    csv = (FIXTURES / "ib_sample.csv").read_bytes()
    assert client.post("/api/portfolio/imports/csv", files={"file": ("ib.csv", BytesIO(csv), "text/csv")}).json()["created"] == 4
    before = {t.id: t.import_fingerprint for t in db_session.query(models.Transaction).filter_by(broker="IB")}
    # CSV rows: market US, CSV fingerprint, deposit note = the CSV 說明; the Flex rows differ on all three → business key only
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert (body["parsed"], body["skipped_duplicates"], body["legacy_duplicates"], body["created"], body["candidates"]) == (11, 0, 4, 7, 7)
    assert sorted(p["kind"] for p in body["legacy_pairs"]) == ["cash_flow", "transaction", "transaction", "transaction"]
    assert db_session.query(models.BrokerCashFlow).filter_by(broker="IB", type="deposit").count() == 1
    assert db_session.query(models.Transaction).filter_by(broker="IB", symbol="ACWD").count() == 2  # 1 + 6 from the CSV, nothing new
    after = {t.id: (t.import_fingerprint, t.market) for t in db_session.query(models.Transaction).filter_by(broker="IB")}
    assert set(before) <= set(after) and len(after) == len(before) + 1          # the three CSV rows kept their ids; one new row (the 06-05 sell)
    assert all(after[i][0] != before[i] and after[i][1] == "US" for i in before)  # adoption changes identity only, never market
    assert [after[i][1] for i in after if i not in before] == ["LSE"]              # the new Flex row takes its market from listingExchange
    again = _post(client, FLEX, legacy_until="2026-06-30").json()  # adopted rows are plain fingerprint duplicates now
    assert (again["legacy_duplicates"], again["skipped_duplicates"], again["created"], again["candidates"]) == (0, 11, 0, 0)
    daily = _post(client, FLEX).json()                                # and so is a later run without legacy_until
    assert (daily["legacy_duplicates"], daily["skipped_duplicates"], daily["created"]) == (0, 11, 0)


def test_ib_flex_import_dry_run_reports_pairs_but_adopts_nothing(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row()); db_session.commit()
    body = _post(client, FLEX, legacy_until="2026-06-30", dry_run="true").json()
    assert body["legacy_duplicates"] == 1 and body["legacy_pairs"][0]["kind"] == "transaction" and body["candidates"] == 10 and body["created"] == 0
    assert _acwd_one(db_session).one().import_fingerprint is None and db_session.query(models.Transaction).count() == 1


def test_ib_flex_import_without_legacy_until_never_pairs(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row()); db_session.commit()
    body = _post(client, FLEX).json()
    assert body["legacy_duplicates"] == 0 and _acwd_one(db_session).count() == 2


def test_ib_flex_import_legacy_until_scopes_by_date(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row())                                                                   # 06-02: in scope, paired
    db_session.add(_legacy_row(trade_date=datetime(2026, 6, 5, tzinfo=timezone.utc), type=models.TransactionType.SELL, quantity=Decimal("10"), price=Decimal("28.1"), tax=Decimal("0.5"), symbol="AVGS"))  # the Flex sell's twin, but past `until`
    db_session.commit()
    body = _post(client, FLEX, legacy_until="2026-06-03").json()
    assert body["legacy_duplicates"] == 1 and _acwd_one(db_session).count() == 1
    assert db_session.query(models.Transaction).filter_by(broker="IB", symbol="AVGS", type=models.TransactionType.SELL).count() == 2


@pytest.mark.parametrize("field, value", [("currency", "GBP"), ("fee", Decimal("1.79")), ("tax", Decimal("0.01")), ("price", Decimal("325.06"))])
def test_ib_flex_import_legacy_key_separates_currency_fee_tax_and_price(client, db_session, field, value):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row(**{field: value})); db_session.commit()                             # same day, one field off: another event
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 0 and _acwd_one(db_session).count() == 2


def test_ib_flex_import_legacy_key_ignores_market_and_name(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row(market="US", name="whatever the CSV said")); db_session.commit()   # the hand-correction case
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 1 and _acwd_one(db_session).count() == 1 and _acwd_one(db_session).one().market == "US"


def test_ib_flex_import_reports_an_ambiguous_legacy_match_instead_of_guessing(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row()); db_session.add(_legacy_row(name="y")); db_session.commit()  # two old rows, one Flex row
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert body["legacy_duplicates"] == 0 and body["error_counts"] == {"parse": 3, "ambiguous": 1, "commit": 0} and body["candidates"] == 10
    assert sum(e["reason"] == "ambiguous legacy match" for e in body["errors"]) == 1
    assert _acwd_one(db_session).count() == 2 and all(t.import_fingerprint is None for t in _acwd_one(db_session))  # untouched
    assert body["created"] == 10


def test_ib_flex_import_one_old_row_and_two_new_fills_is_ambiguous_not_a_silent_merge(client, db_session):
    _seed_fx(db_session, *JUNE)
    db_session.add(_legacy_row()); db_session.commit()
    body = _post(client, TWIN, legacy_until="2026-06-30").json()
    assert sum(e["reason"] == "ambiguous legacy match" for e in body["errors"]) == 2 and body["legacy_duplicates"] == 0
    assert _acwd_one(db_session).count() == 1


def test_ib_flex_import_keeps_two_same_day_fills_apart_by_trade_id(client, db_session):
    _seed_fx(db_session, *JUNE)
    body = _post(client, TWIN).json()
    assert body["created"] == 12 and _acwd_one(db_session).count() == 2


def test_ib_flex_import_pairs_the_identified_rows_first(client, db_session):
    """One old CSV-era row and one Flex row already imported for the same key: the Flex row is identified by its
    fingerprint, the old row pairs with nothing new → nothing ambiguous, nothing adopted, nothing created."""
    _seed_fx(db_session, *JUNE)
    assert _post(client, FLEX).json()["created"] == 11                       # Flex rows exist with Flex fingerprints
    db_session.add(_legacy_row()); db_session.commit()                        # plus an unidentified old twin
    body = _post(client, FLEX, legacy_until="2026-06-30").json()
    assert (body["legacy_duplicates"], body["skipped_duplicates"], body["created"], body["error_counts"]["ambiguous"]) == (0, 11, 0, 0)
    assert _acwd_one(db_session).count() == 2


def test_ib_flex_import_dry_run_writes_nothing_and_reports_candidates(client, db_session):
    _seed_fx(db_session, *JUNE)
    body = _post(client, FLEX, dry_run="true").json()
    assert body["dry_run"] is True and body["created"] == 0 and body["recalc_scheduled"] is False and body["candidates"] == 11  # parse errors are not rows
    assert db_session.query(models.Transaction).count() == 0 and db_session.query(models.BrokerCashFlow).count() == 0


def test_ib_flex_import_retries_a_row_once_its_fx_rate_exists(client, db_session):
    _seed_fx(db_session, *[d for d in JUNE if d != date(2026, 6, 5)])  # the sell's date has no rate yet
    first = _post(client, FLEX).json()
    assert any(e["reason"] == "missing FX rate for 2026-06-05 USD" for e in first["errors"]) and first["created"] == 10
    assert first["error_counts"] == {"parse": 3, "ambiguous": 0, "commit": 1} and first["candidates"] == 10
    _seed_fx(db_session, date(2026, 6, 5))
    second = _post(client, FLEX).json()
    assert (second["created"], second["skipped_duplicates"], second["candidates"]) == (1, 10, 1)


def test_ib_flex_import_refuses_a_flex_error_document_and_oversize_uploads(client):
    assert _post(client, b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1020</ErrorCode></FlexStatementResponse>").status_code == 400
    assert _post(client, b"<FlexQueryResponse>" + b" " * (5 * 1024 * 1024) + b"</FlexQueryResponse>").status_code == 413
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/integration/test_ib_flex_import.py -v` — Expected: FAIL with 404 (no route).

- [ ] **Step 3: Implement `legacy_duplicates`** in `broker_ib_flex_service.py` per the Interfaces block:
  `known` from `Transaction.import_fingerprint` / `BrokerCashFlow.import_fingerprint` (`broker == "IB"`, not null);
  `parsed_fps = {r.fingerprint for r in parsed.rows}`; existing candidates = IB rows dated `<= until` whose
  fingerprint is null or not in `parsed_fps`, loaded with their `id` and key fields
  (`Transaction.id, symbol, type, quantity, price, trade_date, currency, fee, tax` /
  `BrokerCashFlow.id, date, type, amount, currency`) and grouped `dict[key, list[id]]` (ids ascending); parsed
  candidates = rows dated `<= until` whose fingerprint is not in `known`, grouped `dict[key, list[ParsedRow]]`
  (row_index ascending); per key the rule of step 4, pairing by position; adoption by
  `db.query(Transaction).filter(Transaction.id == existing_id).update({"import_fingerprint": row.fingerprint})`
  (same for `BrokerCashFlow`), `db.commit()` once unless `dry_run`. Return
  `LegacyResult(pending=ParseResult(rows=kept, errors=parsed.errors + ambiguities, unresolved_names=parsed.unresolved_names), pairs=pairs)`.

- [ ] **Step 4: Implement the route** in `imports.py`:

```python
@router.post("/ib-flex")
def import_ib_flex(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    dry_run: bool = Query(default=False),
    legacy_until: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
```
Body: `raw = _read_upload(file)`; `original = broker_ib_flex_service.parse(raw)` inside the same `try/except
ValueError → HTTPException(400)` as `import_transactions`; `legacy = (legacy_duplicates(db, original,
until=legacy_until, dry_run=dry_run) if legacy_until else LegacyResult(pending=original, pairs=[]))`;
`result = import_service.commit_transactions(db, legacy.pending, dry_run=dry_run)`; recalc scheduling copied from
`import_transactions` (`:233-237`); `body = _serialize_result(original, result, recalc_scheduled=..., csv_format="ib-flex")`;
then `body["parsed"] = len(original.rows)`, `body["legacy_duplicates"] = len(legacy.pairs)`, `body["legacy_pairs"] =
[asdict(p) for p in legacy.pairs]`, `commit_errors = len(result.errors) - len(legacy.pending.errors)`,
`body["error_counts"] = {"parse": len(original.errors), "ambiguous": len(legacy.pending.errors) - len(original.errors), "commit": commit_errors}`,
`body["candidates"] = len(legacy.pending.rows) - result.skipped_duplicates - commit_errors`; `body["errors"]` is
`result.errors` (which already include the parse errors and the ambiguities, since `commit` copies
`parsed.errors`); return `body`. And the two-line change in `import_service._commit_broker_rows`
(`note=payload.get("fingerprint_note", payload.get("name"))`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/integration/test_ib_flex_import.py tests/unit/test_broker_ib_flex_service.py tests/integration/test_broker_csv_import.py tests/unit/test_broker_ib_service.py -v`
Expected: all pass (the CSV import tests are unchanged; `test_ib_commit_uses_symbol_map_market_override` proves the
re-hash change is a no-op for CSV rows).

- [ ] **Step 6: Commit**

```bash
git add app/services/broker_ib_flex_service.py app/services/import_service.py app/routers/imports.py tests/integration/test_ib_flex_import.py
git commit -m "feat(portfolio): POST /imports/ib-flex with an explicit legacy_until pairing pass"
```

---

### Task 4: Worker config keys, credentials file, `write_private_json`

**Files:**
- Modify: `services/accounting-service/worker/config.py` (`WorkerConfig` `:26-52`, `load` `:89-124`)
- Modify: `services/accounting-service/worker/parser.py` (`Gate._write` `:468-475`)
- Create: `services/accounting-service/worker/flex.py`
- Test: `services/accounting-service/tests/worker/test_flex.py`, `tests/worker/test_config.py`

**Interfaces:**
- Produces on `WorkerConfig` (frozen dataclass; all with defaults so existing constructions keep working):

| field | env | default |
|---|---|---|
| `flex_credentials_file: Path` | `STATEMENT_FLEX_CREDENTIALS_FILE` | `~/.config/homehub-portfolio-flex.env` |
| `flex_base_url: str` | `STATEMENT_FLEX_BASE_URL` | `https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService` (`.rstrip("/")`) |
| `flex_timeout_s: int` | `STATEMENT_FLEX_TIMEOUT` | `60` |
| `flex_poll_attempts: int` | `STATEMENT_FLEX_POLL_ATTEMPTS` | `12` |
| `flex_poll_interval_s: int` | `STATEMENT_FLEX_POLL_INTERVAL` | `10` |
| `portfolio_api_url: str` | `STATEMENT_PORTFOLIO_API_URL` | `http://127.0.0.1:8001` (`.rstrip("/")`) |

- `parser.write_private_json(state_dir: Path, path: Path, data: dict) -> None` (the body of `Gate._write`;
  `Gate._write` becomes a one-line delegate).
- `flex.Credentials` — `@dataclass(frozen=True)` with `token: str = field(repr=False)`, `query_id: str`.
- `flex.load_credentials(path: Path) -> Credentials` — `key=value` lines (UTF-8 with BOM allowed, `#` comments,
  blank lines), keys `IB_FLEX_TOKEN` (must match `^[A-Za-z0-9]{8,128}$`) and `IB_FLEX_QUERY_ID` (must match
  `^[0-9]{1,12}$`); a missing or malformed value raises `ValueError("<KEY> missing or malformed")` — the key
  name only, never the value.

- [ ] **Step 1: Write the failing tests** `tests/worker/test_flex.py` (first part) and one in `test_config.py`:

```python
# tests/worker/test_flex.py
import json
import logging
from pathlib import Path

import httpx
import pytest

from worker import config, flex

TOKEN = "abcdefghijklmnopqrstuv0123"


def _creds(tmp_path: Path, token: str = TOKEN, query_id: str = "1234567") -> Path:
    p = tmp_path / "flex.env"
    p.write_text(f"# ib\nIB_FLEX_TOKEN={token}\nIB_FLEX_QUERY_ID={query_id}\n", encoding="utf-8")
    return p


def test_load_credentials_reads_key_value_lines(tmp_path):
    creds = flex.load_credentials(_creds(tmp_path))
    assert (creds.token, creds.query_id) == (TOKEN, "1234567")


def test_credentials_repr_hides_the_token(tmp_path):
    creds = flex.load_credentials(_creds(tmp_path))
    assert TOKEN not in repr(creds) and TOKEN not in str(creds) and "1234567" in repr(creds)


@pytest.mark.parametrize("token, query_id, key", [
    ("", "1234567", "IB_FLEX_TOKEN"), ("short!", "1234567", "IB_FLEX_TOKEN"), (TOKEN, "homehub-daily", "IB_FLEX_QUERY_ID"), (TOKEN, "", "IB_FLEX_QUERY_ID"),
])
def test_load_credentials_names_only_the_key_on_a_bad_value(tmp_path, token, query_id, key):
    with pytest.raises(ValueError) as exc:
        flex.load_credentials(_creds(tmp_path, token, query_id))
    assert str(exc.value) == f"{key} missing or malformed"
    for secret in (token, query_id):
        assert not secret or secret not in str(exc.value)  # an empty value is a substring of anything: skip it
```

```python
# tests/worker/test_config.py (append)
def test_flex_defaults():
    cfg = config.load({})
    assert cfg.flex_base_url == "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
    assert cfg.portfolio_api_url == "http://127.0.0.1:8001" and cfg.flex_poll_attempts == 12 and cfg.flex_poll_interval_s == 10
    assert cfg.flex_credentials_file == Path("~/.config/homehub-portfolio-flex.env").expanduser() and cfg.flex_timeout_s == 60
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/accounting-service && pytest tests/worker/test_flex.py tests/worker/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'flex'` / `AttributeError: flex_base_url`.

- [ ] **Step 3: Implement** the six config fields (after `ocr_child`, each with its default), the `load` lines
using `_path` / `_str` / `int(_str(...))` like the neighbours; `parser.write_private_json`; `flex.Credentials`
and `flex.load_credentials` (regexes `TOKEN_RE = re.compile(r"^[A-Za-z0-9]{8,128}$")`, `QUERY_RE =
re.compile(r"^[0-9]{1,12}$")`; parse with `line.partition("=")`, strip both sides, decode `utf-8-sig`).

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/worker/test_flex.py tests/worker/test_config.py tests/worker/test_parser.py -v`
Expected: all pass (`test_parser.py` proves the `Gate._write` lift changed nothing).

- [ ] **Step 5: Commit**

```bash
git add worker/config.py worker/parser.py worker/flex.py tests/worker/test_flex.py tests/worker/test_config.py
git commit -m "feat(worker): Flex credentials file and STATEMENT_FLEX_* configuration"
```

---

### Task 5: `flex.fetch` — SendRequest → GetStatement with the token kept out of everything but the `t` parameter

**Files:**
- Modify: `services/accounting-service/worker/flex.py`
- Test: `services/accounting-service/tests/worker/test_flex.py`

**Interfaces:**
- Produces:
  - `class FlexError(RuntimeError)`: `__init__(self, code: str, *, transient: bool)`; `str(exc) == code`; codes
    are `send:<ErrorCode>` / `get:<ErrorCode>` (IB status failures; `transient` per `IB_TRANSIENT_CODES`),
    `http:<status>` (≥ 400; transient when ≥ 500 or in `{408, 429}`), `http:<ExceptionClassName>` (any
    `httpx.HTTPError`, transient), `get:pending` (1019 past the poll budget, transient), `xml` (unparseable Flex
    response or an `ErrorCode` that is not four digits, not transient). An `ErrorCode` is accepted only when it
    matches `^[0-9]{4}$`; anything else is `xml`.
    `IB_TRANSIENT_CODES = {"1001", "1003", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"}` (statement
    not yet available / incomplete / settlement, P&L data not ready / server under heavy load / too many requests /
    in progress / could not be retrieved); every other code — `1010`–`1017` (inactive service account, invalid or
    expired token, IP restriction, invalid query, invalid account state, invalid reference code), `1020` (invalid
    request) and any code not in the table — latches (fail closed: the operator looks).
    Every `raise` of a wrapped httpx error uses `from None`, which suppresses the context in the standard traceback
    but does not delete `__context__`; the redacting filter below is the boundary that holds for anything that
    formats more than the standard traceback.
  - `class _Redact(logging.Filter)`: `__init__(self, secret: str)`; `filter(record)`: (a) folds the message —
    `record.msg = record.getMessage().replace(secret, "***")`, `record.args = ()`; (b) formats the exception ITSELF
    before any handler can — `if record.exc_info: record.exc_text = "".join(traceback.format_exception(*record.exc_info)).replace(secret, "***"); record.exc_info = None`
    (`logging.Formatter.format` uses a pre-set `exc_text` and skips its own formatting, `logging/__init__.py`
    `Formatter.format`: `if record.exc_info: if not record.exc_text: …`); (c) `record.stack_info` likewise; returns
    True. `fetch` installs one instance on the root logger and on every root handler for its duration
    (`try/finally` removes them), and sets `httpx` / `httpcore` loggers to WARNING with `propagate = False` (a child
    logger set to DEBUG by someone else still propagates to its parent; `propagate = False` on the parent stops it
    reaching the root handlers). Test: a `logger.exception(...)` inside an `except` of a request-bearing
    `ReadTimeout` shows `***` in `caplog.text` and never the token.
  - `fetch(http: httpx.Client, creds: Credentials, *, base_url: str, attempts: int, interval_s: float, sleep=time.sleep) -> bytes`
    — GET `{base_url}/SendRequest` with `params={"t": creds.token, "q": creds.query_id, "v": "3"}`; on
    `Status == Success` loop up to `attempts` times GET `{base_url}/GetStatement` with `params={"q": referenceCode,
    "t": creds.token, "v": "3"}`: a `FlexQueryResponse` body returns the bytes; a `FlexStatementResponse` with
    `ErrorCode == 1019` sleeps `interval_s` and retries; any other error code raises `get:<code>`.
  - `quiet_http_logging()` — sets the `httpx` and `httpcore` loggers to WARNING and `propagate = False` (httpx logs
    every request URL at INFO; httpcore's children log at DEBUG); `fetch` calls it first; `cli.quiet_libraries`
    calls it too.

- [ ] **Step 1: Write the failing tests** (append to `tests/worker/test_flex.py`):

```python
FLEX = "https://flex.test/AccountManagement/FlexWebService"
SEND_OK = b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>555</ReferenceCode><Url>x</Url></FlexStatementResponse>"
SEND_FAIL = b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1020</ErrorCode><ErrorMessage>Invalid request</ErrorMessage></FlexStatementResponse>"
PENDING = b"<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode><ErrorMessage>in progress</ErrorMessage></FlexStatementResponse>"
STATEMENT = b'<FlexQueryResponse queryName="q" type="AF"><FlexStatements count="1"><FlexStatement accountId="U1"/></FlexStatements></FlexQueryResponse>'


class _Flex:
    """A scripted Flex Web Service: `script` is the list of (status, body) answers for GetStatement; `raise_on_send`
    is an exception instance, `raise_factory` a callable taking the request (for exceptions that carry the URL)."""
    def __init__(self, send=SEND_OK, script=((200, STATEMENT),), raise_on_send=None, raise_factory=None):
        self.send, self.script, self.raise_on_send, self.raise_factory, self.urls = send, list(script), raise_on_send, raise_factory, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        if request.url.path.endswith("/SendRequest"):
            if self.raise_factory:
                raise self.raise_factory(request)
            if self.raise_on_send:
                raise self.raise_on_send
            return httpx.Response(200, content=self.send)
        status, body = self.script.pop(0)
        return httpx.Response(status, content=body)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fetch(handler, creds, attempts=3):
    return flex.fetch(_client(handler), creds, base_url=FLEX, attempts=attempts, interval_s=0, sleep=lambda s: None)


def test_fetch_returns_the_statement_after_polling_past_pending(tmp_path):
    svc = _Flex(script=[(200, PENDING), (200, PENDING), (200, STATEMENT)])
    creds = flex.load_credentials(_creds(tmp_path))
    assert _fetch(svc, creds) == STATEMENT
    assert [u.split("?")[0].rsplit("/", 1)[1] for u in svc.urls] == ["SendRequest", "GetStatement", "GetStatement", "GetStatement"]
    assert all(f"t={TOKEN}" in u for u in svc.urls) and "q=1234567" in svc.urls[0] and "q=555" in svc.urls[1]


def test_fetch_gives_up_after_the_poll_budget_without_latching(tmp_path):
    svc = _Flex(script=[(200, PENDING)] * 3)
    with pytest.raises(flex.FlexError) as exc:
        _fetch(svc, flex.load_credentials(_creds(tmp_path)), attempts=3)
    assert str(exc.value) == "get:pending" and exc.value.transient is True


def _status(code: bytes) -> bytes:
    return b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>" + code + b"</ErrorCode><ErrorMessage>m</ErrorMessage></FlexStatementResponse>"


def _request_url_error(request: httpx.Request) -> Exception:
    return httpx.ReadTimeout("timed out", request=request)  # the exception that carries the URL (and the token)


@pytest.mark.parametrize("svc, code, transient", [
    (lambda: _Flex(send=SEND_FAIL), "send:1020", False),
    (lambda: _Flex(send=_status(b"1012")), "send:1012", False),      # token expired: latch
    (lambda: _Flex(send=_status(b"1018")), "send:1018", True),       # too many requests: retry tomorrow
    (lambda: _Flex(script=[(200, _status(b"1003"))]), "get:1003", True),
    (lambda: _Flex(script=[(200, _status(b"1017"))]), "get:1017", False),
    (lambda: _Flex(script=[(200, _status(b"9999"))]), "get:9999", False),  # unknown code: fail closed
    (lambda: _Flex(send=_status(b"DROP TABLE")), "xml", False),
    (lambda: _Flex(send="<FlexStatementResponse/>".encode("utf-16")), "xml", False),   # not UTF-8
    (lambda: _Flex(send=b"<!DOCTYPE x><FlexStatementResponse><Status>Success</Status></FlexStatementResponse>"), "xml", False),
    (lambda: _Flex(script=[(200, b"<FlexQueryResponse><!ENTITY a 'b'></FlexQueryResponse>")]), "xml", False),
    (lambda: _Flex(script=[(403, b"forbidden")]), "http:403", False),
    (lambda: _Flex(script=[(429, b"slow down")]), "http:429", True),
    (lambda: _Flex(script=[(408, b"timeout")]), "http:408", True),
    (lambda: _Flex(script=[(503, b"later")]), "http:503", True),
    (lambda: _Flex(raise_on_send=httpx.ConnectError("boom")), "http:ConnectError", True),
    (lambda: _Flex(raise_factory=_request_url_error), "http:ReadTimeout", True),
    (lambda: _Flex(send=b"<nope"), "xml", False),
])
def test_fetch_maps_failures_to_codes_without_the_token(tmp_path, caplog, svc, code, transient):
    import traceback
    with caplog.at_level(logging.DEBUG), pytest.raises(flex.FlexError) as exc:
        _fetch(svc(), flex.load_credentials(_creds(tmp_path)))
    assert (str(exc.value), exc.value.transient) == (code, transient)
    assert TOKEN not in str(exc.value) and TOKEN not in repr(exc.value) and exc.value.__cause__ is None
    assert exc.value.__context__ is None or exc.value.__suppress_context__  # a wrapped httpx error is raised `from None`
    assert TOKEN not in "".join(traceback.format_exception(exc.value))  # the standard traceback, context suppressed
    assert TOKEN not in caplog.text


def test_fetch_keeps_httpx_and_httpcore_debug_lines_out_of_the_log(tmp_path, caplog):
    for name in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(name).setLevel(logging.DEBUG)  # a stray DEBUG level on a child must not leak the URL
    with caplog.at_level(logging.DEBUG):
        _fetch(_Flex(), flex.load_credentials(_creds(tmp_path)))
    assert TOKEN not in caplog.text


def test_fetch_redacts_the_token_if_anything_logs_it_anyway(tmp_path, caplog):
    class _Loud:
        def __init__(self): self.urls = []
        def __call__(self, request):
            log = logging.getLogger("worker.someone")
            log.warning("url %s", request.url)                     # a future bug that logs the request
            try:
                raise httpx.ReadTimeout("x", request=request)
            except httpx.ReadTimeout:
                log.exception("failed %s", request.url)             # and one that logs the exception with exc_info
                raise
    with caplog.at_level(logging.DEBUG), pytest.raises(flex.FlexError):
        _fetch(_Loud(), flex.load_credentials(_creds(tmp_path)))
    assert TOKEN not in caplog.text and caplog.text.count("***") >= 2 and "ReadTimeout" in caplog.text


def test_fetch_a_timeout_that_carries_the_request_does_not_expose_it(tmp_path):
    with pytest.raises(flex.FlexError) as exc:
        _fetch(_Flex(raise_factory=_request_url_error), flex.load_credentials(_creds(tmp_path)))
    assert not hasattr(exc.value, "request") and TOKEN not in repr(vars(exc.value))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/worker/test_flex.py -v` — Expected: the new tests FAIL (`AttributeError: fetch`).

- [ ] **Step 3: Implement `FlexError`, `IB_TRANSIENT_CODES`, `_Redact`, `quiet_http_logging`, `fetch`** in
`worker/flex.py`. Parse every response body with the same guard as Task 2's `_root`: `text =
body.decode("utf-8-sig")` (`UnicodeDecodeError → FlexError("xml")`), `"<!DOCTYPE" in text or "<!ENTITY" in text →
FlexError("xml")` over the whole text, then `ET.fromstring(text)` (`ET.ParseError → FlexError("xml", transient=False)`);
a `_parse_flex_xml(body: bytes) -> ET.Element` helper used for SendRequest and GetStatement alike; `response.status_code >= 400 → FlexError(f"http:{status}",
transient=status >= 500 or status in (408, 429))`; `except httpx.HTTPError as exc: raise
FlexError(f"http:{type(exc).__name__}", transient=True) from None` — the `FlexError` never stores the request or the
response. Never call `raise_for_status()`. Log at most `log.info("flex: %s", code)` on failure.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/worker/test_flex.py -v` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add worker/flex.py tests/worker/test_flex.py
git commit -m "feat(worker): fetch the IB Flex statement; the token stays in the t parameter and out of every sink"
```

---

### Task 6: `flex.run`, the latch, and `python -m worker flex`

**Files:**
- Modify: `services/accounting-service/worker/flex.py`
- Modify: `services/accounting-service/worker/cli.py` (docstring `:1`, parsers `:36-50`, a new branch before the
  `run|poll|backfill` precheck `:99`)
- Test: `services/accounting-service/tests/worker/test_flex.py`

**Interfaces:**
- Consumes: `fetch`, `load_credentials`, `parser.write_private_json` (Task 4), `cfg.state_dir`, the portfolio
  route (Task 3: `POST {portfolio_api_url}/api/portfolio/imports/ib-flex?dry_run=<bool>` multipart field `file`,
  JSON body with `parsed`, `created`, `skipped_duplicates`, `legacy_duplicates`, `errors[]`, `recalc_scheduled`).
- Produces:
  - `class FlexDisabled(RuntimeError)`.
  - `LATCH = "flex-disabled.json"`; latch body `{"reason": <code>, "at": <ISO UTC>}` written with
    `write_private_json(cfg.state_dir, cfg.state_dir / LATCH, ...)`.
  - `class AlreadyRunning(RuntimeError)` (flex's own; `pipeline.AlreadyRunning` is the statement runs' lock).
  - `run(cfg, *, flex_http, portfolio_http, dry_run=False, query_id=None, from_file=None, legacy_until=None, unlatch=False, sleep=time.sleep) -> dict`:
    0. Take `fcntl.flock(LOCK_EX | LOCK_NB)` on `cfg.state_dir / ".flex.lock"` (0600) for the whole run; busy →
       `raise AlreadyRunning`.
    1. If the latch exists and not `unlatch` → `raise FlexDisabled("flex disabled until `python -m worker flex --unlatch` succeeds")`.
    2. `data = from_file.read_bytes()` when given; else `creds = load_credentials(cfg.flex_credentials_file)`
       (replace `query_id` when given), `data = fetch(flex_http, creds, base_url=cfg.flex_base_url,
       attempts=cfg.flex_poll_attempts, interval_s=cfg.flex_poll_interval_s, sleep=sleep)`, then save it as
       `cfg.state_dir / "flex" / f"flex-{YYYYmmddTHHMMSSffffffZ}-{pid}.xml"` (microseconds + pid: unique even for two
       runs in one second; dir 0700, file 0600 via `os.open(..., O_WRONLY|O_CREAT|O_EXCL, 0o600)`);
       `summary["saved"] = <file name>` (a name only).
    3. `resp = portfolio_http.post("/api/portfolio/imports/ib-flex", params={"dry_run": ..., **({"legacy_until": legacy_until.isoformat()} if legacy_until else {})}, files={"file": ("flex.xml", data, "application/xml")}, timeout=cfg.flex_timeout_s)`;
       `status >= 400 → FlexError(f"portfolio:{status}", transient=status >= 500 or status in (408, 429))`;
       `httpx.HTTPError → FlexError("portfolio:<ClassName>", transient=True) from None`.
    4. Summary (codes and counts only): `{"dry_run", "source": "fetch"|"file", "bytes", "saved", "parsed",
       "created", "skipped_duplicates", "legacy_duplicates" (a count; `legacy_pairs` is NOT copied — ids stay in the
       API), "candidates", "errors": <len(body["errors"])>,
       "error_codes": {<code>: <count>}, "recalc_scheduled", "codes": []}` where `error_codes` maps each API
       `errors[].reason` through `ERROR_CODES` — an ordered list of `(regex, code)`: `^missing FX rate` → `missing_fx`,
       `^unsupported assetCategory` → `unsupported_asset_category`, `^unsupported cash transaction type` →
       `unsupported_cash_type`, `^unsupported section` → `unsupported_section`, `^ibCommissionCurrency` →
       `commission_currency`, `^ambiguous legacy match` → `ambiguous_legacy`, `without (tradeID|transactionID)` →
       `missing_id`, anything else → `other` — and counts; the reason text itself is never stored or printed.
    5. On `FlexError` not transient: only for a plain run (no `from_file`, no `query_id`, no `dry_run`) write the latch with
       `reason = code` and `summary["codes"] = ["flex_disabled", code]`; otherwise `["failed", code]` and no latch;
       transient: `summary["codes"] = ["transient", code]`, no latch. The latch is removed only when `unlatch` was
       passed AND the run was a plain daily run (no `from_file`, no `query_id`, no `dry_run`) AND it succeeded —
       only that proves the daily credentials and query work again. `run` never raises `FlexError`; it returns the summary.
  - CLI: `p_fx = sub.add_parser("flex")` with `--dry-run` (store_true), `--query-id` (str, default None),
    `--from-file` (Path, default None), `--legacy-until` (`date.fromisoformat`, default None), `--unlatch`
    (store_true). Branch: unless `--from-file`, the credentials file must be a file (`"flex credentials file is
    missing or not a file"`, exit 2, like the token precheck);
    `with httpx.Client(timeout=cfg.flex_timeout_s) as flex_http, httpx.Client(base_url=cfg.portfolio_api_url, timeout=cfg.flex_timeout_s) as portfolio_http:`
    `summary = flex.run(...)`; `print(json.dumps(summary, ensure_ascii=False))`; exit `1 if summary["codes"] else 0`;
    `FlexDisabled` → stderr `flex disabled: …`, exit 1; `flex.AlreadyRunning` → stdout `already running`, exit 3 (as
    `gate` / `verify`); `ValueError` from the credentials file → stderr the message, exit 2. Any other exception
    escaping `flex.run` is caught in the CLI branch, printed as `flex failed: <ClassName>` on stderr (class name only,
    no traceback: the CLI is the output boundary), exit 1.

- [ ] **Step 1: Write the failing tests** (append to `tests/worker/test_flex.py`):

```python
PORTFOLIO_OK = {"parsed": 11, "created": 11, "skipped_duplicates": 0, "legacy_duplicates": 0, "candidates": 11,
                "legacy_pairs": [], "error_counts": {"parse": 2, "ambiguous": 0, "commit": 1},
                "errors": [{"row_index": 9, "message": "unsupported section Transfers", "reason": "unsupported section Transfers"},
                           {"row_index": 4, "message": "missing FX rate for 2026-06-05 USD", "reason": "missing FX rate for 2026-06-05 USD"},
                           {"row_index": 5, "message": f"something odd {TOKEN}", "reason": f"something odd {TOKEN}"}],  # a reason must never be copied
                "recalc_scheduled": True}


class _Portfolio:
    def __init__(self, status=200, body=PORTFOLIO_OK, raise_exc: Exception | None = None):
        self.status, self.body, self.raise_exc, self.requests = status, body, raise_exc, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((str(request.url), dict(request.headers), request.read()))
        if self.raise_exc:
            raise self.raise_exc
        return httpx.Response(self.status, json=self.body)


def _cfg(tmp_path) -> config.WorkerConfig:
    return config.load({"STATEMENT_STATE_DIR": str(tmp_path / "state"), "STATEMENT_FLEX_CREDENTIALS_FILE": str(_creds(tmp_path)),
                        "STATEMENT_FLEX_BASE_URL": FLEX, "STATEMENT_FLEX_POLL_INTERVAL": "0", "STATEMENT_PORTFOLIO_API_URL": "http://portfolio.test"})


def _run(cfg, svc=None, portfolio=None, **kw):
    svc, portfolio = svc or _Flex(), portfolio or _Portfolio()
    with _client(svc) as fh, httpx.Client(base_url="http://portfolio.test", transport=httpx.MockTransport(portfolio)) as ph:
        return flex.run(cfg, flex_http=fh, portfolio_http=ph, sleep=lambda s: None, **kw), svc, portfolio


def test_run_fetches_saves_and_posts_the_statement(tmp_path, caplog):
    cfg = _cfg(tmp_path)
    with caplog.at_level(logging.INFO):
        summary, svc, portfolio = _run(cfg)
    saved = list((cfg.state_dir / "flex").glob("flex-*.xml"))
    assert len(saved) == 1 and saved[0].read_bytes() == STATEMENT and oct(saved[0].stat().st_mode)[-3:] == "600" and oct((cfg.state_dir / "flex").stat().st_mode)[-3:] == "700"
    assert summary == {"dry_run": False, "source": "fetch", "bytes": len(STATEMENT), "saved": saved[0].name, "parsed": 11, "created": 11,
                       "skipped_duplicates": 0, "legacy_duplicates": 0, "candidates": 11, "errors": 3,
                       "error_codes": {"unsupported_section": 1, "missing_fx": 1, "other": 1}, "recalc_scheduled": True, "codes": []}
    assert "2026-06-05" not in json.dumps(summary)  # a date from a reason is text, not a code
    url, headers, body = portfolio.requests[0]
    assert url == "http://portfolio.test/api/portfolio/imports/ib-flex?dry_run=false" and STATEMENT in body
    assert TOKEN not in url and TOKEN not in body.decode("latin-1") and TOKEN not in json.dumps(headers) and TOKEN not in json.dumps(summary) and TOKEN not in caplog.text
    assert not (cfg.state_dir / flex.LATCH).exists()


def test_run_dry_run_query_id_and_legacy_until_reach_both_services(tmp_path):
    from datetime import date
    summary, svc, portfolio = _run(_cfg(tmp_path), dry_run=True, query_id="<backfill-query-id>", legacy_until=date(2026, 6, 30))
    assert "q=<backfill-query-id>" in svc.urls[0] and summary["dry_run"] is True
    assert portfolio.requests[0][0] == "http://portfolio.test/api/portfolio/imports/ib-flex?dry_run=true&legacy_until=2026-06-30"


def test_run_from_file_posts_without_calling_ib(tmp_path):
    saved = tmp_path / "old.xml"; saved.write_bytes(STATEMENT)
    summary, svc, portfolio = _run(_cfg(tmp_path), from_file=saved)
    assert svc.urls == [] and summary["source"] == "file" and summary["saved"] is None and len(portfolio.requests) == 1


def test_run_latches_on_a_failed_send_request_and_only_a_plain_unlatch_run_clears_it(tmp_path):
    cfg = _cfg(tmp_path)
    summary, _, portfolio = _run(cfg, svc=_Flex(send=SEND_FAIL))
    assert summary["codes"] == ["flex_disabled", "send:1020"] and portfolio.requests == []
    latch = json.loads((cfg.state_dir / flex.LATCH).read_text(encoding="utf-8"))
    assert latch["reason"] == "send:1020" and TOKEN not in json.dumps(latch) and oct((cfg.state_dir / flex.LATCH).stat().st_mode)[-3:] == "600"
    with pytest.raises(flex.FlexDisabled):
        _run(cfg)
    saved = tmp_path / "old.xml"; saved.write_bytes(STATEMENT)
    for kw in ({"from_file": saved}, {"dry_run": True}, {"query_id": "<backfill-query-id>"}):
        summary, _, _ = _run(cfg, unlatch=True, **kw)   # these prove nothing about the daily credentials
        assert summary["codes"] == [] and (cfg.state_dir / flex.LATCH).exists()
    summary, _, _ = _run(cfg, unlatch=True)
    assert summary["codes"] == [] and not (cfg.state_dir / flex.LATCH).exists()


def test_run_refuses_to_overlap_itself(tmp_path):
    import fcntl
    cfg = _cfg(tmp_path); cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / ".flex.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(flex.AlreadyRunning):
            _run(cfg)


def test_run_file_names_are_unique_across_two_runs_in_one_second(tmp_path):
    cfg = _cfg(tmp_path)
    _run(cfg); _run(cfg)
    assert len(list((cfg.state_dir / "flex").glob("flex-*.xml"))) == 2


def test_run_keeps_the_saved_xml_when_the_portfolio_post_fails(tmp_path):
    cfg = _cfg(tmp_path)
    summary, _, _ = _run(cfg, portfolio=_Portfolio(raise_exc=httpx.ConnectError("down")))
    assert summary["codes"] == ["transient", "portfolio:ConnectError"] and len(list((cfg.state_dir / "flex").glob("flex-*.xml"))) == 1
    assert not (cfg.state_dir / flex.LATCH).exists()
    busy = _run(cfg, portfolio=_Portfolio(status=429, body={"detail": "slow down"}))[0]
    assert busy["codes"] == ["transient", "portfolio:429"] and not (cfg.state_dir / flex.LATCH).exists()
    bad = _run(cfg, portfolio=_Portfolio(status=400, body={"detail": "flex: 1020"}))[0]
    assert bad["codes"] == ["flex_disabled", "portfolio:400"] and (cfg.state_dir / flex.LATCH).exists()


def test_run_transient_fetch_failure_does_not_latch_or_save(tmp_path):
    cfg = _cfg(tmp_path)
    summary, _, portfolio = _run(cfg, svc=_Flex(script=[(503, b"x")]))
    assert summary["codes"] == ["transient", "http:503"] and portfolio.requests == [] and not (cfg.state_dir / "flex").exists()


def test_cli_flex_prints_one_json_line_and_exits_by_codes(tmp_path, monkeypatch, capsys):
    from worker import cli
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    calls = []
    monkeypatch.setattr(flex, "run", lambda c, **kw: calls.append(kw) or {"codes": [], "created": 0})
    assert cli.main(["flex", "--dry-run", "--query-id", "<backfill-query-id>", "--legacy-until", "2026-06-30"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out.strip()) == {"codes": [], "created": 0} and err == ""
    assert calls[0]["dry_run"] is True and calls[0]["query_id"] == "<backfill-query-id>" and calls[0]["from_file"] is None and calls[0]["unlatch"] is False
    assert str(calls[0]["legacy_until"]) == "2026-06-30"
    monkeypatch.setattr(flex, "run", lambda c, **kw: {"codes": ["transient", "http:503"]})
    assert cli.main(["flex"]) == 1
    out, err = capsys.readouterr()                                        # drain: one JSON line, nothing on stderr
    assert json.loads(out.strip())["codes"] == ["transient", "http:503"] and err == ""
    monkeypatch.setattr(flex, "run", lambda c, **kw: (_ for _ in ()).throw(flex.AlreadyRunning()))
    assert cli.main(["flex"]) == 3
    out, err = capsys.readouterr()
    assert out.strip() == "already running" and err == ""
    monkeypatch.setattr(flex, "run", lambda c, **kw: (_ for _ in ()).throw(RuntimeError(f"boom {TOKEN}")))
    assert cli.main(["flex"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "flex failed: RuntimeError" in err and TOKEN not in err and "Traceback" not in err


def test_cli_flex_exit_codes_for_missing_credentials_and_latch(tmp_path, monkeypatch, capsys):
    from worker import cli
    import dataclasses
    cfg = dataclasses.replace(_cfg(tmp_path), flex_credentials_file=tmp_path / "absent.env")
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    assert cli.main(["flex"]) == 2 and "flex credentials file is missing" in capsys.readouterr().err
    cfg2 = _cfg(tmp_path)
    monkeypatch.setattr(config, "load", lambda env=None: cfg2)
    monkeypatch.setattr(flex, "run", lambda c, **kw: (_ for _ in ()).throw(flex.FlexDisabled("x")))
    assert cli.main(["flex"]) == 1 and "flex disabled" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/worker/test_flex.py -v` — Expected: the new tests FAIL (`AttributeError: run` / argparse error
`invalid choice: 'flex'`).

- [ ] **Step 3: Implement `FlexDisabled`, `AlreadyRunning`, `LATCH`, `ERROR_CODES`, `run`** in `worker/flex.py` and
the `flex` subcommand in `worker/cli.py` per the Interfaces block. Use `logging.getLogger("worker")`; log codes only.
In `cli.py` import the module (`from worker import flex`) and call `flex.run(...)` by attribute, so tests can
monkeypatch `flex.run`.

- [ ] **Step 4: Run to verify they pass, then the whole worker suite**

Run: `pytest tests/worker/test_flex.py -v && pytest -q tests/worker` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add worker/flex.py worker/cli.py tests/worker/test_flex.py
git commit -m "feat(worker): python -m worker flex — fetch, store, post, latch"
```

---

### Task 7: systemd units, READMEs, spec §7 rev 3

**Files:**
- Create: `services/accounting-service/deploy/statements/homehub-portfolio-flex.service`,
  `services/accounting-service/deploy/statements/homehub-portfolio-flex.timer`
- Modify: `services/accounting-service/deploy/statements/README.md` (install steps `:21-38`; a new section),
  `services/accounting-service/README.md` (commands list `~:397-404`, exit codes `~:405-412`, config table
  `~:421-446`, a paragraph after the composite one), `docs/superpowers/specs/2026-10-19-portfolio-integration-design.md`
  (header status line `:3-7`, §7 `:201-212`, §10 "Worker" and "Portfolio" bullets).

**Interfaces:** consumes the CLI from Task 6 and the config keys from Task 4; produces nothing code-level.

- [ ] **Step 1: Write the units**

`homehub-portfolio-flex.service`:
```ini
[Unit]
Description=HomeHub portfolio: fetch the IB Flex statement and import it

[Service]
Type=oneshot
WorkingDirectory=%h/workspace/home-hub/services/accounting-service
EnvironmentFile=-%h/.config/homehub-statements.env
UMask=0077
ExecStart=%h/workspace/home-hub/services/accounting-service/.venv/bin/python -m worker flex
Nice=10
```

`homehub-portfolio-flex.timer`:
```ini
[Unit]
Description=HomeHub portfolio: IB Flex statement daily at 15:00 Taipei (after the US close and IB's overnight statement run)

[Timer]
OnCalendar=*-*-* 15:00:00 Asia/Taipei
Persistent=true
Unit=homehub-portfolio-flex.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Verify the units parse**

Run: `systemd-analyze --user verify services/accounting-service/deploy/statements/homehub-portfolio-flex.service services/accounting-service/deploy/statements/homehub-portfolio-flex.timer; echo exit $?`
Expected: `exit 0` (warnings about `%h` paths not existing on the dev host are acceptable; errors are not).

- [ ] **Step 3: Deploy README** — in the install steps change the `ln -sf` glob to
`homehub-{statements,portfolio-flex}*.{service,timer}` and `list-timers 'homehub-*'`; add a section
"IB Flex statement timer (Phase 2, 2026-10-20)" stating: the credentials file
`~/.config/homehub-portfolio-flex.env` (worker user, 0600, two `KEY=VALUE` lines `IB_FLEX_TOKEN`, `IB_FLEX_QUERY_ID`,
written with `read -s`, never on a command line or in chat); `STATEMENT_FLEX_*` / `STATEMENT_PORTFOLIO_API_URL`
overrides; what the timer does; the state files `<state_dir>/flex/flex-<ts>.xml` (0600) and
`<state_dir>/flex-disabled.json`; the latch rule (IB status codes outside `IB_TRANSIENT_CODES`, `xml`,
`portfolio:4xx` other than 408/429 latch; IB transient codes, HTTP 5xx / 408 / 429, timeouts, connection errors and
`get:pending` do not); a run that does not latch but fails still exits 1, so `systemctl --user list-units --failed`
and the Loki query `{job="homehub-statements"} |= "flex" | json | codes != ""` show a query that is pending every
day; recovery `python -m worker flex --unlatch` (a plain daily run: `--dry-run`, `--query-id`, `--from-file` never
clear the latch); the one-off backfill `python -m worker flex --query-id <id> --legacy-until 2026-06-30 --dry-run`
(read `candidates`, `legacy_duplicates`, `error_codes`) then the same without `--dry-run`; re-posting a saved file
`python -m worker flex --from-file <state_dir>/flex/flex-<ts>.xml`; enabling
`systemctl --user enable --now homehub-portfolio-flex.timer`.

- [ ] **Step 4: Service README** — add `python -m worker flex [--dry-run] [--query-id ID] [--from-file XML]
[--legacy-until DATE] [--unlatch]` to the commands list; exit codes: "0 imported (errors in the body are counted,
not fatal); 1 any code in `codes` (`flex_disabled`, `transient`), the latch, or an unexpected exception (class name
on stderr); 2 credentials file missing/malformed or config error; 3 already running"; six rows in the config
table; one paragraph "IB Flex statements (2026-10-20)" naming the adapter `broker_ib_flex_service`, the route and
its `dry_run` / `legacy_until` parameters, the fingerprint (existing functions, note carries the Flex id, market from
`listingExchange`), signed cash amounts, the `legacy_until` pairing pass and its ambiguity error, the sections
imported (Trades EXECUTION/STK, CashTransactions) and reported (Transfers, CorporateActions), the sections ignored
(OpenPositions, ConversionRates — positions derive from transactions and corporate actions are reported, not
applied), the summary keys with the `error_codes` allowlist, and the rule: once Flex is live the IB Transfer-History
CSV is never uploaded again (a CSV row and its Flex twin carry different fingerprints; the legacy pass adopted the
Flex one), and the manual-era overlap is handled exactly once, by the backfill run with `--legacy-until`.

- [ ] **Step 5: Spec rev 3** — edit `docs/superpowers/specs/2026-10-19-portfolio-integration-design.md`:
  - status line: prepend `Status: rev 3 (2026-10-20) §7 amended by the Phase 2 plan: XML instead of CSV; the token
    travels only in the Flex `t` query parameter (IB offers no other transport); section list; legacy-duplicate
    pass; `--unlatch` / `--dry-run` / `--query-id` / `--from-file`.` and keep the rev 2 text below it.
  - §7 bullet 1: `CSV` → `XML (Trades at execution level, Cash Transactions, Transfers, Corporate Actions, Open
    Positions; last 30 calendar days; yyyy-MM-dd; currency rates off)`.
  - §7 bullet 2: `stores the CSV` → `stores the XML`; endpoint `POST /api/portfolio/imports/ib-flex`; `Flex CSV is not
    the Activity Statement CSV` → `Flex XML is not the Transfer History CSV the IB importer reads today`; sections
    `(Trades, CashTransactions; Transfers and CorporateActions are reported, OpenPositions ignored)`; replace the
    fingerprint sentence `so a Flex row and the same event … cash_flow_fingerprint)` with: `using the existing
    fingerprint functions with the Flex tradeID / transactionID in the note, so two identical same-day fills stay two
    rows; the market comes from the Flex listingExchange (LSE for LSE/LSEETF/LSEIOB); cash amounts keep IB's sign.
    A Flex row never shares a fingerprint with a row from the manual Transfer-History CSV imports; the one-off
    backfill over that era runs with legacy_until=<date>, which pairs rows one-to-one by business key (symbol,
    type, quantity, price, date, currency, fee, tax; cash: date, type, signed amount, currency) with the rows dated
    up to that day, drops the pairs, and reports any key with unequal counts as "ambiguous legacy match" for the
    owner to resolve; a paired row adopts the Flex fingerprint, so no later import re-creates it; the daily run never
    pairs. Once Flex is live the IB Transfer-History CSV is never uploaded again: a CSV row and its Flex twin carry
    different fingerprints, so a re-upload would duplicate.`
  - §7 bullet 3: replace `request URL recorded by the test transport` with `every request other than the two Flex
    calls (the token is the t query parameter of SendRequest and GetStatement and nothing else: never a header,
    never the portfolio request); the CLI is the output boundary: codes and counts on stdout, class names on stderr,
    no traceback, and a redacting filter on the root logger while the token is in use`; replace `Failures latch like
    the parser gate (flex-disabled.json) and surface in the run summary` with `Non-transient failures (an IB status
    code outside the transient table, an unparseable response, a portfolio 4xx other than 408/429) latch like the
    parser gate (flex-disabled.json); transient ones (IB codes 1003–1009, 1018, 1019, 1021; HTTP 5xx / 408 / 429;
    timeouts) exit 1 without latching and the unit shows failed`; add `Recovery: python -m worker flex --unlatch (a
    plain daily run; --dry-run, --query-id and --from-file never clear the latch); a one-off backfill query runs with
    --query-id and --legacy-until; a saved XML re-posts with --from-file.`
  - §10 "Worker" bullet: `fixture CSV` → `fixture XML`; "Portfolio" bullet: `Flex adapter dedupes against an
    Activity-import fingerprint` → `Flex adapter dedupes re-imports by fingerprint (Flex id in the note) and, with
    legacy_until, pairs the manual-era rows one-to-one by business key, reporting ambiguity`.

- [ ] **Step 6: Verify the docs mention every surface**

Run: `grep -c "worker flex" services/accounting-service/README.md services/accounting-service/deploy/statements/README.md; grep -n "rev 3" docs/superpowers/specs/2026-10-19-portfolio-integration-design.md | head -1; grep -n "STATEMENT_FLEX_CREDENTIALS_FILE" services/accounting-service/README.md`
Expected: counts ≥ 1 in both READMEs; the rev 3 line; the config row.

- [ ] **Step 7: Commit**

```bash
git add services/accounting-service/deploy/statements/homehub-portfolio-flex.service services/accounting-service/deploy/statements/homehub-portfolio-flex.timer services/accounting-service/deploy/statements/README.md services/accounting-service/README.md docs/superpowers/specs/2026-10-19-portfolio-integration-design.md
git commit -m "docs(portfolio): IB Flex timer units, README, spec §7 rev 3"
```

---

## Rollout (spec §11 step 3, after merge)

1. Deploy: worker checkout to the merged main; portfolio service restart (new route); `ln -sf` the two units,
   `daemon-reload`; do NOT enable the timer yet.
2. Owner-run dry-run of the backfill query as the worker user: `python -m worker flex --query-id <backfill-query-id>
   --legacy-until 2026-06-30 --dry-run` — `2026-06-30` covers the whole CSV-imported range (the last CSV row is
   2026-06-05) → read `candidates` / `legacy_duplicates` / `error_codes` from the JSON line (`created` is 0 on a dry
   run by design; expected from the 2026-10-20 sample: `candidates` 5 — 3 trades + 2 cash flows — `legacy_duplicates`
   4 — the three 2026-06-02 trades and the 2026-06-01 deposit — `ambiguous_legacy` 0, `missing_fx` only for dates
   past `fx_rates`); the pair list (ids) is in the API response, readable with `curl -s -F file=@<saved xml>
   'http://127.0.0.1:8001/api/portfolio/imports/ib-flex?dry_run=true&legacy_until=2026-06-30' | jq .legacy_pairs`
   as the worker user; then the same without `--dry-run` (the four rows adopt their Flex fingerprints). New IB
   trades land with `market = LSE` straight from `listingExchange`, matching the hand-corrected rows. From this
   point the IB CSV is never uploaded again.
3. Daily query dry-run, then real: `python -m worker flex --dry-run`, `python -m worker flex`. The backfill query must
   select the same Trade and CashTransaction fields as the daily query; the dry run must show `candidates` 0 (or only
   rows newer than the backfill) and `skipped_duplicates` equal to the overlap — stop otherwise.
4. `systemctl --user enable --now homehub-portfolio-flex.timer`; watch the first timer run in the journal
   (`journalctl --user -u homehub-portfolio-flex.service`).
5. Delete the backfill query in IB Account Management once step 2 has imported.
6. Alloy matcher: add `_SYSTEMD_USER_UNIT=homehub-portfolio-flex.service` before enabling the timer.
