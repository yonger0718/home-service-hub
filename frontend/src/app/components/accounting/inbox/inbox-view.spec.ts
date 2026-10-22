import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';

import {
  AMOUNT_ANY_LABEL,
  buildCards,
  buttonLabel,
  cardTitle,
  inboxErrorMessage,
  learnLabel,
  lineKind,
  matchAnswer,
  questionText,
  recordAnswer,
} from './inbox-view';
import { InboxAccount, InboxItem } from './inbox.model';

function makeItem(overrides: Partial<InboxItem> = {}): InboxItem {
  return {
    case_id: 1,
    case_version: 1,
    kind: 'line_unmatched',
    status: 'open',
    question: 'bank_only',
    folded: false,
    group_key: null,
    statement: { id: 10, period_end: '2026-09-30' },
    line: { id: 5, date: '2026-09-03', merchant: '全家便利商店', amount: '-120.0000', line_kind: 'purchase' },
    entry: null,
    candidates: [],
    proposal: null,
    actions: ['record', 'defer', 'dismiss'],
    rule_hint: { merchant_pattern: '全家便利商店', line_kind: 'purchase', flow_sign: '-', amount: '120.0000', default_category_id: null },
    ...overrides,
  };
}

function makeAccount(items: InboxItem[]): InboxAccount {
  return { id: 1, name: '玉山', currency: 'TWD', is_credit: false, items };
}

const ENTRY = { id: 3, date: '2026-09-02', title: '午餐', amount: '-100.0000', account_id: 1 };

describe('buildCards', () => {
  it('merges unfolded items by group_key, keeps order and buckets by question', () => {
    const a = makeItem({ case_id: 1, group_key: 'g1' });
    const b = makeItem({ case_id: 2, group_key: 'g1' });
    const c = makeItem({ case_id: 3 });
    const d = makeItem({ case_id: 4, group_key: 'g1', folded: true });
    const e = makeItem({ case_id: 5, question: 'proposed' });
    const f = makeItem({ case_id: 6, question: 'balance_gap' });
    const g = makeItem({ case_id: 7, question: 'settlement' });
    const h = makeItem({ case_id: 8, question: 'other' });
    const cards = buildCards([makeAccount([a, b, c, d, e, f, g, h])]);
    expect(cards.ask.map((card) => card.key)).toEqual(['g1', 'case:3', 'case:4']);
    expect(cards.ask[0].items.map((item) => item.case_id)).toEqual([1, 2]);
    expect(cards.ask[0].actions).toEqual(['record', 'defer', 'dismiss']);
    expect(cards.ask[0].learnable).toBe(true);
    expect(cards.proposed.map((card) => card.key)).toEqual(['case:5']);
    expect(cards.balanceGap.map((card) => card.key)).toEqual(['case:6']);
    expect(cards.settlement.map((card) => card.key)).toEqual(['case:7']);
    expect(cards.other.map((card) => card.key)).toEqual(['case:8']);
  });

  it('is learnable for accept_bank_amount on amount_differs, not without a hint', () => {
    const base = { question: 'amount_differs' as const, entry: ENTRY, actions: ['accept_bank_amount' as const, 'dismiss' as const] };
    expect(buildCards([makeAccount([makeItem({ ...base, actions: ['accept_bank_amount'] })])]).ask[0].learnable).toBe(true);
    expect(buildCards([makeAccount([makeItem({ ...base, actions: ['accept_bank_amount'], rule_hint: null })])]).ask[0].learnable).toBe(false);
  });

  it('is not learnable without a rule hint', () => {
    expect(buildCards([makeAccount([makeItem({ rule_hint: null })])]).ask[0].learnable).toBe(false);
  });
});

describe('questionText', () => {
  it('words every question', () => {
    expect(questionText(makeItem(), 'TWD')).toBe('銀行有、你沒記');
    expect(questionText(makeItem({ question: 'transfer' }), 'TWD')).toBe('轉帳／繳費，你沒記 — 在時間軸記一筆轉帳，或對到已記的');
    expect(questionText(makeItem({ question: 'ledger_only' }), 'TWD')).toBe('你記了，銀行沒有');
    expect(questionText(makeItem({ question: 'amount_differs', entry: ENTRY }), 'TWD')).toBe('銀行 −$120，你記 −$100（差 $20）');
    expect(
      questionText(
        makeItem({
          question: 'amount_differs',
          candidates: [{ entry_id: 3, date: '2026-09-02', title: '午餐', amount: '-100.0000', reason: null, residual: null, date_distance: 1 }],
        }),
        'TWD',
      ),
    ).toBe('銀行 −$120，你記 −$100（差 $20）');
    expect(questionText(makeItem({ question: 'date_differs', entry: ENTRY }), 'TWD')).toBe('你記 9/2，銀行 9/3 扣');
    expect(
      questionText(makeItem({ question: 'ambiguous', candidates: [1, 2, 3].map((id) => ({ entry_id: id, date: '2026-09-02', title: 't', amount: '-1', reason: null, residual: null, date_distance: 0 })) }), 'TWD'),
    ).toBe('有 3 筆可能是它');
    expect(questionText(makeItem({ question: 'other', case_id: 42 }), 'TWD')).toBe('需要在 API 處理（案件 #42）');
    expect(questionText(makeItem({ question: 'balance_gap' }), 'TWD')).toBe('期初差額');
    expect(questionText(makeItem({ question: 'settlement' }), 'TWD')).toBe('交割款，等證券連動');
    expect(
      questionText(makeItem({ question: 'proposed', proposal: { id: 1, action: 'match', params: {}, author: 'x', rationale: '' } }), 'TWD'),
    ).toBe('系統有提議：match');
  });
});

