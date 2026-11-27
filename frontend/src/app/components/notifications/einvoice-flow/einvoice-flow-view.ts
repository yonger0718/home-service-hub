import { slashDate } from '../../accounting/dates';
import { EinvoiceAnswer, amountGap, einvoiceAnswer } from '../../accounting/inbox/einvoice-answer';
import { InboxEinvoice } from '../../accounting/inbox/einvoice.model';
import { invoiceAmount } from '../../accounting/inbox/einvoice-format';

/** One answer button: 「連結 <name>」 and the shared answer parts. */
export interface EinvoiceFlowAnswer extends EinvoiceAnswer {
  readonly label: string;
}

/** The invoice card (D10 item 2). */
export interface EinvoiceCard {
  readonly seller: string;
  readonly date: string;
  readonly number: string;
  /** 「統編 12345678」 or 「沒有統編」. */
  readonly taxId: string;
  readonly items: readonly { name: string; subtotal: string }[];
  readonly total: string;
}

export interface EinvoiceStep {
  readonly chip: string;
  readonly reason: string;
  readonly card: EinvoiceCard;
  readonly answers: readonly EinvoiceFlowAnswer[];
  /** The first answer is the solid default, except for a near invoice beyond the close threshold. */
  readonly solidFirst: boolean;
}

/** The highest digit key: 1–9 are the answer keys. */
const MAX_DIGIT = 9;

/** A near candidate counts as close when it is within $50 AND within 20 % of the invoice total (owner, 2026-11-27). */
const CLOSE_GAP = 50;
const CLOSE_SHARE = 0.2;

/** The first candidate of a near invoice is close (a near match by amount, not only by seller name). */
export function nearIsClose(invoice: InboxEinvoice): boolean {
  const first = invoice.candidates[0];
  const total = Math.abs(Number(invoice.total_amount));
  if (!first) return false;
  const gap = amountGap(invoice, first);
  return gap <= CLOSE_GAP && gap <= total * CLOSE_SHARE;
}

function reason(invoice: InboxEinvoice): string {
  if (invoice.state === 'near') {
    return nearIsClose(invoice) ? '沒有金額完全相同的帳，這筆最接近' : '商家名稱對得上，金額不同';
  }
  const n = invoice.candidates.length;
  const sameDay = invoice.candidates.every((c) => c.date === invoice.issue_date);
  return sameDay ? `同一天、同金額的帳有 ${n} 筆` : `金額相同的帳有 ${n} 筆`;
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
      taxId: invoice.seller_tax_id ? `統編 ${invoice.seller_tax_id}` : '沒有統編',
      items: invoice.items.map((item) => ({ name: item.name, subtotal: invoiceAmount(item.subtotal) })),
      total: invoiceAmount(invoice.total_amount),
    },
    answers: invoice.candidates.map((target) => {
      const a = einvoiceAnswer(invoice, target);
      return { ...a, label: `連結 ${a.name}` };
    }),
    solidFirst: invoice.state !== 'near' || nearIsClose(invoice),
  };
}

/** Digit keys: candidate i gets i while i ≤ 9; 都不是 gets N + 1 when that is ≤ 9, else no key. */
export function answerKeys(candidates: number): { candidates: (number | null)[]; dismiss: number | null } {
  return {
    candidates: Array.from({ length: candidates }, (_, i) => (i + 1 <= MAX_DIGIT ? i + 1 : null)),
    dismiss: candidates + 1 <= MAX_DIGIT ? candidates + 1 : null,
  };
}
