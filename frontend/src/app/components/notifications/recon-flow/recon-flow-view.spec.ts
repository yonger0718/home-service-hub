import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';

import { formatMoney } from '../../accounting/format';
import { questionText } from '../../accounting/inbox/inbox-view';
import { InboxCandidate, InboxEntry, InboxPeerCandidate, InboxTransfer } from '../../accounting/inbox/inbox.model';
import { makeInboxAccount, makeItem as baseItem } from '../../accounting/inbox/testing';
import { InboxItem } from '../../accounting/inbox/inbox.model';
import {
  compareRows,
  adjustEntries,
  flowActions,
  flowAnswerLabel,
  flowCard,
  flowChip,
  flowEntry,
  flowError,
  flowExcluded,
  flowHeadline,
  flowLearnScope,
  flowLearnable,
  flowNote,
  flowPartOf,
  isCaseAction,
  transferLegs,
} from './recon-flow-view';

const entry = (over: Partial<InboxEntry> = {}): InboxEntry => ({
  id: 7,
  date: '2026-09-20',
  title: '測試咖啡',
  amount: '-2066.0000',
  account_id: 1,
  ...over,
});
const line = (date: string, amount: string, merchant = '測試咖啡') => ({
  id: 5,
  date,
  merchant,
  amount,
  line_kind: 'purchase',
});
/** `makeItem` with a fictional merchant (its default is a real store name). */
const makeItem = (over: Partial<InboxItem> = {}): InboxItem =>
  baseItem({
    line: { id: 5, date: '2026-09-03', merchant: '測試便利店', amount: '-120.0000', line_kind: 'purchase' },
    rule_hint: {
      merchant_pattern: '測試便利店',
      line_kind: 'purchase',
      flow_sign: '-',
      amount: '120.0000',
      default_category_id: null,
    },
    ...over,
  });
