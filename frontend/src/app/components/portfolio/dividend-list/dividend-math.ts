import { Dividend } from '../../../models/portfolio.model';
import { formatFxRate, formatNativeAmount, groupedInteger, isTwdCurrency, twdAmount } from '../money';

export type DividendStatus = 'pending' | 'confirmed' | 'legacy' | 'unresolved';

/** Card figures in TWD (CONTEXT.md): 已確認收款, 有效待收, 舊資料帳面 and 已記錄股利 = confirmed + legacy. */
export interface DividendTotals {
  confirmed: number;
  confirmedCount: number;
  pending: number;
  pendingCount: number;
  legacy: number;
  legacyCount: number;
  recorded: number;
}

/** `legacy_unknown` and a missing status are both 舊資料. */
export function dividendStatus(d: Dividend): DividendStatus {
  const status = d.receipt_status;
  return status === 'pending' || status === 'confirmed' || status === 'unresolved' ? status : 'legacy';
}

/**
 * TWD sums per status over every given row. A foreign row without a finite positive fx is left out of every bucket
 * and counted once in `foreignWithoutFx`; a TWD row whose amount does not parse is skipped silently; `unresolved`
 * rows count nowhere.
 * 已記錄股利 (`recorded`) is confirmed + legacy and never includes pending.
 */
export function dividendTotals(rows: readonly Dividend[]): DividendTotals & { foreignWithoutFx: number } {
  const totals = { confirmed: 0, confirmedCount: 0, pending: 0, pendingCount: 0, legacy: 0, legacyCount: 0, recorded: 0, foreignWithoutFx: 0 };
  for (const d of rows) {
    const status = dividendStatus(d);
    if (status === 'unresolved') continue;
    const twd = twdAmount(d.amount, d.fx_rate_to_twd, d.currency);
    if (twd == null) {
      if (!isTwdCurrency(d.currency)) totals.foreignWithoutFx++;
      continue;
    }
    totals[status] += twd;
    totals[`${status}Count`]++;
  }
  totals.recorded = totals.confirmed + totals.legacy;
  return totals;
}

const STATUS_TAGS: Record<DividendStatus, string> = {
  pending: '待收',
  confirmed: '已收款',
  legacy: '舊資料 · 未核實',
  unresolved: '待人工釐清',
};

export function statusTag(s: DividendStatus): string {
  return STATUS_TAGS[s];
}

function exYear(date: string | Date): string {
  return typeof date === 'string' ? date.slice(0, 4) : String(date.getFullYear());
}

/** 'MM/DD' when the date's year is the ex-date's year, else 'YYYY/MM/DD'; read from the string parts (no Date parsing). */
function shortDate(date: string, exDate: string | Date): string {
  const [year, month, day] = date.slice(0, 10).split('-');
  return year === exYear(exDate) ? `${month}/${day}` : `${year}/${month}/${day}`;
}

/**
 * 發放 / 收款 column: a confirmed row with a receipt date → '收款 MM/DD' over the receiving account's name;
 * any other row with a payment date → '預定 MM/DD' over its `payment_date_source`; otherwise '—'.
 */
export function payLine(d: Dividend, accountName: (id: number) => string | null): { main: string; sub: string | null } {
  if (dividendStatus(d) === 'confirmed') {
    if (!d.receipt_date) return { main: '—', sub: null };
    const account = d.receipt_account_id != null ? accountName(d.receipt_account_id) : null;
    return { main: `收款 ${shortDate(d.receipt_date, d.ex_dividend_date)}`, sub: account ?? null };
  }
  if (!d.payment_date) return { main: '—', sub: null };
  return { main: `預定 ${shortDate(d.payment_date, d.ex_dividend_date)}`, sub: d.payment_date_source || null };
}

/**
 * 淨額 column: TWD rows → '+{TWD}'; foreign rows with fx → '+{TWD}' over '{native} · 匯率 {fx}';
 * foreign rows without fx → '+{native}' over '—' (`twd` null).
 */
export function netAmountLines(d: Dividend): { main: string; sub: string | null; twd: number | null } {
  const twd = twdAmount(d.amount, d.fx_rate_to_twd, d.currency);
  if (isTwdCurrency(d.currency)) return { main: twd == null ? '—' : `+${groupedInteger(twd)}`, sub: null, twd };
  const native = formatNativeAmount(d.amount, d.currency);
  if (twd == null) return { main: `+${native}`, sub: '—', twd: null };
  const rate = twdAmount(1, d.fx_rate_to_twd, d.currency)!;
  return { main: `+${groupedInteger(twd)}`, sub: `${native} · 匯率 ${formatFxRate(rate)}`, twd };
}
