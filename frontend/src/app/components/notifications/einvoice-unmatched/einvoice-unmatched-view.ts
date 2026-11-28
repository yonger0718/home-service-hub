import { shortDate } from '../../accounting/dates';
import { invoiceAmount } from '../../accounting/inbox/einvoice-format';
import { InboxEinvoice } from '../../accounting/inbox/einvoice.model';

/** One invoice row: date, items and amount (D11). */
export interface UnmatchedRow {
  readonly invoice: InboxEinvoice;
  readonly date: string;
  readonly items: string;
  readonly amount: string;
}

/** One seller: header (seller, meta, total), rows, and the group actions it offers. */
export interface UnmatchedGroup {
  readonly key: string;
  readonly seller: string;
  readonly taxId: string | null;
  readonly meta: string;
  readonly total: string;
  readonly rows: readonly UnmatchedRow[];
  /** 這家以後都不提醒: a tax id and an invoice carrying `offer_mute`. */
  readonly canMute: boolean;
}

/** 「a、b · N 項」: the first two item names and the item count; '' without items. */
export function itemsText(invoice: InboxEinvoice): string {
  const n = invoice.items.length;
  if (n === 0) return '';
  return `${invoice.items.slice(0, 2).map((item) => item.name).join('、')} · ${n} 項`;
}

function sum(rows: readonly InboxEinvoice[]): number {
  return rows.reduce((total, invoice) => total + Number(invoice.total_amount), 0);
}

/**
 * The unmatched invoices grouped by seller (tax id, else name): groups by count, then total, then seller; rows newest
 * first. The header meta reads 「N 張 · 統編 X」 or 「N 張 · 沒有統編」, plus 「 · 已標不用記帳 K 次」 when the owner
 * marked this seller 不用記帳 before (K = `seller_dismissed`).
 */
export function unmatchedGroups(invoices: readonly InboxEinvoice[]): UnmatchedGroup[] {
  const bySeller = new Map<string, InboxEinvoice[]>();
  for (const invoice of invoices) {
    if (invoice.state !== 'unmatched') continue;
    const key = invoice.seller_tax_id ? `tax:${invoice.seller_tax_id}` : `name:${invoice.seller_name}`;
    bySeller.set(key, [...(bySeller.get(key) ?? []), invoice]);
  }
  const groups = [...bySeller.entries()].map(([key, list]) => {
    const rows = [...list].sort((a, b) => b.issue_date.localeCompare(a.issue_date) || b.id - a.id);
    const taxId = rows[0].seller_tax_id || null;
    const dismissed = Math.max(0, ...rows.map((invoice) => invoice.seller_dismissed ?? 0));
    const meta = [
      `${rows.length} 張`,
      taxId ? `統編 ${taxId}` : '沒有統編',
      ...(dismissed > 0 ? [`已標不用記帳 ${dismissed} 次`] : []),
    ].join(' · ');
    return {
      group: {
        key,
        seller: rows[0].seller_name,
        taxId,
        meta,
        total: invoiceAmount(String(sum(rows))),
        rows: rows.map((invoice) => ({
          invoice,
          date: shortDate(invoice.issue_date),
          items: itemsText(invoice),
          amount: invoiceAmount(invoice.total_amount),
        })),
        canMute: taxId !== null && rows.some((invoice) => invoice.offer_mute),
      },
      total: sum(rows),
    };
  });
  return groups
    .sort((a, b) => b.group.rows.length - a.group.rows.length || b.total - a.total ||
      a.group.seller.localeCompare(b.group.seller))
    .map(({ group }) => group);
}

/** 「N 張 · M 家 · 不計入通知數字」 over what is shown. */
export function unmatchedHeadline(groups: readonly UnmatchedGroup[]): string {
  const n = groups.reduce((count, group) => count + group.rows.length, 0);
  return `${n} 張 · ${groups.length} 家 · 不計入通知數字`;
}
