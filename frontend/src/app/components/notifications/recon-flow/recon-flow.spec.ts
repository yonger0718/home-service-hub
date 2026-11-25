import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountingService } from '../../../services/accounting.service';
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { NotificationCenterService } from '../../../services/notification-center.service';
import { ToastService } from '../../../services/toast.service';
import {
  InboxApplyBody,
  InboxApplyOut,
  InboxCandidate,
  InboxItem,
  InboxOut,
  InboxPeerCandidate,
  ReconciliationAction,
} from '../../accounting/inbox/inbox.model';
import {
  applied,
  button,
  categoryTile,
  makeInbox,
  makeInboxAccount,
  makeItem,
  text,
} from '../../accounting/inbox/testing';
import { makeCategory } from '../../accounting/testing/fixtures';
import { NotifTask } from '../notif-task';
import { ReconFlowComponent } from './recon-flow';
import { ReconUndoService } from './recon-undo.service';

const INBOX_URL = '/api/accounting/reconciliation/inbox';
const APPLY_URL = '/api/accounting/reconciliation/inbox/apply';
const STATEMENT = 10;
const RECONCILE_URL = '/api/accounting/accounts/1/statements/10/reconcile';

/** A fictional bank_only line `商家 n` of −$(100 + n) on statement 10; 忽略 first so key 1 answers at once. */
function line(caseId: number, overrides: Partial<InboxItem> = {}): InboxItem {
  const merchant = `測試商家 ${caseId}`;
  return makeItem({
    case_id: caseId,
    case_version: 1,
    line: { id: 100 + caseId, date: '2026-09-03', merchant, amount: `-${100 + caseId}.0000`, line_kind: 'purchase' },
    actions: ['dismiss', 'record', 'match_candidate'],
    rule_hint: {
      merchant_pattern: merchant,
      line_kind: 'purchase',
      flow_sign: '-',
      amount: `${100 + caseId}.0000`,
      default_category_id: null,
    },
    ...overrides,
  });
}

/** Case `old`'s bank line back under a fresh case id (version 1), as the reconcile after 復原 returns it. */
function fresh(old: number, caseId: number): InboxItem {
  return { ...line(old), case_id: caseId, case_version: 1 };
}

function ten(): InboxItem[] {
  return Array.from({ length: 10 }, (_, i) => line(i + 1));
}

/** amount_differs: bank −$2,096 vs ledger −$2,066 (entry 500). */
function differs(caseId: number, overrides: Partial<InboxItem> = {}): InboxItem {
  return makeItem({
    case_id: caseId,
    question: 'amount_differs',
    kind: 'amount_delta',
    line: { id: 200 + caseId, date: '2026-09-12', merchant: '測試服飾', amount: '-2096.0000', line_kind: 'purchase' },
    entry: { id: 500, date: '2026-09-12', title: '測試服飾', amount: '-2066.0000', account_id: 1 },
    actions: ['accept_bank_amount', 'match_candidate', 'dismiss'],
    rule_hint: {
      merchant_pattern: '測試服飾',
      line_kind: 'purchase',
      flow_sign: '-',
      amount: '2096.0000',
      default_category_id: null,
    },
    ...overrides,
  });
}

/** ledger_only on entry 500: the follower of `differs`. */
function ledgerOnly(caseId: number): InboxItem {
  return makeItem({
    case_id: caseId,
    question: 'ledger_only',
    kind: 'entry_unmatched',
    line: null,
    entry: { id: 500, date: '2026-09-12', title: '測試服飾', amount: '-2066.0000', account_id: 1 },
    actions: ['dismiss'],
    rule_hint: null,
  });
}

function inbox(items: InboxItem[]): InboxOut {
  return makeInbox([makeInboxAccount({ items })]);
}

function refused(caseId: number, code: string): InboxApplyOut {
  return { results: [{ case_id: caseId, status: 'refused', action_id: null, code }], rule_id: null, rule_error: null };
}

const NO_PEER_HINT =
  '另一個帳戶還沒有對得上的那一筆（帳單可能還沒匯入）。已經記過的話選「已記在別處，對到…」；不然先略過，另一邊進來後再配。';

function actionsUrl(caseId: number): string {
  return `/api/accounting/reconciliation/cases/${caseId}/actions`;
}

function cand(entryId: number, title: string, amount = '20000.0000'): InboxCandidate {
  return { entry_id: entryId, date: '2026-09-05', title, amount, reason: null, residual: null, date_distance: 0 };
}

/** amount_differs on a transfer leg (entry 500 known), as the API sends it: no 改成銀行的, the transfer field instead. */
function leg(caseId: number): InboxItem {
  return differs(caseId, {
    actions: ['match_candidate', 'dismiss'],
    transfer: {
      adjust_transfer: { entry_id: 500, line_id: 200 + caseId, candidate_entry_ids: [500] },
      pair_transfer: null,
    },
  });
}

/**
 * ambiguous −$20,000 (line 300 + id): candidates 610 (−$19,970) / 611 (−$19,950) and the plain expense 612 (−$1,250);
 * `legs` are the transfer legs.
 */
function ambiguous(caseId: number, legs: number[] = [610, 611]): InboxItem {
  return makeItem({
    case_id: caseId,
    question: 'ambiguous',
    kind: 'ambiguous_match',
    line: { id: 300 + caseId, date: '2026-09-05', merchant: '測試轉出', amount: '-20000.0000', line_kind: 'withdrawal' },
    candidates: [
      cand(610, '測試轉帳 甲', '-19970.0000'),
      cand(611, '測試轉帳 乙', '-19950.0000'),
      cand(612, '測試餐費', '-1250.0000'),
    ],
    actions: ['match_candidate', 'dismiss'],
    rule_hint: null,
    transfer: {
      adjust_transfer: { entry_id: null, line_id: 300 + caseId, candidate_entry_ids: legs },
      pair_transfer: null,
    },
  });
}

function peer(): InboxPeerCandidate {
  return { line_id: 801, statement_id: 20, account_name: '測試銀行', posted_date: '2026-09-06', amount: '20000.0000' };
}

/** transfer −$20,000 (line 400 + id) waiting for its other leg; `peers` are the other account's lines. */
function needsPeer(caseId: number, peers: InboxPeerCandidate[] = [peer()]): InboxItem {
  return makeItem({
    case_id: caseId,
    question: 'transfer',
    kind: 'transfer_needs_peer',
    line: { id: 400 + caseId, date: '2026-09-05', merchant: '測試轉出', amount: '-20000.0000', line_kind: 'withdrawal' },
    actions: ['match_candidate', 'dismiss'],
    rule_hint: null,
    transfer: { adjust_transfer: null, pair_transfer: { peer_candidates: peers } },
  });
}

/** The `/cases/{id}/actions` answer: the applied action `id`. */
function took(id: number, caseId: number, action: string): ReconciliationAction {
  return {
    id,
    case_id: caseId,
    proposal_id: null,
    action,
    params: {},
    actor: 'spa',
    status: 'applied',
    effect_slot: 'case',
    created_entry_ids: [],
    deleted_entry_ids: [],
    touched_entry_ids: [],
    touched_group_ids: [],
    replayed: false,
    label: 'spa',
    answer: null,
    created_at: '2026-10-01T00:00:00Z',
    reverted_by_action_id: null,
    line: null,
  };
}

