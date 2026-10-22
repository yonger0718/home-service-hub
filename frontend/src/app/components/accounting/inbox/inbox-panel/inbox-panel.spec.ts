import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  InboxAccount,
  InboxApplyBody,
  InboxApplyOut,
  InboxCounts,
  InboxItem,
  InboxOut,
} from '../inbox.model';
import { AppInboxPanelComponent, inboxLearn } from './inbox-panel';

const APPLY_URL = '/api/accounting/reconciliation/inbox/apply';

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
    line: {
      id: 5,
      date: '2026-09-03',
      merchant: '全家便利商店',
      amount: '-120.0000',
      line_kind: 'purchase',
    },
    entry: null,
    candidates: [],
    proposal: null,
    actions: ['record', 'match_candidate', 'dismiss'],
    rule_hint: {
      merchant_pattern: '全家便利商店',
      line_kind: 'purchase',
      flow_sign: '-',
      amount: '120.0000',
      default_category_id: null,
    },
    ...overrides,
  };
}

function makeInboxAccount(overrides: Partial<InboxAccount> = {}): InboxAccount {
  return { id: 1, name: '玉山', currency: 'TWD', is_credit: false, items: [], ...overrides };
}

function makeInbox(accounts: InboxAccount[], counts: Partial<InboxCounts> = {}): InboxOut {
  return {
    generated_at: '2026-10-22T00:00:00Z',
    counts: {
      ask: 0,
      ask_items: 0,
      proposed: 0,
      balance_gap: 0,
      settlement: 0,
      other: 0,
      ...counts,
    },
    accounts,
  };
}

/** Two 全家 lines that share a group_key (one merged card) and a Uber line on its own. */
function member(caseId: number, version: number, date: string): InboxItem {
  return makeItem({
    case_id: caseId,
    case_version: version,
    group_key: 'g-family',
    line: {
      id: 50 + caseId,
      date,
      merchant: '全家便利商店',
      amount: '-120.0000',
      line_kind: 'purchase',
    },
  });
}

const UBER = makeItem({
  case_id: 5,
  case_version: 7,
  line: {
    id: 55,
    date: '2026-09-12',
    merchant: 'Uber 行程',
    amount: '-250.0000',
    line_kind: 'purchase',
  },
  rule_hint: {
    merchant_pattern: 'Uber 行程',
    line_kind: 'purchase',
    flow_sign: '-',
    amount: '250.0000',
    default_category_id: null,
  },
});

function text(node: Element | null | undefined): string {
  return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
}

function button(scope: Element, label: string): HTMLButtonElement {
  return Array.from(scope.querySelectorAll<HTMLButtonElement>('button')).find(
    (node) => text(node) === label,
  )!;
}

function applied(...caseIds: number[]): InboxApplyOut {
  return {
    results: caseIds.map((case_id) => ({
      case_id,
      status: 'applied',
      action_id: 900 + case_id,
      code: null,
    })),
    rule_id: null,
    rule_error: null,
  };
}