const candidate = (over: Partial<InboxCandidate> = {}): InboxCandidate => ({
  entry_id: 9,
  date: '2026-09-20',
  title: '測試咖啡',
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
    expect(flowHeadline(item, 'TWD')).toBe(`測試便利店 ${formatMoney('-120', 'TWD', { sign: true })}`);
  });

  it('ledger_only chip and headline', () => {
    const item = makeItem({ question: 'ledger_only', line: null, entry: entry() });
    expect(flowChip('ledger_only')).toBe('你記了，銀行沒有');
    expect(flowHeadline(item, 'TWD')).toBe(`測試咖啡 ${formatMoney('-2066', 'TWD', { sign: true })}`);
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

  it('amount_differs without a line, without an entry, or with equal amounts: 金額不同', () => {
    const noLine = makeItem({ question: 'amount_differs', line: null, entry: entry() });
    expect(flowHeadline(noLine, 'TWD')).toBe('金額不同');
    const noEntry = makeItem({ question: 'amount_differs', line: line('2026-09-20', '-2096'), entry: null });
    expect(flowHeadline(noEntry, 'TWD')).toBe('金額不同');
    const equal = makeItem({
      question: 'amount_differs',
      line: line('2026-09-20', '-2066.00'),
      entry: entry({ amount: '-2066.0000' }),
    });
    expect(flowHeadline(equal, 'TWD')).toBe('金額不同');
  });

  it('date_differs: equal dates, entry only in candidates[0], no entry at all', () => {
    const same = makeItem({ question: 'date_differs', line: line('2026-09-20', '-2066'), entry: entry() });
    expect(flowHeadline(same, 'TWD')).toBe('同一筆，日期不同');
    const viaCandidate = makeItem({
      question: 'date_differs',
      line: line('2026-09-28', '-2066'),
      entry: null,
      candidates: [candidate()],
    });
    expect(flowHeadline(viaCandidate, 'TWD')).toBe('同一筆，晚了 8 天入帳');
    const none = makeItem({ question: 'date_differs', line: line('2026-09-28', '-2066'), entry: null });
    expect(flowHeadline(none, 'TWD')).toBe('同一筆，日期不同');
  });

  it('ledger_only without an entry falls back to the question text', () => {
    const item = makeItem({ question: 'ledger_only', line: null, entry: null });
    expect(flowHeadline(item, 'TWD')).toBe('你記了，銀行沒有');
  });

  it('headline money keeps its sign (literal)', () => {
    const item = makeItem({ question: 'ledger_only', line: null, entry: entry() });
    expect(flowHeadline(item, 'TWD')).toBe('測試咖啡 \u2212$2,066');
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
    expect(flowEntry(item)).toEqual({ id: 9, date: '2026-09-20', title: '測試咖啡', amount: '-2066.0000' });
    expect(flowEntry(makeItem())).toBeNull();
    expect(flowEntry(makeItem({ question: 'amount_differs', entry: entry() }))?.id).toBe(7);
  });

  it('compareRows for bank_only has the ledger side missing', () => {
    const rows = compareRows(makeItem(), 'TWD');
    expect(rows.map((r) => r.label)).toEqual(['日期', '商家', '金額']);
    expect(rows.every((r) => r.ledgerMissing && !r.bankMissing && !r.differs)).toBe(true);
    expect(rows[1].ledger).toBe('沒有對應記錄');
    expect(rows[0].ledger).toBe('—');
    expect(rows[1].bank).toBe('測試便利店');
    expect(rows[0].bank).toBe('2026/09/03');
  });

  it('compareRows marks only 金額 as differing for amount_differs', () => {
    const item = makeItem({ question: 'amount_differs', line: line('2026-09-20', '-2096'), entry: entry() });
    const rows = compareRows(item, 'TWD');
    expect(rows.map((r) => r.differs)).toEqual([false, false, true]);
    expect(rows[2].bank).toBe(formatMoney('-2096', 'TWD', { sign: true }));
    expect(rows[2].ledger).toBe(formatMoney('-2066', 'TWD', { sign: true }));
  });

  it('compareRows compares amounts as numbers (-2066.00 equals -2066.0000)', () => {
    const item = makeItem({
      question: 'amount_differs',
      line: line('2026-09-20', '-2066.00'),
      entry: entry({ amount: '-2066.0000' }),
    });
    expect(compareRows(item, 'TWD')[2].differs).toBe(false);
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
    expect(flowNote(makeItem({ question: 'transfer' }), 'TWD')).toBe(NO_PEER_HINT);
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

  const NO_PEER_HINT =
    '另一個帳戶還沒有對得上的那一筆（帳單可能還沒匯入）。已經記過的話選「已記在別處，對到…」；不然先略過，另一邊進來後再配。';
  const peer = (over: Partial<InboxPeerCandidate> = {}): InboxPeerCandidate => ({
    line_id: 31,
    statement_id: 12,
    account_name: '測試銀行',
    posted_date: '2026-09-04',
    amount: '1000.0000',
    ...over,
  });
  const transfer = (over: Partial<InboxTransfer> = {}): InboxTransfer => ({
    adjust_transfer: null,
    pair_transfer: null,
    ...over,
  });

  it('flowActions: the transfer and amount answers join the server order', () => {
    const leg = makeItem({
      question: 'amount_differs',
      actions: ['match_candidate', 'dismiss'],
      transfer: transfer({ adjust_transfer: { entry_id: 7, line_id: 5, candidate_entry_ids: [] } }),
    });
    expect(flowActions(leg)).toEqual(['adjust_transfer', 'match_candidate', 'dismiss']);

    const ambiguous = makeItem({
      question: 'ambiguous',
      actions: ['match_candidate', 'dismiss'],
      candidates: [
        candidate({ entry_id: 21 }),
        candidate({ entry_id: 22 }),
        candidate({ entry_id: 23 }),
      ],
      transfer: transfer({
        adjust_transfer: { entry_id: null, line_id: 5, candidate_entry_ids: [21, 22] },
      }),
    });
    expect(flowActions(ambiguous)).toEqual([
      'adjust_transfer',
      'match_candidate',
      'adjust_amount',
      'dismiss',
    ]);

    const paired = makeItem({
      question: 'transfer',
      actions: ['match_candidate', 'dismiss'],
      transfer: transfer({ pair_transfer: { peer_candidates: [peer()] } }),
    });
    expect(flowActions(paired)).toEqual(['pair_transfer', 'match_candidate', 'dismiss']);

    const lonely = makeItem({
      question: 'transfer',
      actions: ['match_candidate', 'dismiss'],
      transfer: transfer({ pair_transfer: { peer_candidates: [] } }),
    });
    expect(flowActions(lonely)).toEqual(['match_candidate', 'dismiss']);

    const allLegs = makeItem({
      question: 'ambiguous',
      actions: ['match_candidate', 'dismiss'],
      candidates: [candidate({ entry_id: 21 }), candidate({ entry_id: 22 })],
      transfer: transfer({
        adjust_transfer: { entry_id: null, line_id: 5, candidate_entry_ids: [21, 22] },
      }),
    });
    expect(flowActions(allLegs)).toEqual(['adjust_transfer', 'match_candidate', 'dismiss']);

    const old = makeItem({ question: 'transfer', actions: ['match_candidate', 'dismiss'] });
    expect(flowActions(old)).toBe(old.actions);
  });

  it('transferLegs and adjustEntries split the candidates', () => {
    const base = {
      question: 'ambiguous' as const,
      candidates: [
        candidate({ entry_id: 21 }),
        candidate({ entry_id: 22 }),
        candidate({ entry_id: 23 }),
      ],
    };
    const ambiguous = makeItem({
      ...base,
      transfer: transfer({
        adjust_transfer: { entry_id: null, line_id: 5, candidate_entry_ids: [21, 22] },
      }),
    });
    expect(transferLegs(ambiguous).map((c) => c.entry_id)).toEqual([21, 22]);
    expect(adjustEntries(ambiguous).map((c) => c.entry_id)).toEqual([23]);
    const known = makeItem({
      ...base,
      transfer: transfer({
        adjust_transfer: { entry_id: 21, line_id: 5, candidate_entry_ids: [] },
      }),
    });
    expect(transferLegs(known)).toEqual([]);
    expect(adjustEntries(makeItem({ ...base, question: 'amount_differs' }))).toEqual([]);
    expect(transferLegs(makeItem(base))).toEqual([]);
  });

  it('flowAnswerLabel: buttons as the inbox names them, case actions as the owner reads them', () => {
    const known = makeItem({
      transfer: transfer({ adjust_transfer: { entry_id: 7, line_id: 5, candidate_entry_ids: [] } }),
    });
    const unknown = makeItem({
      transfer: transfer({
        adjust_transfer: { entry_id: null, line_id: 5, candidate_entry_ids: [21] },
      }),
    });
    expect(flowAnswerLabel('adjust_transfer', known, 'TWD')).toBe('改成帳單的轉帳');
    expect(flowAnswerLabel('adjust_transfer', unknown, 'TWD')).toBe('改成帳單的轉帳…');
    expect(flowAnswerLabel('pair_transfer', known, 'TWD')).toBe('配成轉帳…');
    expect(flowAnswerLabel('adjust_amount', known, 'TWD')).toBe('改金額…');
    expect(flowAnswerLabel('dismiss', makeItem(), 'TWD')).toBe('忽略');
    expect(isCaseAction('adjust_amount')).toBe(true);
    expect(isCaseAction('dismiss')).toBe(false);
  });

  it('flowNote: the transfer hint follows the peer candidates', () => {
    const none = makeItem({
      question: 'transfer',
      transfer: transfer({ pair_transfer: { peer_candidates: [] } }),
    });
    expect(flowNote(none, 'TWD')).toBe(NO_PEER_HINT);
    const some = makeItem({
      question: 'transfer',
      transfer: transfer({ pair_transfer: { peer_candidates: [peer()] } }),
    });
    expect(flowNote(some, 'TWD')).toBe('選「配成轉帳…」把兩個帳戶的這筆對起來。');
  });

  it('flowLearnScope counts the case answers among the step answers', () => {
    const leg = makeItem({
      question: 'bank_only',
      actions: ['record', 'dismiss'],
      transfer: transfer({ adjust_transfer: { entry_id: 7, line_id: 5, candidate_entry_ids: [] } }),
    });
    expect(flowLearnScope(leg, 'TWD')).toBe('只有「記為支出…」「忽略」會記住');
  });

  it('flowError: the transfer and amount refusals', () => {
    expect(flowError('peer_leg_claimed')).toEqual({
      text: '沒成功：另一個帳戶的那一筆已經配給別的轉帳了',
      reload: false,
    });
    expect(flowError('entry_out_of_scope')).toEqual({ text: '這一題的資料剛剛變了', reload: true });
    expect(flowError('fx_pair_required')).toEqual({
      text: '沒成功：缺少外幣金額資料，不能直接改',
      reload: false,
    });
    const invalid = new HttpErrorResponse({
      status: 422,
      error: { detail: [{ msg: 'bad body' }] },
    });
    expect(flowError(invalid)).toEqual({
      text: '沒成功：答案沒有送出去，請再試一次',
      reload: false,
    });
  });

  it('flowError: no plain sentence carries a code', () => {
    const codes = [
      'peer_leg_claimed',
      'peer_ambiguous',
      'not_standalone',
      'cross_currency',
      'peer_is_card',
      'kind_sign',
      'fx_pair_required',
      'has_settlements',
      'peer_not_live',
      'peer_statement_busy',
      'owner_deleted',
      'duplicate_effect',
      'wrong_account',
      'wrong_currency',
      'not_member',
      'foreign_zero',
      'not_found',
      'case_closed',
    ];
    for (const code of codes) {
      const out = flowError(code);
      expect(out.text, code).not.toBe('沒成功：答案沒有送出去，請再試一次');
      expect(out.text, code).not.toMatch(/[a-z_]{4,}/);
      expect(out.reload, code).toBe(false);
    }
    for (const code of [
      'line_claimed',
      'line_not_in_case',
      'line_not_printed',
      'peer_out_of_scope',
    ]) {
      expect(flowError(code).reload, code).toBe(true);
    }
  });

  it('flowActions: 記為… last on a part-of line, the server order otherwise', () => {
    const actions = ['record', 'match_candidate', 'dismiss'] as const;
    const partOf = makeItem({ actions: [...actions], part_of: [entry({ amount: '-5000' })], rule_hint: null });
    expect(flowPartOf(partOf)).toBe(true);
    expect(flowActions(partOf)).toEqual(['match_candidate', 'dismiss', 'record']);
    const plain = makeItem({ actions: [...actions] });
    expect(flowPartOf(plain)).toBe(false);
    expect(flowActions(plain)).toBe(plain.actions);
  });

  it('flowLearnScope names the answers that learn, as the buttons read', () => {
    expect(flowLearnScope(makeItem({ actions: ['record', 'match_candidate', 'dismiss'] }), 'TWD')).toBe(
      '只有「記為支出…」「忽略」會記住',
    );
    expect(flowLearnScope(makeItem({ actions: ['record', 'dismiss'] }), 'TWD')).toBe('「記為支出…」「忽略」都會記住');
    expect(flowLearnScope(makeItem({ actions: ['defer'] }), 'TWD')).toBeNull();
    expect(flowLearnScope(makeItem({ rule_hint: null }), 'TWD')).toBeNull();
  });

  it('flowExcluded: the compared entry of an amount question only', () => {
    const candidates = [
      { entry_id: 9, date: '2026-09-01', title: '測試', amount: '-10', reason: null, residual: null, date_distance: 0 },
    ];
    expect(flowExcluded(makeItem({ question: 'amount_differs', entry: entry() }))).toBe(entry().id);
    expect(flowExcluded(makeItem({ question: 'amount_differs', entry: null, candidates }))).toBe(9);
    expect(flowExcluded(makeItem({ question: 'date_differs', entry: entry() }))).toBeNull();
    expect(flowExcluded(makeItem({ question: 'bank_only' }))).toBeNull();
  });

  it('flowError: a changed question offers a reload; the rest is plain words, never a code', () => {
    for (const code of ['stale_case', 'candidate_changed', 'case_moved', 'not_offered']) {
      expect(flowError(code), code).toEqual({ text: '這一題的資料剛剛變了', reload: true });
    }
    expect(flowError('protected')).toEqual({ text: '沒成功：這筆是轉帳或交割款，不能改金額', reload: false });
    expect(flowError('brand_new_code')).toEqual({ text: '沒成功：答案沒有送出去，請再試一次', reload: false });
    expect(flowError(null).text).not.toMatch(/[a-z_]{4,}/);
    const conflict = new HttpErrorResponse({ status: 409, error: { message: 'stale_case: case 3' } });
    expect(flowError(conflict).reload).toBe(true);
    const invalid = new HttpErrorResponse({ status: 422, error: { detail: [{ msg: 'Value error, entry_claimed' }] } });
    expect(flowError(invalid).text).toBe('沒成功：這筆記錄已經對到別的行了');
    expect(flowError(new HttpErrorResponse({ status: 500 })).text).toBe('沒成功：答案沒有送出去，請再試一次');
  });
});