describe('cardTitle', () => {
  it('titles line cards, entry cards and merged cards', () => {
    const [line] = buildCards([makeAccount([makeItem()])]).ask;
    expect(cardTitle(line, 'TWD')).toBe('2026/09/03 全家便利商店 −$120');
    const [entry] = buildCards([makeAccount([makeItem({ question: 'ledger_only', line: null, entry: { ...ENTRY, amount: '100.0000' } })])]).ask;
    expect(cardTitle(entry, 'TWD')).toBe('2026/09/02 午餐 +$100');
    const [merged] = buildCards([makeAccount([makeItem({ group_key: 'g' }), makeItem({ case_id: 2, group_key: 'g' })])]).ask;
    expect(cardTitle(merged, 'TWD')).toBe('2026/09/03 全家便利商店 −$120 ×2 筆');
  });
});

describe('buttonLabel', () => {
  it('labels every button', () => {
    const expense = makeItem();
    const income = makeItem({ line: { id: 6, date: '2026-09-04', merchant: '薪資', amount: '500.0000', line_kind: 'deposit' } });
    expect(buttonLabel('record', expense, 'TWD')).toBe('記為支出…');
    expect(buttonLabel('record', income, 'TWD')).toBe('記為收入…');
    expect(buttonLabel('match_candidate', makeItem({ question: 'date_differs' }), 'TWD')).toBe('對到 9/3 那行');
    expect(buttonLabel('match_candidate', expense, 'TWD')).toBe('已記在別處，對到…');
    expect(buttonLabel('accept_bank_amount', income, 'TWD')).toBe('改成銀行的 +$500');
    expect(buttonLabel('defer', expense, 'TWD')).toBe('等下期');
    expect(buttonLabel('dismiss', makeItem({ question: 'ambiguous' }), 'TWD')).toBe('不是同一筆');
    expect(buttonLabel('dismiss', expense, 'TWD')).toBe('忽略');
    expect(buttonLabel('anchor', expense, 'TWD')).toBe('對齊銀行期初');
    expect(buttonLabel('apply_proposal', expense, 'TWD')).toBe('套用');
    expect(buttonLabel('reject_proposal', expense, 'TWD')).toBe('退回');
  });
});

describe('answers and learn labels', () => {
  it('builds the stored answer text', () => {
    const hint = makeItem().rule_hint!;
    expect(learnLabel(hint, 'record')).toBe('以後「全家便利商店」都這樣');
    expect(AMOUNT_ANY_LABEL).toBe('金額不同也算');
    expect(recordAnswer('expense', '餐飲')).toBe('記為支出 餐飲');
    expect(recordAnswer('income', '薪資')).toBe('記為收入 薪資');
    expect(matchAnswer('午餐')).toBe('對到 午餐');
    expect(lineKind(makeItem())).toBe('expense');
    expect(lineKind(makeItem({ line: { id: 6, date: '2026-09-04', merchant: 'x', amount: '5', line_kind: 'deposit' } }))).toBe('income');
  });
});

describe('inboxErrorMessage', () => {
  it('maps a string code, with a fallback for unknown codes', () => {
    expect(inboxErrorMessage('case_closed')).toBe('已經處理過了');
    expect(inboxErrorMessage('weird')).toBe('沒成功：weird');
  });

  it('maps a 409 business code', () => {
    const err = new HttpErrorResponse({ status: 409, error: { detail: 'stale_case: case 1 is at version 3' } });
    expect(inboxErrorMessage(err)).toBe('對象已變，重新整理');
  });

  it('maps a 422 detail message', () => {
    const err = new HttpErrorResponse({ status: 422, error: { detail: [{ msg: 'rule_pattern_mismatch' }] } });
    expect(inboxErrorMessage(err)).toBe('這幾筆描述不同，不能一起學');
  });
});
