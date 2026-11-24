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
import { InboxApplyBody, InboxApplyOut, InboxItem, InboxOut } from '../../accounting/inbox/inbox.model';
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

describe('ReconFlowComponent', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<ReconFlowComponent>;
  let el: HTMLElement;
  let closed: ReturnType<typeof vi.fn<() => void>>;
  const tasks = signal<readonly NotifTask[]>([]);

  beforeEach(() => {
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
  });

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

  it('refused: inline 沒成功 and still on the same question after the re-read', () => {
    open(ten());
    key('1');
    apply().flush(refused(1, 'stale_case'));
    reread().flush(inbox([line(1, { case_version: 2 }), ...ten().slice(1)]));
    detect();
    expect(text(el.querySelector('.flow-error[role="alert"]'))).toBe('沒成功：對象已變，重新整理');
    expect(headline()).toBe('測試商家 1 −$101');
    expect(progressText()).toBe('第 1 / 10 筆');
    // The next press carries the fresh version.
    key('1');
    expect((apply().request.body as InboxApplyBody).cases).toEqual([{ case_id: 1, case_version: 2 }]);
  });

  it('an HTTP error on apply: inline 沒成功 and no re-read', () => {
    open(ten());
    key('1');
    apply().flush({ detail: 'case_closed' }, { status: 409, statusText: 'Conflict' });
    detect();
    expect(text(el.querySelector('.flow-error[role="alert"]'))).toBe('沒成功：已經處理過了');
    expect(headline()).toBe('測試商家 1 −$101');
    expect(answers()[0].disabled).toBe(false);
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
      expect.objectContaining({ kind: 'error', text: '答案已記下，但規則沒存：描述只有數字，不能學', action: null }),
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
      expect(text(sub)).toBe('之後同樣的行自動處理，可在記帳設定關掉');
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

    it('sits above the answers on phone and below them at panes width', () => {
      open(ten(), 'phone');
      const order = (a: Element, b: Element) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
      expect(order(el.querySelector('.flow-learn')!, el.querySelector('.flow-answers')!)).toBeTruthy();
      fixture.destroy();
      open(ten(), 'panes');
      expect(order(el.querySelector('.flow-answers')!, el.querySelector('.flow-learn')!)).toBeTruthy();
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
    });

    it('stale link: no ask item for the statement → 這張帳單沒有要你決定的了', () => {
      render();
      firstRead().flush(inbox([line(1, { statement: { id: 99, period_end: '2026-08-31' } })]));
      detect();
      expect(text(el.querySelector('.flow-end h2'))).toBe('這張帳單沒有要你決定的了');
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
      firstRead().flush(inbox(ten()));
      detect();
      expect(h1s()).toEqual(['至 9/30 帳單']);

      fixture.destroy();
      render('panes');
      detect();
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
});
