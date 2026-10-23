import { Dividend } from '../../../models/portfolio.model';
import { TagVariant } from '../../ui/tag/tag';
import { formatNativeAmount, groupedInteger, isTwdCurrency } from '../money';
import { StockSquare, stockSquare } from '../square';
import { DividendStatus, dividendStatus, netAmountLines, payLine, statusTag } from './dividend-math';

/** The page's status SegToggle: 全部 / 待收 / 已收款 / 舊資料. */
export type DividendStatusFilter = 'all' | 'pending' | 'confirmed' | 'legacy';

/** One `.div-row` of the dividends list (Decision 8). */
export interface DividendRowView {
  id: number;
  status: DividendStatus;
  square: StockSquare;
  /** `{name} {symbol}`, or the symbol alone without a name. */
  primary: string;
  /** 除息日 `YYYY/MM/DD`. */
  exDate: string;
  /** Phone line under the amount: `除息 {exDate} · {pay.main}`, without the pay part when it is `—`. */
  dateLine: string;
  /** 發放 / 收款 column. */
  pay: { main: string; sub: string | null };
  /** 每股 × 股數 column: `0.48 × 20,000`, `0.0001`, or `現金股利`. */
  perShare: string;
  /** `匯費 x · 補充保費 y` (parts above zero), else null. */
  deductions: string | null;
  net: { main: string; sub: string | null };
  /** Pending rows in `--app-primary`, confirmed / legacy / unresolved in `--app-dividend`. */
  netClass: 'primary' | 'dividend';
  tag: { text: string; variant: TagVariant };
  reviewReason: string | null;
  sourceCorrected: boolean;
}

const TAG_VARIANTS: Record<DividendStatus, TagVariant> = {
  pending: 'primary',
  confirmed: 'dividend',
  legacy: 'neutral',
  unresolved: 'warning',
};

export function statusTagVariant(s: DividendStatus): TagVariant {
  return TAG_VARIANTS[s];
}

/** 全部 lists every row; the other options their own status (unresolved rows only under 全部). */
export function matchesStatus(d: Dividend, filter: DividendStatusFilter): boolean {
  return filter === 'all' || dividendStatus(d) === filter;
}

/** 'YYYY/MM/DD': a string's own date part (no Date parsing), a Date's local calendar day. */
export function exDateLabel(date: string | Date): string {
  if (typeof date === 'string') return date.slice(0, 10).replaceAll('-', '/');
  return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(2, '0')}`;
}

/** `0.48 × 20,000` (per share 2–4 fraction digits, quantity grouped; a foreign row suffixes its currency); `現金股利` without a per-share figure. */
export function perShareLine(d: Dividend): string {
  if (d.cash_dividend_per_share == null) return '現金股利';
  const perShare = Number(d.cash_dividend_per_share || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  const qty = Number(d.quantity_at_record_date || 0);
  const price = isTwdCurrency(d.currency) ? perShare : `${perShare} ${d.currency}`;
  return qty > 0 ? `${price} × ${qty.toLocaleString('en-US')}` : price;
}

/** `匯費 {fee} · 補充保費 {tax}` with only the parts above zero, in the row's currency; null when neither is. */
export function deductionLine(d: Dividend): string | null {
  const amount = (value: number) => (isTwdCurrency(d.currency) ? groupedInteger(value) : formatNativeAmount(value, d.currency));
  const fee = Number(d.fee || 0);
  const tax = Number(d.tax || 0);
  const parts: string[] = [];
  if (fee > 0) parts.push(`匯費 ${amount(fee)}`);
  if (tax > 0) parts.push(`補充保費 ${amount(tax)}`);
  return parts.length ? parts.join(' · ') : null;
}

/** One list row; `name` is the page's display name for the symbol, `accountName` resolves the receiving account. */
export function dividendRow(d: Dividend, name: string | null, accountName: (id: number) => string | null): DividendRowView {
  const status = dividendStatus(d);
  const net = netAmountLines(d);
  const exDate = exDateLabel(d.ex_dividend_date);
  const pay = payLine(d, accountName);
  return {
    id: d.id,
    status,
    square: stockSquare(d.symbol, name),
    primary: name ? `${name} ${d.symbol}` : d.symbol,
    exDate,
    dateLine: pay.main === '—' ? `除息 ${exDate}` : `除息 ${exDate} · ${pay.main}`,
    pay,
    perShare: perShareLine(d),
    deductions: deductionLine(d),
    net: { main: net.main, sub: net.sub },
    netClass: status === 'pending' ? 'primary' : 'dividend',
    tag: { text: statusTag(status), variant: statusTagVariant(status) },
    reviewReason: d.review_reason || null,
    sourceCorrected: !!d.source_correction,
  };
}
