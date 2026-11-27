import { shortDate } from '../dates';
import { invoiceAmount } from './einvoice-format';
import { EinvoiceSummary, EinvoiceTarget } from './einvoice.model';

/**
 * One entry (or split group) offered for an invoice, as the e-invoice flow's answer buttons and the 選其他帳… sheet
 * both show it: 「<name>」, account · date · category, the amount without a sign, and what differs from the invoice
 * (日期差 N 天, 金額差 $N).
 */
export interface EinvoiceAnswer {
  readonly target: EinvoiceTarget;
  readonly name: string;
  readonly sub: string;
  readonly amount: string;
  readonly chips: readonly string[];
}

/** Calendar days between two ISO dates. */
export function daysApart(a: string, b: string): number {
  const day = (iso: string) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
  return Math.abs(day(a) - day(b)) / 86_400_000;
}

/** ||target| − invoice total|. */
export function amountGap(
  invoice: Pick<EinvoiceSummary, 'total_amount'>,
  target: EinvoiceTarget,
): number {
  return Math.abs(Math.abs(Number(target.amount)) - Math.abs(Number(invoice.total_amount)));
}

export function einvoiceAnswer(
  invoice: Pick<EinvoiceSummary, 'issue_date' | 'total_amount'>,
  target: EinvoiceTarget,
): EinvoiceAnswer {
  const days = daysApart(target.date, invoice.issue_date);
  const gap = amountGap(invoice, target);
  return {
    target,
    name: target.name ?? target.category ?? '這筆帳',
    sub: [target.account_name, shortDate(target.date), target.category].filter(Boolean).join(' · '),
    amount: invoiceAmount(String(Math.abs(Number(target.amount)))),
    chips: [
      ...(days > 0 ? [`日期差 ${days} 天`] : []),
      ...(gap > 0 ? [`金額差 ${invoiceAmount(String(gap))}`] : []),
    ],
  };
}