describe('ReconFlowComponent', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<ReconFlowComponent>;
  let el: HTMLElement;
  let closed: ReturnType<typeof vi.fn<() => void>>;
  const tasks = signal<readonly NotifTask[]>([]);

  beforeEach(() => setup());

  function setup(): void {
    tasks.set([]);
    // A failed verify in the previous afterEach skips Angular's own reset; without this one failure fails the rest.
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ReconFlowComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: NotificationCenterService, useValue: { tasks } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  }

  afterEach(() => {
    // Destroy first: a failed verify must not leave this flow's document key listener to the next test.
    fixture?.destroy();
    document.querySelectorAll('.spec-extra').forEach((node) => node.remove());
    http.verify();
  });

  function render(mode: LayoutMode = 'panes', statementId = STATEMENT): void {
    TestBed.inject(LayoutModeService).set(mode);
    fixture = TestBed.createComponent(ReconFlowComponent);
    fixture.componentRef.setInput('statementId', statementId);
    closed = vi.fn<() => void>();
    fixture.componentInstance.closed.subscribe(closed);
    el = fixture.nativeElement as HTMLElement;
    fixture.detectChanges();
  }

  function detect(): void {
    TestBed.tick();
    fixture.detectChanges();
  }

  function firstRead(): TestRequest {
    return http.expectOne((r) => r.url === INBOX_URL && r.params.get('limit') === '1000' && !r.params.has('account_id'));
  }

  function reread(): TestRequest {
    return http.expectOne(
      (r) => r.url === INBOX_URL && r.params.get('account_id') === '1' && r.params.get('limit') === '1000',
    );
  }

  function open(items: InboxItem[], mode: LayoutMode = 'panes'): void {
    render(mode);
    firstRead().flush(inbox(items));
    detect();
  }

  function apply(): TestRequest {
    return http.expectOne((r) => r.method === 'POST' && r.url === APPLY_URL);
  }

  function key(k: string, target: EventTarget = document, init: KeyboardEventInit = {}): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(event);
    detect();
    return event;
  }

  function headline(): string {
    return text(el.querySelector('h2'));
  }

  function progressText(): string {
    return text(el.querySelector('.flow-progress-text'));
  }

  function answers(): HTMLButtonElement[] {
    return Array.from(el.querySelectorAll<HTMLButtonElement>('.flow-answers button'));
  }

  function switchEl(): HTMLButtonElement | null {
    return el.querySelector<HTMLButtonElement>('button[role="switch"]');
  }

  /** 復原 on the current toast: the revert, then the statement reconcile (account 1, statement 10). */
  function undoLast(actionId: number): void {
    const toasts = TestBed.inject(ToastService);
    toasts.runAction(toasts.current()!.id);
    http.expectOne(`/api/accounting/reconciliation/actions/${actionId}/revert`).flush({});
    http.expectOne(RECONCILE_URL).flush({});
    detect();
  }

  /** Answer the current question with key 1 and land a re-read with `rest`. */
  function answerWith1(caseId: number, rest: InboxItem[]): void {
    key('1');
    apply().flush(applied(caseId));
    reread().flush(inbox(rest));
    detect();
  }

  it('renders chip, headline, grid and three answers for an amount_differs item; first answer solid', () => {
    open([differs(1)]);
    expect(text(el.querySelector('.flow-chip'))).toBe('金額不同');
    expect(headline()).toBe('銀行多扣了 $30');
    const table = el.querySelector('table')!;
    expect(Array.from(table.querySelectorAll('thead th[scope="col"]')).map(text)).toEqual(['銀行帳單', '你的記錄']);
    expect(Array.from(table.querySelectorAll('tbody th[scope="row"]')).map(text)).toEqual(['日期', '商家', '金額']);
    const amountCells = table.querySelectorAll('tbody tr:nth-child(3) td');
    expect(Array.from(amountCells).map(text)).toEqual(['−$2,096', '−$2,066']);
    expect(amountCells[0].classList).toContain('differs');
    expect(table.querySelector('tbody tr:nth-child(2) td')!.classList).not.toContain('differs');
    expect(answers().map((b) => text(b.querySelector('span')))).toEqual([
      '改成銀行的 −$2,096',
      '已記在別處，對到…',
      '不是同一筆',
    ]);
    expect(answers().map((b) => text(b.querySelector('kbd')))).toEqual(['1', '2', '3']);
    expect(answers()[0].classList).toContain('solid');
    expect(answers()[1].classList).not.toContain('solid');
    expect(text(el.querySelector('.flow-hint'))).toBe('1–3 選擇 · S 略過 · ← → 上下筆 · Esc 關閉');
  });

  it('marks no answer solid when the item has part_of', () => {
    open([
      line(1, {
        part_of: [{ id: 700, date: '2026-09-01', title: '測試還款', amount: '-5000.0000', account_id: 1 }],
        rule_hint: null,
      }),
    ]);
    expect(answers().length).toBe(3);
    expect(answers().some((b) => b.classList.contains('solid'))).toBe(false);
  });

  it('renders the header, link and close button at panes width, the back link and short progress on phone', () => {
    tasks.set([{ id: `recon:${STATEMENT}`, progress: { k: 17, n: 28 } } as unknown as NotifTask]);
    open(ten());
    expect(text(el.querySelector('.flow-kicker'))).toBe('對帳 · 玉山');
    expect(text(el.querySelector('.flow-title'))).toBe('至 9/30 帳單');
    expect(text(el.querySelector('.flow-sub'))).toBe('已對上 17 / 28 行 · 這次要你決定 10 筆');
    expect(el.querySelector('a.flow-statement')!.getAttribute('href')).toBe('/accounting/accounts/1/statements/10');
    expect(progressText()).toBe('第 1 / 10 筆');
    expect(el.querySelectorAll('.flow-bar [data-seg]').length).toBe(10);
    expect(el.querySelector('.flow-bar')!.getAttribute('aria-hidden')).toBe('true');
    el.querySelector<HTMLButtonElement>('button[aria-label="關閉對帳"]')!.click();
    expect(closed).toHaveBeenCalledTimes(1);
    expect(el.getAttribute('role')).toBe('region');
    expect(el.getAttribute('aria-label')).toBe('對帳');

    fixture.destroy();
    tasks.set([]);
    open(ten(), 'phone');
    expect(text(el.querySelector('.flow-sub'))).toBe('這次要你決定 10 筆');
    expect(progressText()).toBe('1 / 10');
    expect(el.querySelector('button[aria-label="關閉對帳"]')).toBeNull();
    expect(el.querySelector('kbd')).toBeNull();
    expect(el.querySelector('.flow-hint')).toBeNull();
    el.querySelector<HTMLButtonElement>('[aria-label="返回通知"]')!.click();
    expect(closed).toHaveBeenCalledTimes(1);
  });

  it('answering advances: key 1 posts one case with the button label, then shows 第 2 / 10 筆', () => {
    const marked = vi.spyOn(TestBed.inject(AccountingService), 'markEntriesChanged');
    open(ten());
    expect(el.querySelector('.flow-loading')).toBeNull();
    expect(answers().length).toBe(3);
    expect(headline()).toBe('測試商家 1 −$101');
    key('1');
    const req = apply();
    expect(req.request.body as InboxApplyBody).toEqual({
      cases: [{ case_id: 1, case_version: 1 }],
      action: 'dismiss',
      params: {},
      answer: '忽略',
      learn: null,
    });
    req.flush(applied(1));
    detect();
    // 已處理 with 復原 as soon as the answer is in; the step moves only after the re-read.
    expect(TestBed.inject(ToastService).current()).toEqual(
      expect.objectContaining({ kind: 'done', text: '已處理：忽略', action: expect.objectContaining({ label: '復原' }) }),
    );
    expect(headline()).toBe('測試商家 1 −$101');
    reread().flush(inbox(ten().slice(1)));
    detect();
    expect(marked).toHaveBeenCalledTimes(1);
    expect(headline()).toBe('測試商家 2 −$102');
    expect(progressText()).toBe('第 2 / 10 筆');
    // The owner's key moved the step: focus is on the new headline.
    expect(document.activeElement).toBe(el.querySelector('h2'));
  });

  it('shows 讀取中… while the first read is pending', () => {
    render();
    expect(text(el.querySelector('.flow-loading'))).toBe('讀取中…');
    firstRead().flush(inbox(ten()));
    detect();
    expect(el.querySelector('.flow-loading')).toBeNull();
    // Initial load never steals focus.
    expect(document.activeElement).not.toBe(el.querySelector('h2'));
  });

  it('double press: two clicks before the response send one request', () => {
    open(ten());
    answers()[0].click();
    answers()[0].click();
    detect();
    key('1');
    expect(el.querySelector('.flow-answers')!.getAttribute('aria-busy')).toBe('true');
    expect(answers()[0].disabled).toBe(true);
    apply().flush(applied(1));
    reread().flush(inbox(ten().slice(1)));
    detect();
    expect(headline()).toBe('測試商家 2 −$102');
  });

  describe('related pair', () => {
    const pair = () => [differs(1), ledgerOnly(2), line(3)];

    it('shows the pair once with the note and applies the leader only; both count as handled', () => {
      open(pair());
      expect(progressText()).toBe('第 1 / 3 筆');
      expect(text(el.querySelector('.flow-pair'))).toBe(
        '你這筆 −$2,066 也列在「你記了，銀行沒有」。這裡對上或改金額後，那一題會自動消失。',
      );
      key('1');
      const req = apply();
      expect((req.request.body as InboxApplyBody).cases).toEqual([{ case_id: 1, case_version: 1 }]);
      expect((req.request.body as InboxApplyBody).action).toBe('accept_bank_amount');
      req.flush(applied(1));
      reread().flush(inbox([line(3)]));
      detect();
      expect(headline()).toBe('測試商家 3 −$103');
      expect(progressText()).toBe('第 3 / 3 筆');
      answerWith1(3, []);
      expect(text(el.querySelector('.flow-end h2'))).toBe('這張帳單都決定好了');
      expect(progressText()).toBe('3 / 3 筆');
    });

    it('a follower listed before its leader: opens on the leader as 第 1 / 3 筆; after 不是同一筆 the follower is next', () => {
      open([ledgerOnly(2), differs(1), line(3)]);
      expect(text(el.querySelector('.flow-chip'))).toBe('金額不同');
      expect(progressText()).toBe('第 1 / 3 筆');
      key('3');
      const req = apply();
      expect((req.request.body as InboxApplyBody).action).toBe('dismiss');
      req.flush(applied(1));
      reread().flush(inbox([ledgerOnly(2), line(3)]));
      detect();
      expect(text(el.querySelector('.flow-chip'))).toBe('你記了，銀行沒有');
      expect(progressText()).toBe('第 2 / 3 筆');
    });

    it('對到 a different entry: the re-read still has the follower, which is the next question', () => {
      const candidate = {
        entry_id: 600,
        date: '2026-09-12',
        title: '測試服飾 另一筆',
        amount: '-2096.0000',
        reason: null,
        residual: null,
        date_distance: 0,
      };
      open([ledgerOnly(2), differs(1, { candidates: [candidate] }), line(3)]);
      key('2');
      const sheet = el.querySelector<HTMLElement>('app-inbox-match-sheet')!;
      sheet.querySelector<HTMLInputElement>('input[type="radio"]')!.click();
      detect();
      button(sheet, '確定').click();
      detect();
      const req = apply();
      expect(req.request.body as InboxApplyBody).toEqual({
        cases: [{ case_id: 1, case_version: 1 }],
        action: 'match_candidate',
        params: { entry_ids: { '1': 600 } },
        answer: '對到 測試服飾 另一筆',
        learn: null,
      });
      req.flush(applied(1));
      reread().flush(inbox([ledgerOnly(2), line(3)]));
      detect();
      expect(text(el.querySelector('.flow-chip'))).toBe('你記了，銀行沒有');
      expect(progressText()).toBe('第 2 / 3 筆');
    });

    it('a re-read that still has the follower shows it next', () => {
      open(pair());
      key('3');
      apply().flush(applied(1));
      reread().flush(inbox([ledgerOnly(2), line(3)]));
      detect();
      expect(text(el.querySelector('.flow-chip'))).toBe('你記了，銀行沒有');
      expect(progressText()).toBe('第 2 / 3 筆');
      expect(el.querySelector('.flow-pair')).toBeNull();
    });
  });

  it('refused because the question changed: 這一題的資料剛剛變了 with 重新讀取, still on the same question', () => {
    open(ten());
    key('1');
    apply().flush(refused(1, 'stale_case'));
    reread().flush(inbox([line(1, { case_version: 2 }), ...ten().slice(1)]));
    detect();
    const alert = el.querySelector('.flow-error[role="alert"]')!;
    expect(text(alert.querySelector('span'))).toBe('這一題的資料剛剛變了');
    expect(text(alert)).not.toContain('對象已變');
    // 重新讀取 is a GET only; the error goes and the question shows what the read returned.
    button(alert, '重新讀取').click();
    detect();
    expect(answers()[0].disabled).toBe(true);
    http.expectNone(APPLY_URL);
    reread().flush(inbox([line(1, { case_version: 3 }), ...ten().slice(1)]));
    detect();
    expect(el.querySelector('.flow-error')).toBeNull();
    expect(headline()).toBe('測試商家 1 −$101');
    key('1');
    expect((apply().request.body as InboxApplyBody).cases).toEqual([{ case_id: 1, case_version: 3 }]);
  });

  it('重新讀取 that fails: 清單讀取失敗 with 重試', () => {
    open(ten());
    key('1');
    apply().flush(refused(1, 'candidate_changed'));
    reread().flush(inbox(ten()));
    detect();
    button(el, '重新讀取').click();
    detect();
    reread().flush('boom', { status: 500, statusText: 'Server Error' });
    detect();
    expect(text(el.querySelector('.flow-error--read span'))).toBe('清單讀取失敗');
    button(el, '重試').click();
    detect();
    reread().flush(inbox(ten()));
    detect();
    expect(el.querySelector('.flow-error')).toBeNull();
  });

  it('every refusal is in plain words: no raw code, no 對象已變', () => {
    const codes = [
      'stale_case', 'candidate_changed', 'case_moved', 'not_offered', 'not_conserved', 'protected', 'owner_may_record',
      'historical_statement', 'locked_until_cutover', 'entry_claimed', 'entry_in_group', 'line_outside_period',
      'no_line', 'action_busy', 'some_new_code',
    ];
    for (const code of codes) {
      fixture?.destroy();
      setup();
      open(ten());
      key('1');
      apply().flush(refused(1, code));
      reread().flush(inbox(ten()));
      detect();
      const shown = text(el.querySelector('.flow-error[role="alert"] span'));
      expect(shown, code).not.toMatch(/[a-z_]{4,}|對象已變|cutover/);
      expect(shown.length, code).toBeGreaterThan(4);
    }
  });

  it('refused, second half (kept from the old test): the next press carries the fresh version', () => {
    open(ten());
    key('1');
    apply().flush(refused(1, 'not_conserved'));
    reread().flush(inbox([line(1, { case_version: 2 }), ...ten().slice(1)]));
    detect();
    expect(text(el.querySelector('.flow-error[role="alert"]'))).toBe('沒成功：金額對不上，不能直接對到這筆');
    expect(el.querySelector('.flow-error .flow-retry')).toBeNull();
    expect(headline()).toBe('測試商家 1 −$101');
    expect(progressText()).toBe('第 1 / 10 筆');
    // The next press carries the fresh version.
    key('1');
    expect((apply().request.body as InboxApplyBody).cases).toEqual([{ case_id: 1, case_version: 2 }]);
  });

  it('an HTTP error on apply: inline 沒成功 and no re-read', () => {
    open(ten());
    key('1');
    apply().flush({ detail: 'action_busy: try later' }, { status: 409, statusText: 'Conflict' });
    detect();
    expect(text(el.querySelector('.flow-error[role="alert"]'))).toBe('沒成功：系統正在處理這張帳單，等一下再試');
    expect(headline()).toBe('測試商家 1 −$101');
    expect(answers()[0].disabled).toBe(false);
  });

  it('an HTTP 409 case_closed on apply: 這筆已經處理過了 and a re-read that moves on', () => {
    open(ten());
    key('1');
    apply().flush({ detail: 'case_closed' }, { status: 409, statusText: 'Conflict' });
    detect();
    expect(TestBed.inject(ToastService).current()?.text).toBe('這筆已經處理過了');
    reread().flush(inbox(ten().slice(1)));
    detect();
    expect(headline()).toBe('測試商家 2 −$102');
    expect(el.querySelector('.flow-error')).toBeNull();
  });

  it('re-read fails: 答案已送出，但清單讀取失敗; 重試 sends a GET and no POST', () => {
    open(ten());
    key('1');
    apply().flush(applied(1));
    reread().flush('boom', { status: 500, statusText: 'Server Error' });
    detect();
    const alert = el.querySelector('.flow-error[role="alert"]')!;
    expect(text(alert)).toContain('答案已送出，但清單讀取失敗');
    // The 復原 is already offered: closing the flow now does not lose it.
    expect(TestBed.inject(ToastService).current()).toEqual(
      expect.objectContaining({ kind: 'done', text: '已處理：忽略', action: expect.objectContaining({ label: '復原' }) }),
    );
    // The answer is in: pressing again must not post it twice, and keys that do nothing are not consumed.
    for (const k of ['1', '2', 's', 'ArrowRight']) {
      expect(key(k).defaultPrevented).toBe(false);
    }
    expect(headline()).toBe('測試商家 1 −$101');
    button(el, '重試').click();
    detect();
    reread().flush(inbox(ten().slice(1)));
    detect();
    expect(headline()).toBe('測試商家 2 −$102');
    expect(el.querySelector('.flow-error')).toBeNull();
    expect(TestBed.inject(ToastService).current()).toEqual(
      expect.objectContaining({ kind: 'done', text: '已處理：忽略', action: expect.objectContaining({ label: '復原' }) }),
    );
  });

  it('refused case_closed and gone after the re-read: plain 這筆已經處理過了 toast, then the next question', () => {
    open(ten());
    key('1');
    apply().flush(refused(1, 'case_closed'));
    reread().flush(inbox(ten().slice(1)));
    detect();
    expect(headline()).toBe('測試商家 2 −$102');
    expect(el.querySelector('.flow-error')).toBeNull();
    expect(TestBed.inject(ToastService).current()).toEqual(
      expect.objectContaining({ kind: 'done', text: '這筆已經處理過了', action: null }),
    );
  });

  it('a revert read in flight when an answer is pressed is cancelled; busy holds until the answer re-read', () => {
    open(ten());
    answerWith1(1, ten().slice(1));
    const toasts = TestBed.inject(ToastService);
    undoLast(901);
    const stale = reread();
    key('1');
    const post = apply();
    expect(stale.cancelled).toBe(true);
    expect(answers().every((b) => b.disabled)).toBe(true);
    answers()[0].click();
    detect();
    http.expectNone((r) => r.method === 'POST' && r.url === APPLY_URL);
    post.flush(applied(2));
    reread().flush(inbox([fresh(1, 11), ...ten().slice(2)]));
    detect();
    // The queued 復原 target is shown, and the second answer got its own 復原.
    expect(headline()).toBe('測試商家 1 −$101');
    expect(answers()[0].disabled).toBe(false);
    expect(toasts.current()).toEqual(
      expect.objectContaining({ kind: 'done', text: '已處理：忽略', action: expect.objectContaining({ label: '復原' }) }),
    );
    toasts.runAction(toasts.current()!.id);
    http.expectOne('/api/accounting/reconciliation/actions/902/revert');
  });

  it('a revert read pending when the apply fails is restarted so the case still shows', () => {
    open(ten());
    answerWith1(1, ten().slice(1));
    undoLast(901);
    const stale = reread();
    key('1');
    expect(stale.cancelled).toBe(true);
    apply().flush({ detail: 'case_closed' }, { status: 409, statusText: 'Conflict' });
    detect();
    reread().flush(inbox([fresh(1, 11), ...ten().slice(1)]));
    detect();
    expect(headline()).toBe('測試商家 1 −$101');
  });

  it('rule_error: advances and raises the 規則沒存 error toast instead of 完成', () => {
    open(ten());
    switchEl()!.click();
    detect();
    key('1');
    apply().flush({ ...applied(1), rule_error: 'rule_pattern_empty' });
    reread().flush(inbox(ten().slice(1)));
    detect();
    expect(headline()).toBe('測試商家 2 −$102');
    expect(TestBed.inject(ToastService).current()).toEqual(
      expect.objectContaining({ kind: 'error', text: '答案已記下，但規則沒存：描述只有數字，沒辦法記住', action: null }),
    );
  });

  describe('learn', () => {
    it('hides the switch without a rule_hint', () => {
      open([line(1, { rule_hint: null })]);
      expect(switchEl()).toBeNull();
    });

    it('on + 忽略 on bank_only sends amount_exact true; the switch is off again on the next step', () => {
      open(ten());
      const sw = switchEl()!;
      expect(sw.getAttribute('aria-checked')).toBe('false');
      expect(text(el.querySelector('.flow-learn-label'))).toBe('以後「測試商家 1」都這樣');
      const sub = el.querySelector('.flow-learn-sub')!;
      // Every answer that will be remembered is named, as its button reads.
      expect(text(sub)).toBe('只有「忽略」「記為支出…」會記住，之後同樣的行自動處理；可在記帳設定關掉');
      expect(sub.id).not.toBe('');
      expect(sw.getAttribute('aria-describedby')).toBe(sub.id);
      expect(sw.getAttribute('aria-label')).toBe('以後「測試商家 1」都這樣');
      sw.click();
      detect();
      expect(switchEl()!.getAttribute('aria-checked')).toBe('true');
      key('1');
      const req = apply();
      expect((req.request.body as InboxApplyBody).learn).toEqual({ amount_exact: true });
      req.flush(applied(1));
      reread().flush(inbox(ten().slice(1)));
      detect();
      expect(switchEl()!.getAttribute('aria-checked')).toBe('false');
    });

    it('on + 改成銀行的 sends amount_exact false', () => {
      open([differs(1)]);
      switchEl()!.click();
      detect();
      key('1');
      expect((apply().request.body as InboxApplyBody).learn).toEqual({ amount_exact: false });
    });

    it('on + a non-learnable answer sends learn null', () => {
      open([differs(1)]);
      switchEl()!.click();
      detect();
      key('3');
      expect((apply().request.body as InboxApplyBody).learn).toBeNull();
    });

    it('sits before the answers at every width', () => {
      const order = (a: Element, b: Element) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
      for (const mode of ['phone', 'sheet', 'panes'] as const) {
        open(ten(), mode);
        expect(order(el.querySelector('.flow-learn')!, el.querySelector('.flow-answers')!), mode).toBeTruthy();
        fixture.destroy();
      }
    });

    it('names the one answer it applies to on an amount question, and tags the answers that learn while on', () => {
      open([differs(1)]);
      expect(text(el.querySelector('.flow-learn-sub'))).toBe(
        '只有「改成銀行的 −$2,096」會記住，之後同樣的行自動處理；可在記帳設定關掉',
      );
      const tags = () => answers().map((b) => text(b.querySelector('.flow-answer-learns')));
      expect(tags()).toEqual(['', '', '']);
      switchEl()!.click();
      detect();
      expect(tags()).toEqual(['會記住', '', '']);
    });

    it('the record sheet tick is the switch: a tick changed in the sheet is written back, also after 取消', () => {
      open([line(1, { actions: ['record', 'match_candidate', 'dismiss'] })]);
      expect(switchEl()!.getAttribute('aria-checked')).toBe('false');
      key('1');
      http.expectOne((r) => r.url === '/api/accounting/categories').flush([]);
      http.expectOne((r) => r.url === '/api/accounting/projects').flush([]);
      detect();
      const sheet = el.querySelector<HTMLElement>('app-inbox-record-sheet')!;
      const box = sheet.querySelector<HTMLInputElement>('.learn input')!;
      expect(box.checked).toBe(false);
      box.click();
      detect();
      expect(switchEl()!.getAttribute('aria-checked')).toBe('true');
      button(sheet, '取消').click();
      detect();
      expect(el.querySelector('app-inbox-record-sheet')).toBeNull();
      expect(switchEl()!.getAttribute('aria-checked')).toBe('true');
      // Opened again it carries the switch; unticking there turns the switch off.
      key('1');
      http.expectOne((r) => r.url === '/api/accounting/categories').flush([]);
      http.expectOne((r) => r.url === '/api/accounting/projects').flush([]);
      detect();
      const again = el.querySelector<HTMLElement>('app-inbox-record-sheet .learn input') as HTMLInputElement;
      expect(again.checked).toBe(true);
      again.click();
      detect();
      expect(switchEl()!.getAttribute('aria-checked')).toBe('false');
    });
  });

  describe('record / match', () => {
    it('記為支出… opens the record sheet with the switch state; 確定 posts record with the sheet learn', () => {
      open([line(1, { actions: ['record', 'match_candidate', 'dismiss'] })]);
      switchEl()!.click();
      detect();
      key('1');
      http
        .expectOne((r) => r.url === '/api/accounting/categories' && r.params.get('kind') === 'expense')
        .flush([makeCategory({ id: 31, name: '測試餐飲' })]);
      http.expectOne((r) => r.url === '/api/accounting/projects').flush([]);
      detect();
      const sheet = el.querySelector<HTMLElement>('app-inbox-record-sheet')!;
      expect(sheet.querySelector<HTMLInputElement>('.learn input')!.checked).toBe(true);
      categoryTile(sheet, '測試餐飲').click();
      detect();
      button(sheet, '確定').click();
      detect();
      expect(el.querySelector('app-inbox-record-sheet')).toBeNull();
      expect(apply().request.body as InboxApplyBody).toEqual({
        cases: [{ case_id: 1, case_version: 1 }],
        action: 'record',
        params: { category_id: 31 },
        answer: '記為支出 測試餐飲',
        learn: { amount_exact: true },
      });
    });

    it('對到… posts match_candidate with entry_ids', () => {
      open([
        line(1, {
          actions: ['match_candidate', 'dismiss'],
          candidates: [
            {
              entry_id: 77,
              date: '2026-09-03',
              title: '測試早餐',
              amount: '-101.0000',
              reason: null,
              residual: null,
              date_distance: 0,
            },
          ],
        }),
      ]);
      answers()[0].click();
      detect();
      const sheet = el.querySelector<HTMLElement>('app-inbox-match-sheet')!;
      sheet.querySelector<HTMLInputElement>('input[type="radio"]')!.click();
      detect();
      button(sheet, '確定').click();
      detect();
      expect(apply().request.body as InboxApplyBody).toEqual({
        cases: [{ case_id: 1, case_version: 1 }],
        action: 'match_candidate',
        params: { entry_ids: { '1': 77 } },
        answer: '對到 測試早餐',
        learn: null,
      });
    });
  });

  it('對到… on an amount question never offers the entry being compared, in the candidates or the search', () => {
    const cand = (entry_id: number, title: string) => ({
      entry_id,
      date: '2026-09-12',
      title,
      amount: '-2066.0000',
      reason: null,
      residual: null,
      date_distance: 0,
    });
    // As the server sends it without `entry`: the compared entry is the first candidate.
    open([differs(1, { entry: null, candidates: [cand(500, '測試服飾'), cand(501, '測試另一筆')] })]);
    key('2');
    detect();
    const sheet = el.querySelector<HTMLElement>('app-inbox-match-sheet')!;
    const rows = () => Array.from(sheet.querySelectorAll('label')).map(text);
    expect(rows().length).toBe(1);
    expect(rows()[0]).toContain('測試另一筆');
    button(sheet, '搜尋這個帳戶的記錄').click();
    http
      .expectOne((r) => r.url === '/api/accounting/reconciliation/inbox/cases/1/search')
      .flush([cand(500, '測試服飾'), cand(502, '測試第三筆')]);
    detect();
    expect(rows().length).toBe(2);
    expect(rows().join()).not.toContain('測試服飾');
  });

  it('part-of: 記為支出… goes last, and the note links 打開那筆 to the recorded entry', () => {
    open([
      line(1, {
        actions: ['record', 'match_candidate', 'dismiss'],
        part_of: [{ id: 700, date: '2026-09-01', title: '測試還款', amount: '-5000.0000', account_id: 1 }],
        rule_hint: null,
      }),
    ]);
    expect(answers().map((b) => text(b.querySelector('.flow-answer-label')))).toEqual([
      '已記在別處，對到…',
      '忽略',
      '記為支出…',
    ]);
    const link = el.querySelector<HTMLAnchorElement>('.flow-note a')!;
    expect(text(link)).toBe('打開那筆');
    expect(link.getAttribute('href')).toBe('/accounting/entries/700');
    // The keys follow the order shown.
    key('3');
    detect();
    expect(el.querySelector('app-inbox-record-sheet')).not.toBeNull();
    http.expectOne((r) => r.url === '/api/accounting/categories').flush([]);
    http.expectOne((r) => r.url === '/api/accounting/projects').flush([]);
  });

  it('the key hint names the digits the step has', () => {
    const hint = () => text(el.querySelector('.flow-hint'));
    open([line(1, { actions: ['match_candidate', 'dismiss'] })]);
    expect(answers().length).toBe(2);
    expect(hint()).toBe('1–2 選擇 · S 略過 · ← → 上下筆 · Esc 關閉');
    fixture.destroy();
    open([line(1, { actions: ['dismiss'] })]);
    expect(hint()).toBe('1 選擇 · S 略過 · ← → 上下筆 · Esc 關閉');
  });

  describe('keys', () => {
    it('s skips, arrows move without a request, Escape emits closed', () => {
      open(ten());
      const event = key('s');
      expect(event.defaultPrevented).toBe(true);
      expect(headline()).toBe('測試商家 2 −$102');
      key('ArrowRight');
      expect(headline()).toBe('測試商家 3 −$103');
      key('ArrowLeft');
      key('ArrowLeft');
      expect(headline()).toBe('測試商家 1 −$101');
      // Nothing before the first step: ← does nothing, so it is not consumed.
      expect(key('ArrowLeft').defaultPrevented).toBe(false);
      key('Escape');
      expect(closed).toHaveBeenCalledTimes(1);
    });

    it('does nothing with focus in an <input>', () => {
      open(ten());
      const input = document.createElement('input');
      input.className = 'spec-extra';
      document.body.appendChild(input);
      input.focus();
      key('1', input);
      key('s', input);
      const escape = key('Escape', input);
      expect(escape.defaultPrevented).toBe(false);
      expect(headline()).toBe('測試商家 1 −$101');
      expect(closed).not.toHaveBeenCalled();
    });

    it('does nothing while a sheet is open', () => {
      open([line(1, { actions: ['record', 'dismiss'] }), line(2)]);
      key('1');
      http.expectOne((r) => r.url === '/api/accounting/categories').flush([]);
      http.expectOne((r) => r.url === '/api/accounting/projects').flush([]);
      detect();
      expect(el.querySelector('app-inbox-record-sheet')).not.toBeNull();
      key('2');
      key('s');
      key('Escape');
      expect(closed).not.toHaveBeenCalled();
      expect(headline()).toBe('測試商家 1 −$101');
    });

    it('ignores auto-repeat for answers and skip; arrows may repeat', () => {
      open(ten());
      expect(key('1', document, { repeat: true }).defaultPrevented).toBe(false);
      expect(key('s', document, { repeat: true }).defaultPrevented).toBe(false);
      expect(headline()).toBe('測試商家 1 −$101');
      http.expectNone((r) => r.method === 'POST');
      expect(key('ArrowRight', document, { repeat: true }).defaultPrevented).toBe(true);
      expect(headline()).toBe('測試商家 2 −$102');
    });

    it('does not consume 3 on a two-answer step', () => {
      open([line(1, { actions: ['dismiss', 'record'] })]);
      expect(key('3').defaultPrevented).toBe(false);
      http.expectNone((r) => r.method === 'POST');
    });

    it('→ and S before the apply answer are ignored and not consumed', () => {
      open(ten());
      key('1');
      const post = apply();
      expect(key('ArrowRight').defaultPrevented).toBe(false);
      expect(key('S').defaultPrevented).toBe(false);
      expect(headline()).toBe('測試商家 1 −$101');
      post.flush(applied(1));
      reread().flush(inbox(ten().slice(1)));
      detect();
      expect(headline()).toBe('測試商家 2 −$102');
    });

    it('does nothing with focus in a contenteditable or a [role=combobox]', () => {
      open(ten());
      const editable = document.createElement('div');
      editable.className = 'spec-extra';
      editable.setAttribute('contenteditable', 'true');
      editable.tabIndex = 0;
      const inner = document.createElement('span');
      inner.tabIndex = 0;
      editable.appendChild(inner);
      const combo = document.createElement('span');
      combo.className = 'spec-extra';
      combo.setAttribute('role', 'combobox');
      combo.tabIndex = 0;
      document.body.append(editable, combo);
      for (const target of [editable, inner, combo]) {
        target.focus();
        expect(document.activeElement).toBe(target);
        expect(key('1', target).defaultPrevented).toBe(false);
        expect(key('s', target).defaultPrevented).toBe(false);
      }
      http.expectNone((r) => r.method === 'POST');
      expect(headline()).toBe('測試商家 1 −$101');
    });

    it('does nothing while a [role=dialog] is open elsewhere in the page', () => {
      open(ten());
      const dialog = document.createElement('div');
      dialog.className = 'spec-extra';
      dialog.setAttribute('role', 'dialog');
      document.body.appendChild(dialog);
      expect(key('1').defaultPrevented).toBe(false);
      expect(key('s').defaultPrevented).toBe(false);
      key('Escape');
      expect(closed).not.toHaveBeenCalled();
      http.expectNone((r) => r.method === 'POST');
      expect(headline()).toBe('測試商家 1 −$101');
    });

    it('a defaultPrevented Escape does not close', () => {
      open(ten());
      const other = document.createElement('div');
      other.className = 'spec-extra';
      other.addEventListener('keydown', (e) => e.preventDefault());
      document.body.appendChild(other);
      key('Escape', other);
      expect(closed).not.toHaveBeenCalled();
    });
  });

  describe('end state', () => {
    it('after the last question is skipped: 已處理 4 筆，還有 6 筆; 繼續對帳 returns to the first open question', () => {
      open(ten());
      let rest = ten();
      for (const id of [1, 2, 3, 4]) {
        rest = rest.slice(1);
        answerWith1(id, rest);
      }
      for (let i = 0; i < 6; i += 1) key('s');
      const end = el.querySelector('.flow-end')!;
      expect(text(end.querySelector('h2'))).toBe('已處理 4 筆，還有 6 筆');
      expect(text(end)).toContain('剩下的會留在通知裡，帳單對完才會從數字中拿掉。');
      expect(progressText()).toBe('4 / 10 筆');
      expect(document.activeElement).toBe(end.querySelector('h2'));
      button(end, '回到通知').click();
      expect(closed).toHaveBeenCalledTimes(1);
      button(end, '繼續對帳').click();
      detect();
      expect(headline()).toBe('測試商家 5 −$105');
      expect(progressText()).toBe('第 5 / 10 筆');
    });

    it('all answered: 這張帳單都決定好了', () => {
      open([line(1), line(2)]);
      answerWith1(1, [line(2)]);
      answerWith1(2, []);
      const end = el.querySelector('.flow-end')!;
      expect(text(end.querySelector('h2'))).toBe('這張帳單都決定好了');
      expect(button(end, '回到通知')).toBeTruthy();
      expect(button(end, '繼續對帳')).toBeUndefined();
      // Nothing left to decide: the header does not count questions any more.
      expect(el.querySelector('.flow-sub')).toBeNull();
    });

    it('all answered with the statement progress known: only 已對上 k / n 行', () => {
      tasks.set([{ id: `recon:${STATEMENT}`, progress: { k: 28, n: 28 } } as unknown as NotifTask]);
      open([line(1)]);
      answerWith1(1, []);
      expect(text(el.querySelector('.flow-sub'))).toBe('已對上 28 / 28 行');
    });

    it('stale link: no ask item for the statement → 這張帳單沒有要你決定的了', () => {
      render();
      firstRead().flush(inbox([line(1, { statement: { id: 99, period_end: '2026-08-31' } })]));
      detect();
      expect(text(el.querySelector('.flow-end h2'))).toBe('這張帳單沒有要你決定的了');
      expect(text(el)).not.toContain('這次要你決定');
      expect(button(el.querySelector('.flow-end')!, '回到通知')).toBeTruthy();
    });

    it('stale link: the account is there but the statement has no ask item', () => {
      render();
      firstRead().flush(
        inbox([line(1, { question: 'proposed', folded: true }), line(2, { statement: { id: 99, period_end: '2026-08-31' } })]),
      );
      detect();
      expect(text(el.querySelector('.flow-kicker'))).toBe('對帳 · 玉山');
      expect(text(el.querySelector('.flow-end h2'))).toBe('這張帳單沒有要你決定的了');
      expect(el.querySelector('.flow-progress')).toBeNull();
    });
  });

  it('undo wiring: 已處理 toast has 復原; when reverted fires the flow re-reads and shows that case again', () => {
    open(ten());
    answerWith1(1, ten().slice(1));
    const toasts = TestBed.inject(ToastService);
    expect(toasts.current()).toEqual(
      expect.objectContaining({ kind: 'done', text: '已處理：忽略', action: expect.objectContaining({ label: '復原' }) }),
    );
    expect(headline()).toBe('測試商家 2 −$102');
    // Focus is outside the flow (the toast's 復原): the background re-read must not pull it in.
    (document.activeElement as HTMLElement | null)?.blur();
    undoLast(901);
    reread().flush(inbox([fresh(1, 11), ...ten().slice(1)]));
    detect();
    expect(headline()).toBe('測試商家 1 −$101');
    expect(progressText()).toBe('第 1 / 10 筆');
    expect(document.activeElement).not.toBe(el.querySelector('h2'));
  });

  it('undo of an answer that taught a rule switches the rule off before the reconcile', () => {
    open(ten());
    switchEl()!.click();
    detect();
    key('1');
    apply().flush({ ...applied(1), rule_id: 55 });
    reread().flush(inbox(ten().slice(1)));
    detect();
    const toasts = TestBed.inject(ToastService);
    toasts.runAction(toasts.current()!.id);
    http.expectOne('/api/accounting/reconciliation/actions/901/revert').flush({});
    const patch = http.expectOne('/api/accounting/reconciliation/owner-rules/55');
    expect(patch.request.body).toEqual({ enabled: false });
    patch.flush({});
    http.expectOne(RECONCILE_URL).flush({});
    detect();
    expect(toasts.current()?.text).toBe('已復原，並停用「測試商家 1」的規則');
    reread().flush(inbox([fresh(1, 11), ...ten().slice(1)]));
    detect();
    expect(headline()).toBe('測試商家 1 −$101');
  });

  describe('復原 brings the question back under a new case id', () => {
    const three = () => [line(1), line(2), line(3)];
    const doneSegments = () => el.querySelectorAll('.flow-bar [data-state="done"]').length;

    it('plain: lands on the returned question at its old place; 第 1 / 3 筆, nothing handled', () => {
      open(three());
      answerWith1(1, three().slice(1));
      expect(progressText()).toBe('第 2 / 3 筆');
      undoLast(901);
      reread().flush(inbox([fresh(1, 11), line(2), line(3)]));
      detect();
      http.expectNone((r) => r.url === INBOX_URL);
      expect(headline()).toBe('測試商家 1 −$101');
      expect(progressText()).toBe('第 1 / 3 筆');
      expect(el.querySelectorAll('.flow-bar [data-seg]').length).toBe(3);
      expect(doneSegments()).toBe(0);
      // Answering it again posts the new case id and version.
      key('1');
      expect((apply().request.body as InboxApplyBody).cases).toEqual([{ case_id: 11, case_version: 1 }]);
    });

    it('a pair answered with 改成銀行的: both return (follower listed first); leader shown with the note, then the follower', () => {
      open([ledgerOnly(2), differs(1), line(3)]);
      key('1');
      apply().flush(applied(1));
      reread().flush(inbox([line(3)]));
      detect();
      expect(progressText()).toBe('第 3 / 3 筆');
      undoLast(901);
      reread().flush(inbox([{ ...ledgerOnly(22), case_version: 1 }, { ...differs(1), case_id: 21, case_version: 1 }, line(3)]));
      detect();
      expect(text(el.querySelector('.flow-chip'))).toBe('金額不同');
      expect(progressText()).toBe('第 1 / 3 筆');
      expect(text(el.querySelector('.flow-pair'))).toBe(
        '你這筆 −$2,066 也列在「你記了，銀行沒有」。這裡對上或改金額後，那一題會自動消失。',
      );
      expect(doneSegments()).toBe(0);
      // 不是同一筆: the leader leaves, the follower is a real question and comes next.
      key('3');
      const req = apply();
      expect((req.request.body as InboxApplyBody).cases).toEqual([{ case_id: 21, case_version: 1 }]);
      req.flush(applied(21));
      reread().flush(inbox([{ ...ledgerOnly(22), case_version: 1 }, line(3)]));
      detect();
      expect(text(el.querySelector('.flow-chip'))).toBe('你記了，銀行沒有');
      expect(progressText()).toBe('第 2 / 3 筆');
    });

    it('from the end state: the returned question is shown directly', () => {
      open([line(1), line(2)]);
      answerWith1(1, [line(2)]);
      answerWith1(2, []);
      expect(text(el.querySelector('.flow-end h2'))).toBe('這張帳單都決定好了');
      undoLast(902);
      reread().flush(inbox([fresh(2, 12)]));
      detect();
      expect(el.querySelector('.flow-end')).toBeNull();
      expect(headline()).toBe('測試商家 2 −$102');
      expect(progressText()).toBe('第 2 / 2 筆');
    });

    it('nothing came back: one read, the cursor stays, the numbers stay', () => {
      open(three());
      answerWith1(1, three().slice(1));
      undoLast(901);
      reread().flush(inbox(three().slice(1)));
      detect();
      http.expectNone((r) => r.url === INBOX_URL);
      expect(headline()).toBe('測試商家 2 −$102');
      expect(progressText()).toBe('第 2 / 3 筆');
      expect(doneSegments()).toBe(1);
    });

    it('while 重試 (re-read failed) is showing: the 復原 read settles it and lands on the returned question', () => {
      open(three());
      key('1');
      apply().flush(applied(1));
      reread().flush('boom', { status: 500, statusText: 'Server Error' });
      detect();
      expect(text(el.querySelector('.flow-error'))).toContain('答案已送出，但清單讀取失敗');
      undoLast(901);
      reread().flush(inbox([fresh(1, 11), line(2), line(3)]));
      detect();
      expect(el.querySelector('.flow-error')).toBeNull();
      expect(headline()).toBe('測試商家 1 −$101');
      expect(progressText()).toBe('第 1 / 3 筆');
      expect(answers()[0].disabled).toBe(false);
    });

    it('a re-read already in flight when the 復原 finishes is superseded: a fresh GET restores the question', () => {
      open([line(1)]);
      key('1');
      apply().flush(applied(1));
      // GET A: the server may have answered it before the undo (an empty list); its response is still on the way.
      const stale = reread();
      undoLast(901);
      expect(stale.cancelled).toBe(true);
      reread().flush(inbox([fresh(1, 11)]));
      detect();
      http.expectNone((r) => r.url === INBOX_URL);
      expect(headline()).toBe('測試商家 1 −$101');
      expect(progressText()).toBe('第 1 / 1 筆');
      expect(el.querySelector('.flow-error')).toBeNull();
      expect(answers()[0].disabled).toBe(false);
    });

    it('a 復原 while the first read is in flight reads again; the stale first read is dropped', () => {
      render();
      const stale = firstRead();
      const undo = TestBed.inject(ReconUndoService);
      undo.offer({ answer: '忽略', caseId: 1, actionIds: [901], accountId: 1, statementId: STATEMENT, rule: null });
      undoLast(901);
      expect(stale.cancelled).toBe(true);
      firstRead().flush(inbox([fresh(1, 11), line(2)]));
      detect();
      http.expectNone((r) => r.url === INBOX_URL);
      expect(headline()).toBe('測試商家 1 −$101');
      expect(progressText()).toBe('第 1 / 2 筆');
    });

    it('a 復原 while a 重試 read is in flight supersedes it', () => {
      open(three());
      key('1');
      apply().flush(applied(1));
      reread().flush('boom', { status: 500, statusText: 'Server Error' });
      detect();
      (el.querySelector('.flow-retry') as HTMLButtonElement).click();
      detect();
      const stale = reread();
      undoLast(901);
      expect(stale.cancelled).toBe(true);
      reread().flush(inbox([fresh(1, 11), line(2), line(3)]));
      detect();
      http.expectNone((r) => r.url === INBOX_URL);
      expect(el.querySelector('.flow-error')).toBeNull();
      expect(headline()).toBe('測試商家 1 −$101');
      expect(progressText()).toBe('第 1 / 3 筆');
    });

    it('the read after a 復原 fails in the end state: 已復原，但清單讀取失敗 + 重試 (a GET only) restores', () => {
      open([line(1)]);
      answerWith1(1, []);
      expect(text(el.querySelector('.flow-end h2'))).toBe('這張帳單都決定好了');
      undoLast(901);
      reread().flush('boom', { status: 500, statusText: 'Server Error' });
      detect();
      const alert = el.querySelector('.flow-end .flow-error--read');
      expect(alert?.getAttribute('role')).toBe('alert');
      expect(text(alert!.querySelector('span'))).toBe('已復原，但清單讀取失敗');
      (alert!.querySelector('.flow-retry') as HTMLButtonElement).click();
      detect();
      http.expectNone((r) => r.method === 'POST');
      reread().flush(inbox([fresh(1, 11)]));
      detect();
      http.expectNone((r) => r.url === INBOX_URL);
      expect(el.querySelector('.flow-error')).toBeNull();
      expect(headline()).toBe('測試商家 1 −$101');
      expect(progressText()).toBe('第 1 / 1 筆');
    });

    it('the read after a 復原 fails mid-list: the same error under the step; 重試 restores', () => {
      open(three());
      answerWith1(1, three().slice(1));
      undoLast(901);
      reread().flush('boom', { status: 500, statusText: 'Server Error' });
      detect();
      const alert = el.querySelector('.flow-error--read');
      expect(alert?.getAttribute('role')).toBe('alert');
      expect(text(alert!.querySelector('span'))).toBe('已復原，但清單讀取失敗');
      expect(answers().every((b) => b.disabled)).toBe(true);
      (alert!.querySelector('.flow-retry') as HTMLButtonElement).click();
      detect();
      http.expectNone((r) => r.method === 'POST');
      reread().flush(inbox([fresh(1, 11), line(2), line(3)]));
      detect();
      expect(el.querySelector('.flow-error')).toBeNull();
      expect(headline()).toBe('測試商家 1 −$101');
      expect(progressText()).toBe('第 1 / 3 筆');
    });

    it('a 復原 of another statement is ignored', () => {
      open(three());
      const undo = TestBed.inject(ReconUndoService);
      undo.offer({ answer: '忽略', caseId: 70, actionIds: [970], accountId: 1, statementId: 99, rule: null });
      const toasts = TestBed.inject(ToastService);
      toasts.runAction(toasts.current()!.id);
      http.expectOne('/api/accounting/reconciliation/actions/970/revert').flush({});
      http.expectOne('/api/accounting/accounts/1/statements/99/reconcile').flush({});
      detect();
      http.expectNone((r) => r.url === INBOX_URL);
      expect(headline()).toBe('測試商家 1 −$101');
    });

    it('a flow with no account yet (a finished statement) restarts its read on a 復原 of its statement', () => {
      render();
      firstRead().flush(inbox([line(1, { statement: { id: 99, period_end: '2026-08-31' } })]));
      detect();
      expect(text(el.querySelector('.flow-end h2'))).toBe('這張帳單沒有要你決定的了');
      const undo = TestBed.inject(ReconUndoService);
      undo.offer({ answer: '忽略', caseId: 1, actionIds: [901], accountId: 1, statementId: STATEMENT, rule: null });
      undoLast(901);
      firstRead().flush(inbox([fresh(1, 11)]));
      detect();
      expect(headline()).toBe('測試商家 1 −$101');
      expect(progressText()).toBe('第 1 / 1 筆');
    });
  });

  it('→ to the last question and answering it wraps to question 1, not the end state', () => {
    open([line(1), line(2), line(3)]);
    key('ArrowRight');
    key('ArrowRight');
    expect(headline()).toBe('測試商家 3 −$103');
    answerWith1(3, [line(1), line(2)]);
    expect(el.querySelector('.flow-end')).toBeNull();
    expect(headline()).toBe('測試商家 1 −$101');
  });

  it('read failure: banner with 重試', () => {
    render();
    firstRead().flush('boom', { status: 500, statusText: 'Server Error' });
    detect();
    const banner = el.querySelector('app-banner')!;
    expect(text(banner)).toContain('對帳資料讀取失敗');
    expect(text(el.querySelector('.flow-kicker'))).toBe('對帳');
    button(banner, '重試').click();
    detect();
    firstRead().flush(inbox(ten()));
    detect();
    expect(el.querySelector('app-banner')).toBeNull();
    expect(headline()).toBe('測試商家 1 −$101');
  });

  describe('the page heading below panes width', () => {
    const h1s = () => Array.from(el.querySelectorAll('h1')).map(text);

    it('loading: an h1 「對帳」 on phone; none at panes width', () => {
      render('phone');
      detect();
      expect(h1s()).toEqual(['對帳']);
      // 對帳 once: no kicker repeating the heading until the account is known.
      expect(el.querySelector('.flow-kicker')).toBeNull();
      firstRead().flush(inbox(ten()));
      detect();
      expect(h1s()).toEqual(['至 9/30 帳單']);

      expect(text(el.querySelector('.flow-kicker'))).toBe('對帳 · 玉山');

      fixture.destroy();
      render('panes');
      detect();
      expect(text(el.querySelector('.flow-kicker'))).toBe('對帳');
      expect(h1s()).toEqual([]);
      firstRead().flush(inbox(ten()));
      detect();
      expect(h1s()).toEqual([]);
      expect(text(el.querySelector('p.flow-title'))).toBe('至 9/30 帳單');
    });

    it('a failed read: an h1 「對帳」 on phone; none at panes width', () => {
      render('phone');
      firstRead().flush('boom', { status: 500, statusText: 'Server Error' });
      detect();
      expect(el.querySelector('app-banner')).not.toBeNull();
      expect(h1s()).toEqual(['對帳']);

      fixture.destroy();
      render('panes');
      firstRead().flush('boom', { status: 500, statusText: 'Server Error' });
      detect();
      expect(h1s()).toEqual([]);
    });

    it('a statement the inbox does not know: an h1 「對帳」 on phone', () => {
      render('phone', 999);
      firstRead().flush(inbox(ten()));
      detect();
      expect(h1s()).toEqual(['對帳']);
    });
  });

  it('drops an older read when statementId changes mid-read', () => {
    render();
    const old = firstRead();
    fixture.componentRef.setInput('statementId', 99);
    detect();
    expect(old.cancelled).toBe(true);
    firstRead().flush(inbox([line(5, { statement: { id: 99, period_end: '2026-08-31' } })]));
    detect();
    expect(headline()).toBe('測試商家 5 −$105');
  });

  describe('transfer and amount answers', () => {
    const toasts = () => TestBed.inject(ToastService);
    const withUndo = (toast: string) =>
      expect.objectContaining({ kind: 'done', text: toast, action: expect.objectContaining({ label: '復原' }) });

    function caseAction(caseId: number): TestRequest {
      return http.expectOne((r) => r.method === 'POST' && r.url === actionsUrl(caseId));
    }

    function labels(): string[] {
      return answers().map((b) => text(b.querySelector('.flow-answer-label')));
    }

    function pickSheet(): HTMLElement | null {
      return el.querySelector<HTMLElement>('app-flow-pick-sheet');
    }

    function pickRows(): string[] {
      return Array.from(pickSheet()!.querySelectorAll('label')).map(text);
    }

    function pickRow(index: number): void {
      pickSheet()!.querySelectorAll<HTMLInputElement>('input[type="radio"]')[index].click();
      detect();
    }

    function confirmPick(): void {
      button(pickSheet()!, '確定').click();
      detect();
    }

    function noRulePatch(): void {
      http.expectNone((r) => r.url.includes('/owner-rules/'));
    }

    it('a transfer leg: key 1 posts adjust_transfer on the case, then 已照帳單改轉帳 with 復原 that reverts it', () => {
      const marked = vi.spyOn(TestBed.inject(AccountingService), 'markEntriesChanged');
      open([leg(1), line(2)]);
      expect(labels()).toEqual(['照帳單改這筆轉帳', '已記在別處，對到…', '不是同一筆']);
      expect(text(el.querySelector('.flow-note'))).toBe('會把這筆轉帳的兩邊都改成 −$2,096，日期改成帳單上的日期。');
      expect(answers()[0].classList).toContain('solid');
      key('1');
      expect(pickSheet()).toBeNull();
      const req = caseAction(1);
      expect(req.request.body).toEqual({ action: 'adjust_transfer', params: { entry_id: 500, line_id: 201 } });
      http.expectNone(APPLY_URL);
      req.flush(took(951, 1, 'adjust_transfer'));
      detect();
      expect(toasts().current()).toEqual(withUndo('已照帳單改轉帳'));
      expect(marked).toHaveBeenCalled();
      reread().flush(inbox([line(2)]));
      detect();
      expect(headline()).toBe('測試商家 2 −$102');
      expect(progressText()).toBe('第 2 / 2 筆');
      expect(el.querySelector('.flow-error')).toBeNull();
      undoLast(951);
      noRulePatch();
      reread().flush(inbox([leg(1), line(2)]));
      detect();
      expect(headline()).toBe('銀行多扣了 $30');
      expect(progressText()).toBe('第 1 / 2 筆');
    });

    it('a transfer leg with its own note: the transfer sentence first, the note on a new line of the same box', () => {
      open([{ ...leg(1), note: '測試備註' }]);
      const notes = el.querySelectorAll('.flow-note');
      expect(notes.length).toBe(1);
      expect(notes[0].textContent).toContain('會把這筆轉帳的兩邊都改成 −$2,096，日期改成帳單上的日期。\n測試備註');
      // The box keeps line breaks (pre-line): the template's own whitespace must not add any.
      expect(notes[0].textContent!.match(/\n/g)?.length).toBe(1);
    });

    it('ambiguous over two legs: 照帳單改轉帳… asks 哪一筆是這筆轉帳？; the picked leg is posted', () => {
      open([ambiguous(1)]);
      key('2');
      expect(text(pickSheet()!.querySelector('h3'))).toBe('哪一筆是這筆轉帳？');
      expect(text(pickSheet()!.querySelector('.pick-sub'))).toBe('兩邊一起改，日期跟帳單');
      expect(pickRows()).toEqual([
        '2026/09/05 測試轉帳 甲 −$19,970 → −$20,000',
        '2026/09/05 測試轉帳 乙 −$19,950 → −$20,000',
      ]);
      http.expectNone((r) => r.method === 'POST');
      pickRow(1);
      confirmPick();
      expect(pickSheet()).toBeNull();
      expect(caseAction(1).request.body).toEqual({ action: 'adjust_transfer', params: { entry_id: 611, line_id: 301 } });
    });

    it('ambiguous with one leg and no entry_id: still asks, one row pre-picked; nothing posts until 確定', () => {
      open([ambiguous(1, [610])]);
      expect(labels()[1]).toBe('照帳單改轉帳…');
      key('2');
      expect(pickRows()).toEqual(['2026/09/05 測試轉帳 甲 −$19,970 → −$20,000']);
      expect(pickSheet()!.querySelector<HTMLInputElement>('input[type="radio"]')!.checked).toBe(true);
      http.expectNone((r) => r.method === 'POST');
      confirmPick();
      expect(caseAction(1).request.body).toEqual({ action: 'adjust_transfer', params: { entry_id: 610, line_id: 301 } });
    });

    it('a transfer with one peer: 配成哪一筆轉帳？ lists 「日期 from → to 金額」; 確定 pairs it and moves on', () => {
      open([needsPeer(1), line(2)]);
      expect(labels()).toEqual(['配成轉帳…', '已記在別處，對到…', '忽略']);
      expect(text(el.querySelector('.flow-note'))).toBe('選「配成轉帳…」把兩個帳戶的這筆對起來。');
      key('1');
      expect(text(pickSheet()!.querySelector('h3'))).toBe('配成哪一筆轉帳？');
      // The peer line is money in (+$20,000): it went from this account (玉山) to the peer.
      expect(pickRows()).toEqual(['2026/09/06 玉山 → 測試銀行 $20,000']);
      confirmPick();
      const req = caseAction(1);
      expect(req.request.body).toEqual({ action: 'pair_transfer', params: { peer_line_id: 801 } });
      req.flush(took(952, 1, 'pair_transfer'));
      reread().flush(inbox([line(2)]));
      detect();
      expect(headline()).toBe('測試商家 2 −$102');
      expect(toasts().current()).toEqual(withUndo('已配成轉帳：測試銀行 9/6'));
    });

    it('a transfer without peers: no 配成轉帳…, the no-candidate hint, two keys', () => {
      open([needsPeer(1, [])]);
      expect(labels()).toEqual(['已記在別處，對到…', '忽略']);
      expect(text(el.querySelector('.flow-note'))).toBe(NO_PEER_HINT);
      expect(text(el.querySelector('.flow-hint'))).toBe('1–2 選擇 · S 略過 · ← → 上下筆 · Esc 關閉');
    });

    it('ambiguous 改金額…: 改哪一筆的金額？ lists only the non-leg candidates and posts adjust_amount', () => {
      open([ambiguous(1, [610])]);
      expect(labels()).toEqual(['已記在別處，對到…', '照帳單改轉帳…', '改金額…', '不是同一筆']);
      key('3');
      expect(text(pickSheet()!.querySelector('h3'))).toBe('改哪一筆的金額？');
      expect(text(pickSheet()!.querySelector('.pick-sub'))).toBe('改成銀行的 −$20,000');
      expect(pickRows()).toEqual([
        '2026/09/05 測試轉帳 乙 −$19,950 → −$20,000',
        '2026/09/05 測試餐費 −$1,250 → −$20,000',
      ]);
      pickRow(1);
      confirmPick();
      expect(caseAction(1).request.body).toEqual({ action: 'adjust_amount', params: { entry_id: 612, line_id: 301 } });
    });

    it('復原 after 配成轉帳: reverts the action, no rule switch; the question is found again under its new case id', () => {
      open([needsPeer(1), line(2)]);
      key('1');
      confirmPick();
      caseAction(1).flush(took(952, 1, 'pair_transfer'));
      reread().flush(inbox([line(2)]));
      detect();
      undoLast(952);
      noRulePatch();
      reread().flush(inbox([{ ...needsPeer(1), case_id: 31 }, line(2)]));
      detect();
      expect(text(el.querySelector('.flow-chip'))).toBe('轉帳／繳費');
      expect(progressText()).toBe('第 1 / 2 筆');
      expect(el.querySelectorAll('.flow-bar [data-state="done"]').length).toBe(0);
      key('1');
      confirmPick();
      caseAction(31);
    });

    it('復原 after 改金額: reverts the action, no rule switch; the question is found again under its new case id', () => {
      open([ambiguous(1), line(2)]);
      key('3');
      confirmPick();
      caseAction(1).flush(took(953, 1, 'adjust_amount'));
      detect();
      expect(toasts().current()).toEqual(withUndo('已處理：改金額：測試餐費'));
      reread().flush(inbox([line(2)]));
      detect();
      undoLast(953);
      noRulePatch();
      reread().flush(inbox([{ ...ambiguous(1), case_id: 41 }, line(2)]));
      detect();
      expect(headline()).toBe('有 3 筆可能是它');
      expect(progressText()).toBe('第 1 / 2 筆');
      key('4');
      expect((apply().request.body as InboxApplyBody).cases).toEqual([{ case_id: 41, case_version: 1 }]);
    });

    it('the action went in but the re-read fails: 重試 is one GET and no second POST', () => {
      open([leg(1), line(2)]);
      key('1');
      caseAction(1).flush(took(951, 1, 'adjust_transfer'));
      reread().flush('boom', { status: 500, statusText: 'Server Error' });
      detect();
      expect(text(el.querySelector('.flow-error--read span'))).toBe('答案已送出，但清單讀取失敗');
      expect(toasts().current()).toEqual(withUndo('已照帳單改轉帳'));
      expect(key('1').defaultPrevented).toBe(false);
      button(el, '重試').click();
      detect();
      http.expectNone((r) => r.method === 'POST');
      reread().flush(inbox([line(2)]));
      detect();
      expect(headline()).toBe('測試商家 2 −$102');
      expect(el.querySelector('.flow-error')).toBeNull();
    });

    it('a case action still open after the re-read shows no refusal', () => {
      open([leg(1)]);
      key('1');
      caseAction(1).flush(took(951, 1, 'adjust_transfer'));
      reread().flush(inbox([leg(1)]));
      detect();
      expect(el.querySelector('.flow-error')).toBeNull();
      expect(answers()[0].disabled).toBe(false);
    });

    it('a 復原 while the action re-read is in flight supersedes it: a fresh GET restores the question', () => {
      open([leg(1)]);
      key('1');
      caseAction(1).flush(took(951, 1, 'adjust_transfer'));
      const stale = reread();
      undoLast(951);
      expect(stale.cancelled).toBe(true);
      reread().flush(inbox([leg(1)]));
      detect();
      http.expectNone((r) => r.url === INBOX_URL);
      expect(headline()).toBe('銀行多扣了 $30');
      expect(el.querySelector('.flow-error')).toBeNull();
      expect(answers()[0].disabled).toBe(false);
    });

    it('a late answer after the flow closed: the toast still shows, no re-read', () => {
      open([leg(1)]);
      key('1');
      const post = caseAction(1);
      fixture.destroy();
      post.flush(took(951, 1, 'adjust_transfer'));
      expect(toasts().current()).toEqual(withUndo('已照帳單改轉帳'));
      http.expectNone((r) => r.url === INBOX_URL);
    });

    it('a late answer after the flow moved to another statement: the toast shows, the new flow is untouched', () => {
      open([leg(1)]);
      key('1');
      const post = caseAction(1);
      fixture.componentRef.setInput('statementId', 99);
      detect();
      firstRead().flush(inbox([line(5, { statement: { id: 99, period_end: '2026-08-31' } })]));
      detect();
      post.flush(took(951, 1, 'adjust_transfer'));
      detect();
      expect(toasts().current()).toEqual(withUndo('已照帳單改轉帳'));
      http.expectNone((r) => r.url === INBOX_URL);
      expect(headline()).toBe('測試商家 5 −$105');
      expect(answers()[0].disabled).toBe(false);
    });

    it('refused 409 candidate_changed: 這一題的資料剛剛變了 with 重新讀取; no re-read', () => {
      open([leg(1)]);
      key('1');
      caseAction(1).flush({ detail: 'candidate_changed: moved' }, { status: 409, statusText: 'Conflict' });
      detect();
      http.expectNone((r) => r.url === INBOX_URL);
      const alert = el.querySelector('.flow-error[role="alert"]')!;
      expect(text(alert.querySelector('span'))).toBe('這一題的資料剛剛變了');
      expect(button(alert, '重新讀取')).toBeTruthy();
      expect(answers()[0].disabled).toBe(false);
    });

    it('refused 422 peer_leg_claimed: a plain sentence, no 重新讀取, no re-read', () => {
      open([needsPeer(1)]);
      key('1');
      confirmPick();
      caseAction(1).flush(
        { detail: [{ loc: ['params'], msg: 'peer_leg_claimed: line 801 is paired' }] },
        { status: 422, statusText: 'Unprocessable Entity' },
      );
      detect();
      http.expectNone((r) => r.url === INBOX_URL);
      const alert = el.querySelector('.flow-error[role="alert"]')!;
      expect(text(alert)).toBe('沒成功：另一個帳戶的那一筆已經配給別的轉帳了');
      expect(alert.querySelector('button')).toBeNull();
    });

    it('refused 409 case_closed: 這筆已經處理過了 and a re-read that moves on (answered elsewhere)', () => {
      open([leg(1), line(2)]);
      key('1');
      caseAction(1).flush({ detail: 'case_closed: case 1 is closed' }, { status: 409, statusText: 'Conflict' });
      detect();
      expect(toasts().current()?.text).toBe('這筆已經處理過了');
      expect(answers()[0].disabled).toBe(true);
      reread().flush(inbox([line(2)]));
      detect();
      expect(headline()).toBe('測試商家 2 −$102');
      expect(el.querySelector('.flow-error')).toBeNull();
      expect(answers()[0].disabled).toBe(false);
    });

    it('busy: keys and answers are off until the re-read lands', () => {
      open([leg(1), line(2)]);
      key('1');
      const post = caseAction(1);
      expect(answers().every((b) => b.disabled)).toBe(true);
      expect(key('2').defaultPrevented).toBe(false);
      post.flush(took(951, 1, 'adjust_transfer'));
      detect();
      expect(answers().every((b) => b.disabled)).toBe(true);
      expect(key('s').defaultPrevented).toBe(false);
      http.expectNone((r) => r.method === 'POST');
      reread().flush(inbox([line(2)]));
      detect();
      expect(answers()[0].disabled).toBe(false);
    });

    it('a 復原 read in flight is cancelled by the press and issued again after a refusal', () => {
      open([line(1), leg(2)]);
      answerWith1(1, [leg(2)]);
      undoLast(901);
      const stale = reread();
      key('1');
      const post = caseAction(2);
      expect(stale.cancelled).toBe(true);
      post.flush({ detail: 'action_busy: try later' }, { status: 409, statusText: 'Conflict' });
      detect();
      expect(text(el.querySelector('.flow-error[role="alert"]'))).toBe('沒成功：系統正在處理這張帳單，等一下再試');
      reread().flush(inbox([fresh(1, 11), leg(2)]));
      detect();
      expect(headline()).toBe('測試商家 1 −$101');
    });

    it('four answers: key 4 answers the fourth, the hint reads 1–4 選擇', () => {
      open([ambiguous(1)]);
      expect(labels()).toEqual(['已記在別處，對到…', '照帳單改轉帳…', '改金額…', '不是同一筆']);
      expect(answers()[0].classList).toContain('solid');
      expect(answers().map((b) => text(b.querySelector('kbd')))).toEqual(['1', '2', '3', '4']);
      expect(answers().map((b) => b.getAttribute('aria-keyshortcuts'))).toEqual(['1', '2', '3', '4']);
      expect(text(el.querySelector('.flow-hint'))).toBe('1–4 選擇 · S 略過 · ← → 上下筆 · Esc 關閉');
      expect(key('4').defaultPrevented).toBe(true);
      expect((apply().request.body as InboxApplyBody).action).toBe('dismiss');
    });

    it('a held key does not answer', () => {
      open([leg(1)]);
      expect(key('1', document, { repeat: true }).defaultPrevented).toBe(false);
      http.expectNone((r) => r.method === 'POST');
    });

    it('while the picker is open the flow keys do nothing; Esc closes only the sheet', () => {
      open([ambiguous(1), line(2)]);
      key('2');
      for (const k of ['1', '2', '3', '4', 's', 'ArrowRight', 'ArrowLeft']) {
        expect(key(k).defaultPrevented, k).toBe(false);
      }
      expect(pickSheet()).not.toBeNull();
      expect(headline()).toBe('有 3 筆可能是它');
      http.expectNone((r) => r.method === 'POST');
      const escape = key('Escape', pickSheet()!.querySelector('.sheet')!);
      expect(escape.defaultPrevented).toBe(true);
      expect(pickSheet()).toBeNull();
      expect(closed).not.toHaveBeenCalled();
      expect(headline()).toBe('有 3 筆可能是它');
      // 取消 also only closes the sheet.
      key('3');
      button(pickSheet()!, '取消').click();
      detect();
      expect(pickSheet()).toBeNull();
      http.expectNone((r) => r.method === 'POST');
    });
  });
});
