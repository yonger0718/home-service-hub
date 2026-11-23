import { describe, expect, it } from 'vitest';

import { formatMoney } from '../../accounting/format';
import { questionText } from '../../accounting/inbox/inbox-view';
import { InboxCandidate, InboxEntry } from '../../accounting/inbox/inbox.model';
import { makeInboxAccount, makeItem } from '../../accounting/inbox/testing';
import { compareRows, flowCard, flowChip, flowEntry, flowHeadline, flowLearnable, flowNote } from './recon-flow-view';

const entry = (over: Partial<InboxEntry> = {}): InboxEntry => ({
  id: 7,
  date: '2026-09-20',
  title: '星巴克',
  amount: '-2066.0000',
  account_id: 1,
  ...over,
});
const line = (date: string, amount: string, merchant = '星巴克') => ({
  id: 5,
  date,
  merchant,
  amount,
  line_kind: 'purchase',
});
const candidate = (over: Partial<InboxCandidate> = {}): InboxCandidate => ({
  entry_id: 9,
  date: '2026-09-20',
  title: '星巴克',
  amount: '-2066.0000',
  reason: null,
  residual: null,
  date_distance: null,
  ...over,
});

describe('recon-flow-view', () => {
  it('bank_only chip and headline', () => {
    const item = makeItem();
    expect(flowChip('bank_only')).toBe('銀行有，你沒記');
    expect(flowHeadline(item, 'TWD')).toBe(`全家便利商店 ${formatMoney('-120', 'TWD', { sign: true })}`);
  });

  it('ledger_only chip and headline', () => {
    const item = makeItem({ question: 'ledger_only', line: null, entry: entry() });
    expect(flowChip('ledger_only')).toBe('你記了，銀行沒有');
    expect(flowHeadline(item, 'TWD')).toBe(`星巴克 ${formatMoney('-2066', 'TWD', { sign: true })}`);
  });

  it('amount_differs chip and headline, expense', () => {
    const item = makeItem({ question: 'amount_differs', line: line('2026-09-20', '-2096'), entry: entry() });
    expect(flowChip('amount_differs')).toBe('金額不同');
    expect(flowHeadline(item, 'TWD')).toBe(`銀行多扣了 ${formatMoney(30, 'TWD')}`);
    const less = makeItem({ question: 'amount_differs', line: line('2026-09-20', '-2000'), entry: entry() });
    expect(flowHeadline(less, 'TWD')).toBe(`銀行少扣了 ${formatMoney(66, 'TWD')}`);
  });

  it('amount_differs headline, income', () => {
    const more = makeItem({
      question: 'amount_differs',
      line: line('2026-09-20', '500'),
      entry: entry({ amount: '480' }),
    });
    expect(flowHeadline(more, 'TWD')).toBe(`銀行多入了 ${formatMoney(20, 'TWD')}`);
    const less = makeItem({
      question: 'amount_differs',
      line: line('2026-09-20', '500'),
      entry: entry({ amount: '520' }),
    });
    expect(flowHeadline(less, 'TWD')).toBe(`銀行少入了 ${formatMoney(20, 'TWD')}`);
  });

  it('date_differs chip and headline', () => {
    expect(flowChip('date_differs')).toBe('日期不同');
    const late = makeItem({ question: 'date_differs', line: line('2026-09-28', '-2066'), entry: entry() });
    expect(flowHeadline(late, 'TWD')).toBe('同一筆，晚了 8 天入帳');
    const early = makeItem({ question: 'date_differs', line: line('2026-09-18', '-2066'), entry: entry() });
    expect(flowHeadline(early, 'TWD')).toBe('同一筆，早了 2 天入帳');
    const missing = makeItem({ question: 'date_differs', line: null, entry: entry() });
    expect(flowHeadline(missing, 'TWD')).toBe('同一筆，日期不同');
  });

  it('transfer and ambiguous', () => {
    expect(flowChip('transfer')).toBe('轉帳／繳費');
    expect(flowHeadline(makeItem({ question: 'transfer' }), 'TWD')).toBe('轉帳或繳費，你沒記');
    expect(flowChip('ambiguous')).toBe('多筆可能');
    const item = makeItem({ question: 'ambiguous', candidates: [candidate(), candidate({ entry_id: 10 })] });
    expect(flowHeadline(item, 'TWD')).toBe('有 2 筆可能是它');
  });

  it('flowEntry falls back to the first candidate for amount_differs', () => {
    const item = makeItem({ question: 'amount_differs', candidates: [candidate()] });
    expect(flowEntry(item)).toEqual({ id: 9, date: '2026-09-20', title: '星巴克', amount: '-2066.0000' });
    expect(flowEntry(makeItem())).toBeNull();
    expect(flowEntry(makeItem({ question: 'amount_differs', entry: entry() }))?.id).toBe(7);
  });

  it('compareRows for bank_only has the ledger side missing', () => {
    const rows = compareRows(makeItem(), 'TWD');
    expect(rows.map((r) => r.label)).toEqual(['日期', '商家', '金額']);
    expect(rows.every((r) => r.ledgerMissing && !r.bankMissing && !r.differs)).toBe(true);
    expect(rows[1].ledger).toBe('沒有對應記錄');
    expect(rows[0].ledger).toBe('—');
    expect(rows[1].bank).toBe('全家便利商店');
    expect(rows[0].bank).toBe('2026/09/03');
  });

  it('compareRows marks only 金額 as differing for amount_differs', () => {
    const item = makeItem({ question: 'amount_differs', line: line('2026-09-20', '-2096'), entry: entry() });
    const rows = compareRows(item, 'TWD');
    expect(rows.map((r) => r.differs)).toEqual([false, false, true]);
    expect(rows[2].bank).toBe(formatMoney('-2096', 'TWD', { sign: true }));
    expect(rows[2].ledger).toBe(formatMoney('-2066', 'TWD', { sign: true }));
  });

  it('compareRows marks 日期 for date_differs and handles a missing bank side', () => {
    const item = makeItem({ question: 'date_differs', line: line('2026-09-28', '-2066'), entry: entry() });
    expect(compareRows(item, 'TWD').map((r) => r.differs)).toEqual([true, false, false]);
    const ledgerOnly = makeItem({ question: 'ledger_only', line: null, entry: entry() });
    const rows = compareRows(ledgerOnly, 'TWD');
    expect(rows.every((r) => r.bankMissing && !r.ledgerMissing && !r.differs)).toBe(true);
    expect(rows[1].bank).toBe('沒有對應記錄');
  });

  it('flowNote', () => {
    const partOf = makeItem({ part_of: [entry({ amount: '-5000' })] });
    expect(flowNote(partOf, 'TWD')).toBe(questionText(partOf, 'TWD'));
    expect(flowNote(makeItem({ question: 'transfer' }), 'TWD')).toBe('在時間軸記一筆轉帳，或對到已記的。');
    expect(flowNote(makeItem({ note: '提早入帳' }), 'TWD')).toBe('提早入帳');
    expect(flowNote(makeItem(), 'TWD')).toBeNull();
  });

  it('flowLearnable and flowCard', () => {
    const plain = makeItem({ rule_hint: null });
    expect(flowLearnable(plain)).toBe(false);
    expect(flowLearnable(makeItem())).toBe(true);
    expect(flowLearnable(makeItem({ actions: ['defer'] }))).toBe(false);
    const account = makeInboxAccount();
    const card = flowCard(plain, account);
    expect(card.items).toEqual([plain]);
    expect(card.learnable).toBe(false);
    expect(card.key).toBe('1');
    expect(card.account).toBe(account);
    expect(flowCard(makeItem(), account).learnable).toBe(true);
  });
});
