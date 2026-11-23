import { daysBetween, slashDate } from '../../accounting/dates';
import { formatMoney } from '../../accounting/format';
import { InboxCard, learnableAnswer, questionText } from '../../accounting/inbox/inbox-view';
import { InboxAccount, InboxItem, InboxQuestion } from '../../accounting/inbox/inbox.model';

const CHIPS: Partial<Record<InboxQuestion, string>> = {
  bank_only: '銀行有，你沒記',
  ledger_only: '你記了，銀行沒有',
  amount_differs: '金額不同',
  date_differs: '日期不同',
  transfer: '轉帳／繳費',
  ambiguous: '多筆可能',
};

export function flowChip(question: InboxQuestion): string {
  return CHIPS[question] ?? '';
}

export interface FlowEntry {
  id: number;
  date: string;
  title: string;
  amount: string;
}

/** The ledger entry a step compares against: `item.entry`, else the first candidate for amount_differs / date_differs. */
export function flowEntry(item: InboxItem): FlowEntry | null {
  if (item.entry) {
    const { id, date, title, amount } = item.entry;
    return { id, date, title, amount };
  }
  if (item.question === 'amount_differs' || item.question === 'date_differs') {
    const first = item.candidates[0];
    if (first) {
      return { id: first.entry_id, date: first.date, title: first.title, amount: first.amount };
    }
  }
  return null;
}

export function flowHeadline(item: InboxItem, currency: string): string {
  switch (item.question) {
    case 'bank_only':
      return `${item.line?.merchant ?? ''} ${formatMoney(item.line?.amount, currency, { sign: true })}`;
    case 'ledger_only': {
      const entry = flowEntry(item);
      if (!entry) return questionText(item, currency);
      return `${entry.title} ${formatMoney(entry.amount, currency, { sign: true })}`;
    }
    case 'amount_differs': {
      const ledger = flowEntry(item);
      // Without both sides, or with no real difference, there is no sign to read and no amount to name.
      if (!item.line || !ledger || Number(item.line.amount) === Number(ledger.amount)) return '金額不同';
      const bank = Number(item.line.amount);
      const entry = Number(ledger.amount);
      const diff = formatMoney(Math.abs(bank - entry), currency);
      const more = Math.abs(bank) > Math.abs(entry);
      if (bank < 0) return `銀行${more ? '多' : '少'}扣了 ${diff}`;
      return `銀行${more ? '多' : '少'}入了 ${diff}`;
    }
    case 'date_differs': {
      const entry = flowEntry(item);
      if (!item.line || !entry) return '同一筆，日期不同';
      const days = daysBetween(entry.date.slice(0, 10), item.line.date.slice(0, 10));
      if (days > 0) return `同一筆，晚了 ${days} 天入帳`;
      if (days < 0) return `同一筆，早了 ${-days} 天入帳`;
      return '同一筆，日期不同';
    }
    case 'transfer':
      return '轉帳或繳費，你沒記';
    case 'ambiguous':
      return `有 ${item.candidates.length} 筆可能是它`;
    default:
      return questionText(item, currency);
  }
}

/** Extra line under the grid: the part-of warning, the transfer hint, or the item's note; null when none. */
export function flowNote(item: InboxItem, currency: string): string | null {
  if (item.question === 'bank_only' && (item.part_of ?? []).length > 0) {
    return questionText(item, currency);
  }
  if (item.question === 'transfer') return '在時間軸記一筆轉帳，或對到已記的。';
  return item.note || null;
}

export interface CompareRow {
  readonly label: '日期' | '商家' | '金額';
  readonly bank: string;
  readonly ledger: string;
  readonly differs: boolean;
  readonly bankMissing: boolean;
  readonly ledgerMissing: boolean;
}

export function compareRows(item: InboxItem, currency: string): readonly CompareRow[] {
  const line = item.line;
  const entry = flowEntry(item);
  const both = line !== null && entry !== null;
  const side = (present: boolean, text: string, label: CompareRow['label']): string =>
    present ? text : label === '商家' ? '沒有對應記錄' : '—';
  const row = (label: CompareRow['label'], bank: string, ledger: string, differs: boolean): CompareRow => ({
    label,
    bank: side(line !== null, bank, label),
    ledger: side(entry !== null, ledger, label),
    differs: both && differs,
    bankMissing: line === null,
    ledgerMissing: entry === null,
  });
  return [
    row(
      '日期',
      line ? slashDate(line.date) : '',
      entry ? slashDate(entry.date) : '',
      both && line.date.slice(0, 10) !== entry.date.slice(0, 10),
    ),
    row('商家', line?.merchant ?? '', entry?.title ?? '', false),
    row(
      '金額',
      line ? formatMoney(line.amount, currency, { sign: true }) : '',
      entry ? formatMoney(entry.amount, currency, { sign: true }) : '',
      both && Number(line.amount) !== Number(entry.amount),
    ),
  ];
}

export function flowLearnable(item: InboxItem): boolean {
  return item.rule_hint !== null && item.actions.some((a) => learnableAnswer(item.question, a));
}

/** One-item card for the match and record sheets. */
export function flowCard(item: InboxItem, account: InboxAccount): InboxCard {
  return {
    key: String(item.case_id),
    question: item.question,
    account,
    items: [item],
    actions: item.actions,
    rule_hint: item.rule_hint,
    learnable: flowLearnable(item),
  };
}