describe('AppInboxPanelComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AppInboxPanelComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function render(data: InboxOut, mode: 'full' | 'preview' = 'full') {
    const fixture = TestBed.createComponent(AppInboxPanelComponent);
    fixture.componentRef.setInput('data', data);
    fixture.componentRef.setInput('mode', mode);
    const changed = vi.fn();
    fixture.componentInstance.changed.subscribe(changed);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement, changed };
  }

  function askCards(el: HTMLElement): HTMLElement[] {
    return Array.from(el.querySelectorAll<HTMLElement>('.ask-cards .inbox-card'));
  }

  function expectApply(): TestRequest {
    const req = http.expectOne((r) => r.method === 'POST' && r.url === APPLY_URL);
    return req;
  }

  function reload(fixture: ComponentFixture<AppInboxPanelComponent>, data: InboxOut): void {
    fixture.componentRef.setInput('data', data);
    fixture.detectChanges();
  }

  it('renders one card per group_key with ×N and the question text', () => {
    const data = makeInbox(
      [
        makeInboxAccount({ items: [member(1, 2, '2026-09-03'), member(2, 3, '2026-09-10'), UBER] }),
        makeInboxAccount({
          id: 2,
          name: '國泰 CUBE',
          is_credit: true,
          items: [
            makeItem({
              case_id: 20,
              question: 'proposed',
              folded: true,
              proposal: { id: 200, action: 'match', params: {}, author: 'policy', rationale: '' },
              actions: ['apply_proposal', 'reject_proposal'],
            }),
            makeItem({
              case_id: 21,
              question: 'balance_gap',
              folded: true,
              line: null,
              rule_hint: null,
              actions: ['anchor', 'dismiss'],
            }),
            makeItem({ case_id: 22, question: 'settlement', folded: true, actions: [] }),
            makeItem({ case_id: 23, question: 'other', folded: true, actions: ['dismiss'] }),
          ],
        }),
      ],
      { ask: 2, ask_items: 3, proposed: 1, balance_gap: 1, settlement: 1, other: 1 },
    );
    const { el } = render(data);

    expect(text(el.querySelector('.inbox-head'))).toBe('要你決定 2 · 其他 4');
    expect(Array.from(el.querySelectorAll('.ask-cards .acct')).map(text)).toEqual(['🏦 玉山']);
    const cards = askCards(el);
    expect(cards.length).toBe(2);
    expect(text(cards[0].querySelector('.title'))).toBe('2026/09/03 全家便利商店 −$120 ×2 筆');
    expect(text(cards[0].querySelector('.q'))).toBe('銀行有、你沒記');
    expect(Array.from(cards[0].querySelectorAll('.members span')).map(text)).toEqual([
      '2026/09/03 −$120',
      '2026/09/10 −$120',
    ]);
    expect(Array.from(cards[0].querySelectorAll('.btns button')).map(text)).toEqual([
      '記為支出…',
      '已記在別處，對到…',
      '忽略',
    ]);
    expect(text(cards[1].querySelector('.title'))).toBe('2026/09/12 Uber 行程 −$250');
    expect(cards[1].querySelector('.members')).toBeNull();

    const folds = Array.from(el.querySelectorAll('details.fold'));
    expect(folds.map((fold) => text(fold.querySelector('summary')))).toEqual([
      '系統有提議 1（全部套用）',
      '期初差額 1',
      '國泰交割款 1 — 等證券連動',
      '其他 1',
    ]);
    expect(button(folds[0], '全部套用')).toBeTruthy();
    expect(Array.from(folds[0].querySelectorAll('.inbox-card .btns button')).map(text)).toEqual([
      '套用',
      '退回',
    ]);
    expect(Array.from(folds[1].querySelectorAll('.inbox-card .btns button')).map(text)).toEqual([
      '對齊銀行期初',
      '忽略',
    ]);
    expect(folds[2].querySelectorAll('.inbox-card').length).toBe(1);
    expect(folds[2].querySelectorAll('button').length).toBe(0);
    expect(Array.from(folds[3].querySelectorAll('.inbox-card .btns button')).map(text)).toEqual([
      '忽略',
    ]);
    expect(text(folds[3].querySelector('.q'))).toBe('需要在 API 處理（案件 #23）');
  });

  it('one-tap dismiss posts the apply body with every member and the answer', () => {
    const { fixture, el, changed } = render(
      makeInbox(
        [makeInboxAccount({ items: [member(1, 2, '2026-09-03'), member(2, 3, '2026-09-10')] })],
        { ask: 1 },
      ),
    );
    // A merged card asks 確定 once on the same button.
    button(askCards(el)[0], '忽略').click();
    fixture.detectChanges();
    http.expectNone(APPLY_URL);
    button(askCards(el)[0], '確定').click();
    fixture.detectChanges();

    const req = expectApply();
    expect(req.request.body as InboxApplyBody).toEqual({
      cases: [
        { case_id: 1, case_version: 2 },
        { case_id: 2, case_version: 3 },
      ],
      action: 'dismiss',
      params: {},
      answer: '忽略',
      learn: { amount_exact: true },
    });
    req.flush({ ...applied(1, 2), rule_error: 'rule_pattern_empty' });
    fixture.detectChanges();

    expect(askCards(el).length).toBe(0);
    expect(text(el.querySelector('.rule-error'))).toBe('規則沒存：描述只有數字，不能學');
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('learn checkbox adds learn with amount_exact true for record and false for accept', () => {
    expect(inboxLearn(true, false, 'record')).toEqual({ amount_exact: true });
    expect(inboxLearn(true, true, 'record')).toEqual({ amount_exact: false });
    expect(inboxLearn(true, false, 'accept_bank_amount')).toEqual({ amount_exact: false });
    expect(inboxLearn(false, false, 'record')).toBeNull();

    const differs = makeItem({
      case_id: 8,
      case_version: 4,
      question: 'amount_differs',
      line: {
        id: 58,
        date: '2026-09-05',
        merchant: '台電',
        amount: '-1300.0000',
        line_kind: 'purchase',
      },
      candidates: [
        {
          entry_id: 3,
          date: '2026-09-05',
          title: '電費',
          amount: '-1200.0000',
          reason: null,
          residual: null,
          date_distance: 0,
        },
      ],
      actions: ['match_candidate', 'accept_bank_amount', 'dismiss'],
      rule_hint: {
        merchant_pattern: '台電',
        line_kind: 'purchase',
        flow_sign: '-',
        amount: '1300.0000',
        default_category_id: null,
      },
    });
    const { fixture, el } = render(
      makeInbox([makeInboxAccount({ items: [UBER, differs] })], { ask: 2 }),
    );
    const [uber, power] = askCards(el);
    expect(text(power.querySelector('.learn'))).toBe('以後「台電」都這樣');

    // Unticked: no rule.
    button(uber, '忽略').click();
    const untickedReq = expectApply();
    expect((untickedReq.request.body as InboxApplyBody).learn).toBeNull();
    untickedReq.flush(applied(5));
    fixture.detectChanges();

    power.querySelector<HTMLInputElement>('.learn input')!.click();
    fixture.detectChanges();
    button(power, '改成銀行的 −$1,300').click();
    const req = expectApply();
    expect(req.request.body as InboxApplyBody).toEqual({
      cases: [{ case_id: 8, case_version: 4 }],
      action: 'accept_bank_amount',
      params: {},
      answer: '改成銀行的 −$1,300',
      learn: { amount_exact: false },
    });
    req.flush(applied(8));
    fixture.detectChanges();
    expect(askCards(el).length).toBe(0);
  });

  it('a refused member stays with 沒成功 text and the others leave', () => {
    const { fixture, el, changed } = render(
      makeInbox(
        [
          makeInboxAccount({
            items: [
              member(1, 2, '2026-09-03'),
              member(2, 3, '2026-09-10'),
              member(3, 1, '2026-09-17'),
            ],
          }),
        ],
        { ask: 1 },
      ),
    );
    button(askCards(el)[0], '忽略').click();
    fixture.detectChanges();
    button(askCards(el)[0], '確定').click();
    expectApply().flush({
      results: [
        { case_id: 1, status: 'applied', action_id: 901, code: null },
        { case_id: 2, status: 'refused', action_id: null, code: 'stale_case' },
        { case_id: 3, status: 'replayed', action_id: 903, code: null },
      ],
      rule_id: null,
      rule_error: null,
    });
    fixture.detectChanges();

    const cards = askCards(el);
    expect(cards.length).toBe(1);
    expect(text(cards[0].querySelector('.title'))).toBe('2026/09/10 全家便利商店 −$120');
    expect(text(cards[0].querySelector('.refused'))).toBe('沒成功：對象已變，重新整理');
    expect(el.querySelector('.rule-error')).toBeNull();
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('the refused text survives a data reload', () => {
    const { fixture, el } = render(makeInbox([makeInboxAccount({ items: [UBER] })], { ask: 1 }));
    button(askCards(el)[0], '忽略').click();
    expectApply().flush({
      results: [{ case_id: 5, status: 'refused', action_id: null, code: 'case_closed' }],
      rule_id: null,
      rule_error: null,
    });
    fixture.detectChanges();
    expect(text(askCards(el)[0].querySelector('.refused'))).toBe('沒成功：已經處理過了');

    // The parent reloads after `changed`: the case is still there, so is its message.
    reload(
      fixture,
      makeInbox([makeInboxAccount({ items: [{ ...UBER, case_version: 8 }] })], { ask: 1 }),
    );
    expect(text(askCards(el)[0].querySelector('.refused'))).toBe('沒成功：已經處理過了');

    // Applying it later clears the message for good.
    button(askCards(el)[0], '忽略').click();
    const req = expectApply();
    expect((req.request.body as InboxApplyBody).cases).toEqual([{ case_id: 5, case_version: 8 }]);
    req.flush(applied(5));
    fixture.detectChanges();
    expect(askCards(el).length).toBe(0);
    reload(fixture, makeInbox([makeInboxAccount({ items: [UBER] })], { ask: 1 }));
    expect(askCards(el)[0].querySelector('.refused')).toBeNull();

    // A case that leaves `data` loses its message too.
    button(askCards(el)[0], '忽略').click();
    expectApply().flush({
      results: [{ case_id: 5, status: 'refused', action_id: null, code: 'stale_case' }],
      rule_id: null,
      rule_error: null,
    });
    fixture.detectChanges();
    expect(askCards(el)[0].querySelector('.refused')).not.toBeNull();
    reload(fixture, makeInbox([], { ask: 0 }));
    reload(fixture, makeInbox([makeInboxAccount({ items: [UBER] })], { ask: 1 }));
    expect(askCards(el)[0].querySelector('.refused')).toBeNull();
  });

  it('single card: learn unticked, 金額不同也算 sends amount_exact false; merged card: learn ticked, no amount option', () => {
    const { fixture, el } = render(
      makeInbox(
        [
          makeInboxAccount({
            items: [UBER, member(1, 2, '2026-09-03'), member(2, 3, '2026-09-10')],
          }),
        ],
        { ask: 2 },
      ),
    );
    const [single, merged] = askCards(el);

    const singleLearn = single.querySelector<HTMLInputElement>('.learn input')!;
    const amountAny = single.querySelector<HTMLInputElement>('.amount-any input')!;
    expect(text(single.querySelector('.learn'))).toBe('以後「Uber 行程」都這樣');
    expect(singleLearn.checked).toBe(false);
    expect(text(single.querySelector('.amount-any'))).toBe('金額不同也算');
    expect(amountAny.checked).toBe(false);
    expect(amountAny.disabled).toBe(true);
    singleLearn.click();
    fixture.detectChanges();
    expect(amountAny.disabled).toBe(false);
    amountAny.click();
    fixture.detectChanges();
    button(single, '忽略').click();
    const req = expectApply();
    expect((req.request.body as InboxApplyBody).learn).toEqual({ amount_exact: false });
    req.flush(applied(5));
    fixture.detectChanges();

    const mergedLearn = merged.querySelector<HTMLInputElement>('.learn input')!;
    expect(mergedLearn.checked).toBe(true);
    expect(mergedLearn.disabled).toBe(false);
    expect(merged.querySelector('.amount-any')).toBeNull();
    expect(text(merged.querySelector('.amount-locked'))).toBe('同金額 ×2');
    // Still editable: unticked, the merged answer teaches nothing.
    mergedLearn.click();
    fixture.detectChanges();
    button(merged, '忽略').click();
    fixture.detectChanges();
    button(merged, '確定').click();
    const mergedReq = expectApply();
    expect((mergedReq.request.body as InboxApplyBody).learn).toBeNull();
    mergedReq.flush(applied(1, 2));
  });

  it('preview mode shows three cards and the 還有 N 張 link', () => {
    const items = [11, 12, 13].map((id) =>
      makeItem({
        case_id: id,
        line: {
          id,
          date: `2026-09-${id}`,
          merchant: `店 ${id}`,
          amount: '-10.0000',
          line_kind: 'purchase',
        },
      }),
    );
    const card = makeItem({
      case_id: 31,
      line: {
        id: 31,
        date: '2026-09-21',
        merchant: '卡費',
        amount: '-90.0000',
        line_kind: 'purchase',
      },
    });
    const card2 = makeItem({
      case_id: 32,
      line: {
        id: 32,
        date: '2026-09-22',
        merchant: '年費',
        amount: '-80.0000',
        line_kind: 'purchase',
      },
    });
    const folded = makeItem({ case_id: 40, question: 'other', folded: true, actions: ['dismiss'] });
    const { el } = render(
      makeInbox(
        [
          makeInboxAccount({ items: [...items, folded] }),
          makeInboxAccount({ id: 2, name: '國泰 CUBE', is_credit: true, items: [card, card2] }),
        ],
        { ask: 5, ask_items: 5, other: 1 },
      ),
      'preview',
    );

    const cards = askCards(el);
    expect(cards.length).toBe(3);
    expect(cards.map((node) => text(node.querySelector('.title')))).toEqual([
      '2026/09/11 店 11 −$10',
      '2026/09/12 店 12 −$10',
      '2026/09/13 店 13 −$10',
    ]);
    const link = el.querySelector<HTMLAnchorElement>('a.more')!;
    expect(text(link)).toBe('還有 2 張 →');
    expect(link.getAttribute('href')).toBe('/accounting/reminders?tab=todo');
    expect(el.querySelector('details.fold')).toBeNull();
  });

  it('applies proposals one by one and keeps going after a 409', () => {
    const proposal = (caseId: number, id: number, merchant: string) =>
      makeItem({
        case_id: caseId,
        question: 'proposed',
        folded: true,
        line: {
          id: caseId,
          date: '2026-09-08',
          merchant,
          amount: '-60.0000',
          line_kind: 'purchase',
        },
        proposal: { id, action: 'match', params: {}, author: 'policy', rationale: '' },
        actions: ['apply_proposal', 'reject_proposal'],
      });
    const { fixture, el, changed } = render(
      makeInbox(
        [makeInboxAccount({ items: [proposal(60, 600, '早餐'), proposal(61, 601, '午餐')] })],
        { proposed: 2 },
      ),
    );
    const fold = el.querySelector<HTMLElement>('details.fold')!;
    button(fold, '全部套用').click();
    fixture.detectChanges();

    const first = http.expectOne(
      (r) => r.method === 'POST' && r.url === '/api/accounting/reconciliation/proposals/600/apply',
    );
    http.expectNone('/api/accounting/reconciliation/proposals/601/apply');
    expect(text(el.querySelector('.progress'))).toBe('套用中 1/2');
    first.flush(
      { detail: 'case_closed: already resolved' },
      { status: 409, statusText: 'Conflict' },
    );
    fixture.detectChanges();

    const second = http.expectOne(
      (r) => r.method === 'POST' && r.url === '/api/accounting/reconciliation/proposals/601/apply',
    );
    expect(text(el.querySelector('.progress'))).toBe('套用中 2/2');
    second.flush({});
    fixture.detectChanges();

    expect(el.querySelector('.progress')).toBeNull();
    const left = Array.from(el.querySelectorAll<HTMLElement>('details.fold .inbox-card'));
    expect(left.length).toBe(1);
    expect(text(left[0].querySelector('.title'))).toBe('2026/09/08 早餐 −$60');
    expect(text(left[0].querySelector('.refused'))).toBe('沒成功：已經處理過了');
    expect(text(el.querySelector('details.fold summary'))).toBe('系統有提議 1（全部套用）');
    expect(changed).toHaveBeenCalledTimes(1);
  });
});
