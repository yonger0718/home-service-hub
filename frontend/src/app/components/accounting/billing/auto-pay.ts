import { addDays } from '../dates';

/**
 * Card-statement auto-pay as the SPA reads it (notification center D9): the switch and start date from
 * `GET /settings/reconciliation` (`auto_pay`), and one row per statement from `GET /reconciliation/auto-pay`.
 */
export interface AutoPaySwitch {
  readonly enabled: boolean;
  /** `YYYY-MM-DD`: bills whose debit day is before it are not paid (the backend's `cutoff`). */
  readonly enabled_from: string | null;
  /**
   * 單筆上限 (read only, from `GET /settings/reconciliation`; the write-scope PATCH cannot change it): a statement over
   * it is skipped as `over_cap` on its debit day. Absent when not read (a PATCH answer).
   */
  readonly max_amount?: string | null;
}

/** What `PATCH /settings/reconciliation/auto-pay` may send. */
export type AutoPaySwitchChange = Partial<Pick<AutoPaySwitch, 'enabled' | 'enabled_from'>>;

export type AutoPayStatus = 'booked' | 'retired' | 'owner_deleted' | 'owner_edited' | 'skipped';

export interface AutoPayRow {
  readonly statement_id: number;
  readonly card_account_id: number;
  /** Null for a `no_link` skip. */
  readonly pay_account_id: number | null;
  readonly amount: string;
  readonly due_date: string;
  /** The debit day (`due_date` moved to a business day). */
  readonly pay_date: string;
  readonly status: AutoPayStatus;
  readonly reason: string | null;
  readonly updated_at: string;
}

/** Why a statement was not paid automatically (D9 copy). */
export const AUTO_PAY_REASON_TEXT: Readonly<Record<string, string>> = {
  over_cap: '超過單筆上限',
  monthly_budget: '超過當月上限',
  no_link: '沒有設定扣款帳戶',
  link_ineligible: '扣款帳戶不符規定',
  header_disputed: '帳單金額待確認',
  twin: '已有相同的繳款記錄',
  duplicate_statement: '重複的帳單',
  cutoff: '早於起始日',
  expired: '已超過重試期限',
};

/** Skips that make a 系統 task: the transient reasons and `expired` (owner ruling 2026-11-25). `twin`, `cutoff` and
 * `duplicate_statement` are informational. */
export const AUTO_PAY_TASK_REASONS: ReadonlySet<string> = new Set([
  'over_cap', 'monthly_budget', 'no_link', 'link_ineligible', 'header_disputed', 'expired',
]);

/** How far back the SPA reads rows: a statement's debit day falls inside its next cycle, so 45 days covers the last one. */
export const AUTO_PAY_ROWS_DAYS = 45;

export function autoPayRowsSince(today: string): string {
  return addDays(today, -AUTO_PAY_ROWS_DAYS);
}

/**
 * Whether auto-pay will pay this bill: the card has a pay account, the switch is on with a start date on or before
 * the debit day, the debit day is today or later, the bill (`amount`, the cycle's full statement) is not over 單筆上限,
 * and no row of that card and debit day says it was skipped. An unread switch (null) is not pending: the page then
 * shows the plain due pill. The backend gates only on the debit day, so an over-cap bill would otherwise read as
 * 自動扣繳 until then. 當月上限 depends on the month's other bookings and stays a debit-day skip (a 系統 task).
 */
export function autoPayPending(
  autoPay: AutoPaySwitch | null,
  account: { readonly id: number; readonly auto_pay_account_id?: number | null },
  payDate: string,
  today: string,
  rows: readonly AutoPayRow[] | null,
  amount?: number,
): boolean {
  if (!autoPay?.enabled || !autoPay.enabled_from || account.auto_pay_account_id == null) {
    return false;
  }
  if (payDate < autoPay.enabled_from || payDate < today) {
    return false;
  }
  if (amount !== undefined && autoPay.max_amount != null && amount > Number(autoPay.max_amount)) {
    return false;
  }
  return !(rows ?? []).some(r => r.status === 'skipped' && r.card_account_id === account.id && r.pay_date === payDate);
}

/** Whether a booking (kept or edited by the owner) paid this card on or after `since` (the open cycle's first day). */
export function autoPaidSince(rows: readonly AutoPayRow[] | null, accountId: number, since: string): boolean {
  return (rows ?? []).some(
    r => (r.status === 'booked' || r.status === 'owner_edited') && r.card_account_id === accountId && r.pay_date >= since,
  );
}
