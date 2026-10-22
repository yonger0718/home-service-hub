## MODIFIED Requirements

### Requirement: Summary row

The dividends screen SHALL render four summary cards using the `CONTEXT.md` vocabulary: 已確認收款 (count; footnote 已寫入現金帳), 有效待收 (count; value in `--app-primary`; footnote 納入預估含息損益), 舊資料帳面 (count; footnote 收款未核實; swatch drawn dashed), and 已記錄股利 (value in `var(--app-dividend)`; footnote 已確認 ＋ 舊資料). 已記錄股利 MUST equal 已確認收款 + 舊資料帳面 and MUST NOT include 有效待收. At < 760px only 已確認收款 and 有效待收 SHALL be shown. A note SHALL explain that pending dividends count toward 預估含息損益 but not toward available cash, that amounts under review are excluded, and that legacy receipts are unverified.

#### Scenario: Recorded dividends exclude pending
- **GIVEN** confirmed 37,578, legacy 21,342 and pending 14,600
- **WHEN** the summary renders
- **THEN** 已記錄股利 SHALL show 58,920

#### Scenario: Cumulative dividend uses violet
- **WHEN** the summary renders
- **THEN** the 已記錄股利 value uses `var(--app-dividend)`

### Requirement: Violet dividend timeline

Dividend records SHALL render as a list (a table-like grid at ≥ 760px) with: stock square, name + code (and period), 除息日, 發放 / 收款 (預定 date with its source, or 收款 date with the receiving account), `per-share × qty` with deductions beneath, net amount, and a status tag. Net amounts SHALL use `var(--app-dividend)` for received and legacy rows and `--app-primary` for pending rows. Status tags SHALL read 待收, 已收款 or 舊資料 · 未核實. A status `SegToggle` (全部 / 待收 / 已收款 / 舊資料) SHALL filter the list.

#### Scenario: Dividend row uses violet
- **WHEN** a received dividend row renders
- **THEN** its amount is rendered in `var(--app-dividend)` and its tag reads 已收款

#### Scenario: Status filter
- **WHEN** the owner selects 待收
- **THEN** only pending rows SHALL be listed

## ADDED Requirements

### Requirement: Inline receipt confirmation

Each pending row SHALL show a 確認收款 button. Pressing it SHALL expand the row (only one at a time) into a form titled 確認實際收款 · <name> <amount> with the existing warning (只在核對過實際入帳後確認。預定發放日不是收款證明；確認後會寫入現金帳，無法直接編輯撤銷。), a 實際收款日 date input, a 收款帳戶 select of active cash accounts, and the buttons 取消 and 確認已收到並入帳. Submitting SHALL call the existing `POST /api/portfolio/dividends/{id}/confirm-receipt`; on success the row becomes 已收款 and the summary recomputes. This replaces the receipt dialog.

#### Scenario: Confirming a receipt
- **GIVEN** a pending 00878 dividend of 9,600
- **WHEN** the owner presses 確認收款, picks 2026-11-18 and 元大交割戶, and presses 確認已收到並入帳
- **THEN** the confirm-receipt endpoint SHALL be called with that date and account
- **AND** the row SHALL show 已收款 and 有效待收 SHALL drop by 9,600

#### Scenario: Only one form open
- **GIVEN** the form is open on one pending row
- **WHEN** the owner presses 確認收款 on another row
- **THEN** the first form SHALL close
