import { shortDate, slashDate } from '../../accounting/dates';
import { EinvoiceTarget, InboxEinvoice } from '../../accounting/inbox/einvoice.model';
import { invoiceAmount } from '../../accounting/inbox/einvoice-format';

/** One answer button: 「連結 <name>」, account · date · category, the amount and what differs from the invoice. */
export interface EinvoiceAnswer {
  readonly target: EinvoiceTarget;
  readonly label: string;
  readonly sub: string;
  readonly amount: string;
  readonly chips: readonly string[];
}

/** The invoice card (D10 item 2). */
export interface EinvoiceCard {
  readonly seller: string;
  readonly date: string;
  readonly number: string;
  readonly taxId: string;
  readonly items: readonly { name: string; subtotal: string }[];
  readonly total: string;
}

export interface EinvoiceStep {
  readonly chip: string;
  readonly reason: string;
  readonly card: EinvoiceCard;
  readonly answers: readonly EinvoiceAnswer[];
}

/** The highest digit key: 1–9 are the answer keys. */
const MAX_DIGIT = 9;

/** The matcher's near tolerance (`einvoice_rules.near_tolerance`): max($50, 20 % of the invoice total). */
function nearTolerance(total: number): number {
  return Math.max(50, total * 0.2);
}

function daysApart(a: string, b: string): number {
  return Math.abs(Date.UTC(+a.slice(0, 4), +a.slice(5, 7) - 1, +a.slice(8, 10)) -
    Date.UTC(+b.slice(0, 4), +b.slice(5, 7) - 1, +b.slice(8, 10))) / 86_400_000;
}

/** |candidate| − invoice total, as a positive number. */
function amountGap(target: EinvoiceTarget, total: number): number {
  return Math.abs(Math.abs(Number(target.amount)) - total);
}

function reason(invoice: InboxEinvoice): string {
  const total = Math.abs(Number(invoice.total_amount));
  const n = invoice.candidates.length;
  if (invoice.state === 'near') {
    const first = invoice.candidates[0];
    return first && amountGap(first, total) <= nearTolerance(total)
      ? '沒有金額完全相同的帳，這筆最接近'
      : '商家名稱對得上，金額不同';
  }
  const sameDay = invoice.candidates.every((c) => c.date === invoice.issue_date);
  return sameDay ? `同一天、同金額的帳有 ${n} 筆` : `金額相同的帳有 ${n} 筆`;
}

function answer(invoice: InboxEinvoice, target: EinvoiceTarget): EinvoiceAnswer {
  const total = Math.abs(Number(invoice.total_amount));
  const days = daysApart(target.date, invoice.issue_date);
  const gap = amountGap(target, total);
  const chips = [
    ...(days > 0 ? [`日期差 ${days} 天`] : []),
    ...(gap > 0 ? [`金額差 ${invoiceAmount(String(gap))}`] : []),
  ];
  return {
    target,
    label: `連結 ${target.name ?? target.category ?? '這筆帳'}`,
    sub: [target.account_name, shortDate(target.date), target.category].filter(Boolean).join(' · '),
    amount: invoiceAmount(String(Math.abs(Number(target.amount)))),
    chips,
  };
}

/** What the flow shows for one ambiguous / near invoice (D10). */
export function einvoiceStep(invoice: InboxEinvoice): EinvoiceStep {
  return {
    chip: invoice.state === 'near' ? '差一點' : '有兩筆都像',
    reason: reason(invoice),
    card: {
      seller: invoice.seller_name,
      date: slashDate(invoice.issue_date),
      number: invoice.number,
      taxId: invoice.seller_tax_id || '沒有統編',
      items: invoice.items.map((item) => ({ name: item.name, subtotal: invoiceAmount(item.subtotal) })),
      total: invoiceAmount(invoice.total_amount),
    },
    answers: invoice.candidates.map((target) => answer(invoice, target)),
  };
}

/** Digit keys: candidate i gets i while i ≤ 9; 都不是 gets N + 1 when that is ≤ 9, else no key. */
export function answerKeys(candidates: number): { candidates: (number | null)[]; dismiss: number | null } {
  return {
    candidates: Array.from({ length: candidates }, (_, i) => (i + 1 <= MAX_DIGIT ? i + 1 : null)),
    dismiss: candidates + 1 <= MAX_DIGIT ? candidates + 1 : null,
  };
}
