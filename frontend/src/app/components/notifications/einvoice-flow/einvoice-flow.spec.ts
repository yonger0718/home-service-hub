import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountingService } from '../../../services/accounting.service';
import { ToastService } from '../../../services/toast.service';
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { EntryInput } from '../../../models/accounting.model';
import { InboxEinvoice } from '../../accounting/inbox/einvoice.model';
import { InboxCounts } from '../../accounting/inbox/inbox.model';
import { makeEinvoice, makeEinvoiceTarget, makeInbox, text } from '../../accounting/inbox/testing';
import { EinvoiceFlowComponent } from './einvoice-flow';
import { EinvoiceFlowSession, startFlow } from './einvoice-flow-session';

const INBOX_URL = '/api/accounting/reconciliation/inbox';
const EINVOICE_URL = '/api/accounting/einvoices';

/** The spec scenario's invoice: ambiguous 測試咖啡 $85 on 10/22, −85 on 10/22 (entry 11) and −85 on 10/21 (entry 12). */
function coffee(id = 1, over: Partial<InboxEinvoice> = {}): InboxEinvoice {
  return makeEinvoice({
    id,
    number: `AB0000000${id}`,
    seller_name: '測試咖啡',
    total_amount: '85',
    issue_date: '2026-10-22',
    candidates: [
      makeEinvoiceTarget({ id: 11, amount: '-85', date: '2026-10-22', account_name: '測試 Pi 卡', name: '咖啡' }),
      makeEinvoiceTarget({ id: 12, amount: '-85', date: '2026-10-21', account_name: '現金', name: '咖啡' }),
    ],
    ...over,
  });
}

const THREE = [coffee(1), coffee(2), coffee(3)];

describe('EinvoiceFlowComponent', () => {
  let fixture: ComponentFixture<EinvoiceFlowComponent>;
  let el: HTMLElement;
  let http: HttpTestingController;
  let closed: ReturnType<typeof vi.fn<() => void>>;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [EinvoiceFlowComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    fixture?.destroy();
    document.querySelectorAll('.spec-extra').forEach((node) => node.remove());
    http.verify();
  });

  function detect(): void {
    TestBed.tick();
    fixture.detectChanges();
  }

  function render(mode: LayoutMode = 'panes'): void {
    TestBed.inject(LayoutModeService).set(mode);
    fixture = TestBed.createComponent(EinvoiceFlowComponent);
    closed = vi.fn<() => void>();
    fixture.componentInstance.closed.subscribe(closed);
    el = fixture.nativeElement as HTMLElement;
    detect();
  }

  function read(): TestRequest {
    return http.expectOne((r) => r.method === 'GET' && r.url === INBOX_URL && r.params.get('limit') === '1000');
  }

  function flushRead(invoices: InboxEinvoice[], counts: Partial<InboxCounts> = {}): void {
    detect(); // a write's entriesChanged bump runs the re-read effect
    read().flush({ ...makeInbox([], counts), einvoices: invoices });
    detect();
  }

  function open(invoices: InboxEinvoice[] = THREE, mode: LayoutMode = 'panes', counts: Partial<InboxCounts> = {}): void {
    render(mode);
    flushRead(invoices, counts);
  }

  function key(k: string, target: EventTarget = document, init: KeyboardEventInit = {}): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(event);
    detect();
    return event;
  }

  const progressText = () => text(el.querySelector('.flow-progress-text'));
  const answers = () => Array.from(el.querySelectorAll<HTMLButtonElement>('.einv-answer'));
  const shownNumber = () => text(el.querySelector('.einv-card-meta'));
  const post = (path: string) => http.expectOne((r) => r.method === 'POST' && r.url === `${EINVOICE_URL}/${path}`);

  it('the spec scenario: 有兩筆都像, both candidates with keys 1 and 2, the second marked 日期差 1 天, 3 都不是', () => {
    open();
    expect(text(el.querySelector('.flow-chip'))).toBe('有兩筆都像');
    expect(text(el.querySelector('.einv-reason'))).toBe('金額相同的帳有 2 筆');
    expect(progressText()).toBe('第 1 / 3 張');
    expect(answers().map((b) => text(b.querySelector('kbd')))).toEqual(['1', '2']);
    expect(answers().map((b) => text(b.querySelector('.flow-answer-label')))).toEqual(['連結 咖啡', '連結 咖啡']);
    expect(answers()[0].classList.contains('solid')).toBe(true);
    expect(text(answers()[1].querySelector('.einv-diffs'))).toBe('日期差 1 天');
    expect(answers()[0].querySelector('.einv-diffs')).toBeNull();
    expect(text(el.querySelector('.einv-none kbd'))).toBe('3');
    expect(text(el.querySelector('.einv-card'))).toContain('測試咖啡');
    expect(shownNumber()).toBe('2026/10/22 · AB00000001 · 統編 00000001');
    expect(text(el.querySelector('.flow-hint'))).toBe('1–3 選擇 · S 略過 · ← → 上下張 · Esc 關閉');
  });

  it('key 1 links the first candidate by entry_id and the next invoice shows with 第 2 / 3 張', () => {
    open();
    key('1');
    const req = post('1/link');
    expect(req.request.body).toEqual({ entry_id: 11 });
    req.flush({ id: 1, state: 'linked' });
    flushRead([coffee(2), coffee(3)]); // the write's re-read: invoice 1 left the inbox
    expect(progressText()).toBe('第 2 / 3 張');
    expect(shownNumber()).toContain('AB00000002');
  });

  it('a group candidate links by group_id', () => {
    open([coffee(1, { candidates: [makeEinvoiceTarget({ kind: 'group', id: 70, amount: '-85', date: '2026-10-22' })] })]);
    key('1');
    const req = post('1/link');
    expect(req.request.body).toEqual({ group_id: 70 });
    req.flush({ id: 1, state: 'linked' });
    flushRead([]);
    expect(text(el.querySelector('.flow-end-title'))).toBe('發票都處理完了');
    expect(text(el.querySelector('.flow-end-text'))).toBe('連結 1 張');
  });

  it('N+1 (3) is 都不是: a dismiss without mute', () => {
    open();
    key('3');
    const req = post('1/dismiss');
    expect(req.request.body).toEqual({ mute_seller: false });
    req.flush({ id: 1, state: 'dismissed' });
    flushRead([coffee(2), coffee(3)]);
    expect(progressText()).toBe('第 2 / 3 張');
  });

  it('a failed write keeps the invoice and shows the error inline; nothing moves', () => {
    open();
    answers()[1].click();
    detect();
    post('1/link').flush({ detail: 'x' }, { status: 409, statusText: 'Conflict' });
    detect();
    expect(progressText()).toBe('第 1 / 3 張');
    expect(el.querySelector('.flow-error[role="alert"]')).not.toBeNull();
    key('s');
    expect(el.querySelector('.flow-error')).toBeNull(); // the error stays with its invoice
  });

  it('S skips; the end names linked / 都不是 / skipped; 繼續確認 goes back to the skipped one', () => {
    open([coffee(1), coffee(2)]);
    key('s');
    expect(shownNumber()).toContain('AB00000002');
    key('3');
    post('2/dismiss').flush({ id: 2, state: 'dismissed' });
    flushRead([coffee(1)]);
    expect(text(el.querySelector('.flow-end-title'))).toBe('還有 1 張略過');
    expect(text(el.querySelector('.flow-end-text'))).toBe('都不是 1 張 · 略過 1 張');
    expect(progressText()).toBe('已處理 1 / 2 張');
    (Array.from(el.querySelectorAll<HTMLButtonElement>('.flow-end-actions button')).find((b) => text(b) === '繼續確認'))!.click();
    detect();
    expect(shownNumber()).toContain('AB00000001');
  });

  it('← → move between invoices and stop at the ends; Esc closes', () => {
    open();
    expect(key('ArrowLeft').defaultPrevented).toBe(false);
    key('ArrowRight');
    expect(shownNumber()).toContain('AB00000002');
    key('ArrowLeft');
    expect(shownNumber()).toContain('AB00000001');
    key('Escape');
    expect(closed).toHaveBeenCalledTimes(1);
  });

  it('keys wait while typing, with a modifier, or while 選其他帳… is open; the sheet\'s pick links', () => {
    open();
    const input = document.createElement('input');
    input.className = 'spec-extra';
    document.body.appendChild(input);
    input.focus();
    key('1', input);
    key('1', document, { ctrlKey: true });
    input.blur();
    (Array.from(el.querySelectorAll<HTMLButtonElement>('.einv-link')).find((b) => text(b) === '選其他帳…'))!.click();
    detect();
    http.expectOne((r) => r.url === `${EINVOICE_URL}/1/targets`).flush([makeEinvoiceTarget({ id: 77, name: '測試別筆' })]);
    detect();
    key('1');
    http.expectNone((r) => r.method === 'POST');
    (el.querySelector<HTMLButtonElement>('app-einvoice-target-sheet .target-row'))!.click();
    detect();
    const req = post('1/link');
    expect(req.request.body).toEqual({ entry_id: 77 });
    req.flush({ id: 1, state: 'linked' });
    flushRead([coffee(2), coffee(3)]);
    expect(el.querySelector('app-einvoice-target-sheet')).toBeNull();
    expect(progressText()).toBe('第 2 / 3 張');
  });

  it('記成新的一筆 opens the entry form prefilled and remembers the invoice for the way back', () => {
    open();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    (Array.from(el.querySelectorAll<HTMLButtonElement>('.einv-link')).find((b) => text(b) === '記成新的一筆'))!.click();
    expect(navigate).toHaveBeenCalledWith(['/accounting/entry'], {
      queryParams: { kind: 'expense', date: '2026-10-22', amount: '85', name: '測試品項', merchant: '測試咖啡', invoice: 'AB00000001' },
    });
    expect(TestBed.inject(EinvoiceFlowSession).pendingRecord()).toEqual({ id: 1, number: 'AB00000001', mark: 0 });
  });

  /** The entry form's create, as AccountingService sends it. */
  function created(invoiceNumber: string | null): void {
    TestBed.inject(AccountingService)
      .createEntry({ invoice_number: invoiceNumber } as unknown as EntryInput)
      .subscribe();
    http.expectOne((r) => r.method === 'POST' && r.url === '/api/accounting/entries').flush({ id: 900 });
  }

  function sentToForm(id: number): EinvoiceFlowSession {
    const session = TestBed.inject(EinvoiceFlowSession);
    session.state.set({ ...startFlow(THREE), cursor: id });
    session.awaitRecord(id, `AB0000000${id}`, TestBed.inject(AccountingService).invoiceCreateMark());
    return session;
  }

  it('back from the form after a save: counted as 記成新的一筆 and the next invoice shows', () => {
    const session = sentToForm(1);
    created('AB00000001'); // the form saved an entry with this invoice's number
    open(THREE); // invoice 1 is still listed: it links at the next matcher run
    expect(shownNumber()).toContain('AB00000002');
    expect(progressText()).toBe('第 2 / 3 張');
    expect(session.pendingRecord()).toBeNull();
  });

  it('back from the form after a save that linked the invoice (no longer listed): still counted once', () => {
    const session = sentToForm(1);
    created('AB00000001');
    open(THREE.slice(1)); // linked at save (F3a): the read no longer lists invoice 1
    expect(shownNumber()).toContain('AB00000002');
    expect(progressText()).toBe('第 2 / 3 張');
    expect(session.pendingRecord()).toBeNull();
    key('S');
    key('S');
    expect(text(el.querySelector('.flow-end-text'))).toContain('記成新的一筆 1 張');
  });

  it('back from the form without a save while the invoice left the list: not counted', () => {
    sentToForm(1);
    created('AB00000009'); // another number: not this invoice's record
    open(THREE.slice(1));
    key('S');
    key('S');
    expect(text(el.querySelector('.flow-end-text'))).not.toContain('記成新的一筆');
  });

  it('end state links to the invoices without an entry when there are any (D10)', () => {
    open([coffee(1)], 'panes', { einvoice_unmatched: 4 });
    key('S');
    const link = el.querySelector<HTMLAnchorElement>('.einv-unmatched-link');
    expect(text(link)).toBe('有發票沒有帳 4 張 ›');
    expect(link?.getAttribute('href')).toBe('/notifications/einvoices?state=unmatched');
  });

  it('end state: no link when nothing is unmatched', () => {
    open([coffee(1)], 'panes', { einvoice_unmatched: 0 });
    key('S');
    expect(el.querySelector('.flow-end')).not.toBeNull();
    expect(el.querySelector('.einv-unmatched-link')).toBeNull();
  });

  it('連續記帳: two saves count the invoice once', () => {
    sentToForm(1);
    created('AB00000001');
    created('AB00000001');
    open(THREE);
    key('S');
    key('S');
    expect(text(el.querySelector('.flow-end-text'))).toContain('記成新的一筆 1 張');
  });

  it('連續記帳 A then B: A still counts (not only the last number)', () => {
    sentToForm(1);
    created('AB00000001');
    created('AB00000009');
    open(THREE);
    expect(shownNumber()).toContain('AB00000002');
    expect(progressText()).toBe('第 2 / 3 張');
  });

  it('a read still loading when the owner leaves for the form is cancelled and does not use up the pending record', () => {
    open();
    TestBed.inject(AccountingService).markEntriesChanged();
    detect();
    const late = read(); // a refresh in flight
    vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    (Array.from(el.querySelectorAll<HTMLButtonElement>('.einv-link')).find((b) => text(b) === '記成新的一筆'))!.click();
    fixture.destroy(); // the route change unmounts the flow
    expect(late.cancelled).toBe(true);
    expect(TestBed.inject(EinvoiceFlowSession).pendingRecord()).toEqual({ id: 1, number: 'AB00000001', mark: 0 });
  });

  it('an older read answering after a newer one is dropped', () => {
    open();
    TestBed.inject(AccountingService).markEntriesChanged();
    detect();
    TestBed.inject(AccountingService).markEntriesChanged();
    detect();
    const [older, newer] = http.match((r) => r.method === 'GET' && r.url === INBOX_URL);
    newer.flush({ ...makeInbox([]), einvoices: THREE });
    older.flush({ ...makeInbox([]), einvoices: [coffee(2), coffee(3)] }); // stale: invoice 1 still there
    detect();
    expect(shownNumber()).toContain('AB00000001');
    expect(progressText()).toBe('第 1 / 3 張');
  });

  it('back after another write (another page, a schedule definition) or an entry without this number: not counted', () => {
    sentToForm(1);
    TestBed.inject(AccountingService).markEntriesChanged(); // a write that created no entry with this invoice
    created(null); // an entry without an invoice number
    created('AB00000009'); // the number was changed in the form
    open(THREE);
    expect(shownNumber()).toContain('AB00000001');
    expect(progressText()).toBe('第 1 / 3 張');
  });

  it('back from the form without a save: the same invoice again', () => {
    sentToForm(2);
    open(THREE);
    expect(shownNumber()).toContain('AB00000002');
    expect(progressText()).toBe('第 2 / 3 張');
  });

  it('a fresh open (no pending 記成新的一筆) starts over', () => {
    const session = TestBed.inject(EinvoiceFlowSession);
    session.state.set({ ...startFlow(THREE), cursor: 3 });
    open(THREE);
    expect(progressText()).toBe('第 1 / 3 張');
  });

  it('an invoice answered elsewhere leaves on the next read and is not counted as handled', () => {
    open();
    TestBed.inject(AccountingService).markEntriesChanged();
    detect();
    flushRead([coffee(1), coffee(3)]);
    expect(progressText()).toBe('第 1 / 2 張');
    key('ArrowRight');
    expect(shownNumber()).toContain('AB00000003');
  });

  it('a cut read (fewer listed than the count) keeps an absent invoice: absence proves nothing', () => {
    open();
    TestBed.inject(AccountingService).markEntriesChanged();
    detect();
    read().flush({ ...makeInbox([], { einvoice_ask: 3 }), einvoices: [coffee(1), coffee(3)] });
    detect();
    expect(progressText()).toBe('第 1 / 3 張');
    key('ArrowRight');
    expect(shownNumber()).toContain('AB00000002');
  });

  it('the invoice card: seller, total, date · number · tax id, items with subtotals', () => {
    open([coffee(1, { items: [{ name: '測試拿鐵', subtotal: '60' }, { name: '測試蛋糕', subtotal: '25' }] })]);
    expect(text(el.querySelector('.einv-seller'))).toBe('測試咖啡');
    expect(text(el.querySelector('.einv-total'))).toBe('$85');
    expect(shownNumber()).toMatch(/^2026\/10\/22 · AB00000001 · /);
    expect(Array.from(el.querySelectorAll('.einv-items li')).map((li) => text(li))).toEqual(['測試拿鐵$60', '測試蛋糕$25']);
  });

  it('a held digit or S (repeat) answers nothing; held arrows still move', () => {
    open();
    key('1', document, { repeat: true });
    key('S', document, { repeat: true });
    http.expectNone((r) => r.method === 'POST');
    expect(progressText()).toBe('第 1 / 3 張');
    key('ArrowRight', document, { repeat: true });
    expect(shownNumber()).toContain('AB00000002');
  });

  it('no answer while a write is in flight, an open dialog, or an event already handled', () => {
    open();
    key('1');
    const first = post('1/link');
    key('2'); // busy
    http.expectNone((r) => r.method === 'POST' && (r.body as { entry_id?: number } | null)?.entry_id === 12);
    first.flush({ id: 1, state: 'linked' });
    flushRead([coffee(2), coffee(3)]);
    const dialog = document.createElement('div');
    dialog.className = 'spec-extra';
    dialog.setAttribute('role', 'dialog');
    document.body.appendChild(dialog);
    key('1');
    dialog.remove();
    const handled = new KeyboardEvent('keydown', { key: '1', bubbles: true, cancelable: true });
    handled.preventDefault();
    document.dispatchEvent(handled);
    detect();
    http.expectNone((r) => r.method === 'POST');
  });

  it('key 9 is 都不是 with eight candidates and the ninth candidate with nine', () => {
    const targets = (n: number) => Array.from({ length: n }, (_, i) => makeEinvoiceTarget({ id: 100 + i, amount: '-85', date: '2026-10-22' }));
    open([coffee(1, { candidates: targets(8) }), coffee(2, { candidates: targets(9) })]);
    key('9');
    post('1/dismiss').flush({ id: 1, state: 'dismissed' });
    flushRead([coffee(2, { candidates: targets(9) })]);
    key('9');
    const link = post('2/link');
    expect(link.request.body).toEqual({ entry_id: 108 });
    link.flush({ id: 2, state: 'linked' });
    flushRead([]);
  });

  it('crossing 1024 px mid-flow keeps the place, the answers and the skips', () => {
    open();
    key('1');
    post('1/link').flush({ id: 1, state: 'linked' });
    flushRead([coffee(2), coffee(3)]);
    key('S');
    const component = fixture.componentInstance;
    TestBed.inject(LayoutModeService).set('phone');
    detect();
    expect(fixture.componentInstance).toBe(component);
    expect(shownNumber()).toContain('AB00000003');
    expect(progressText()).toBe('3 / 3');
    TestBed.inject(LayoutModeService).set('panes');
    detect();
    expect(progressText()).toBe('第 3 / 3 張');
    expect(el.querySelector('kbd')).not.toBeNull();
  });

  it('ten candidates: keys 1–9 on the first nine, none on the tenth or 都不是', () => {
    const many = Array.from({ length: 10 }, (_, i) => makeEinvoiceTarget({ id: 100 + i, amount: '-85', date: '2026-10-22' }));
    open([coffee(1, { candidates: many })]);
    expect(answers().map((b) => b.querySelector('kbd') ? text(b.querySelector('kbd')) : null))
      .toEqual(['1', '2', '3', '4', '5', '6', '7', '8', '9', null]);
    expect(el.querySelector('.einv-none kbd')).toBeNull();
    key('9');
    post('1/link').flush({ id: 1, state: 'linked' });
    flushRead([]);
  });

  describe('toast and 復原', () => {
    const toast = () => TestBed.inject(ToastService).current();

    it('a link says 已連結 with 復原; 復原 unlinks + undismisses and shows the invoice again', () => {
      open();
      key('1');
      post('1/link').flush({ id: 1, state: 'linked' });
      flushRead([coffee(2), coffee(3)]);
      expect(toast()).toMatchObject({ kind: 'done', text: '已連結：測試咖啡 $85', action: { label: '復原' } });
      toast()!.action!.run();
      detect();
      post('1/unlink').flush({ id: 1, state: 'dismissed' });
      post('1/undismiss').flush({ id: 1, state: 'unmatched' });
      // the API now lists it as unmatched (until the next matcher run): the flow keeps its candidates
      flushRead([makeEinvoice({ ...coffee(1), state: 'unmatched', candidates: [] }), coffee(2), coffee(3)]);
      expect(shownNumber()).toContain('AB00000001');
      expect(answers().length).toBe(2);
      expect(progressText()).toBe('第 1 / 3 張');
      expect(toast()).toMatchObject({ kind: 'done', text: '已復原：測試咖啡 $85' });
    });

    it('復原 with 有發票沒有帳 reminders off: the restored invoice stays although the read no longer lists it', () => {
      open();
      key('1');
      post('1/link').flush({ id: 1, state: 'linked' });
      flushRead([coffee(2), coffee(3)]);
      toast()!.action!.run();
      detect();
      post('1/unlink').flush({ id: 1, state: 'dismissed' });
      post('1/undismiss').flush({ id: 1, state: 'unmatched' });
      flushRead([coffee(2), coffee(3)]); // remind_unmatched off: unmatched invoices are not listed
      expect(shownNumber()).toContain('AB00000001');
      expect(progressText()).toBe('第 1 / 3 張');
      key('2'); // answered again
      post('1/link').flush({ id: 1, state: 'linked' });
      flushRead([coffee(2), coffee(3)]);
      expect(shownNumber()).toContain('AB00000002');
    });

    it('a 都不是 says so; 復原 is one undismiss', () => {
      open();
      key('3');
      post('1/dismiss').flush({ id: 1, state: 'dismissed' });
      flushRead([coffee(2), coffee(3)]);
      expect(toast()).toMatchObject({ text: '已標成都不是：測試咖啡 $85', action: { label: '復原' } });
      toast()!.action!.run();
      detect();
      post('1/undismiss').flush({ id: 1, state: 'unmatched' });
      flushRead([makeEinvoice({ ...coffee(1), state: 'unmatched', candidates: [] }), coffee(2), coffee(3)]);
      expect(shownNumber()).toContain('AB00000001');
      http.expectNone((r) => r.url.endsWith('/unlink'));
    });

    it('a pick from 選其他帳… also offers 復原', () => {
      open();
      (Array.from(el.querySelectorAll<HTMLButtonElement>('.einv-link')).find((b) => text(b) === '選其他帳…'))!.click();
      detect();
      http.expectOne((r) => r.url === `${EINVOICE_URL}/1/targets`).flush([makeEinvoiceTarget({ id: 77 })]);
      detect();
      el.querySelector<HTMLButtonElement>('app-einvoice-target-sheet .target-row')!.click();
      detect();
      post('1/link').flush({ id: 1, state: 'linked' });
      flushRead([coffee(2), coffee(3)]);
      expect(toast()).toMatchObject({ text: '已連結：測試咖啡 $85', action: { label: '復原' } });
    });

    it('a link 復原 whose undismiss fails says the invoice was unlinked and left as 都不是', () => {
      open();
      key('1');
      post('1/link').flush({ id: 1, state: 'linked' });
      flushRead([coffee(2), coffee(3)]);
      toast()!.action!.run();
      detect();
      post('1/unlink').flush({ id: 1, state: 'dismissed' });
      post('1/undismiss').flush({ detail: 'seller_muted' }, { status: 409, statusText: 'Conflict' });
      flushRead([coffee(2), coffee(3)]); // the unlink went through: the inbox is read again
      expect(toast()).toMatchObject({ kind: 'error', text: '已取消連結，但這張留在「都不是」：這家已設為不提醒' });
      expect(shownNumber()).toContain('AB00000002');
    });

    it('a failed 復原 says so and changes nothing here', () => {
      open();
      key('3');
      post('1/dismiss').flush({ id: 1, state: 'dismissed' });
      flushRead([coffee(2), coffee(3)]);
      toast()!.action!.run();
      detect();
      post('1/undismiss').flush({ detail: 'seller_muted' }, { status: 409, statusText: 'Conflict' });
      detect();
      expect(toast()).toMatchObject({ kind: 'error', text: '復原沒成功：這家已設為不提醒' });
      expect(shownNumber()).toContain('AB00000002');
    });
  });

  it('a near invoice beyond the threshold has no solid answer; 沒有統編 stands alone', () => {
    open([coffee(1, { state: 'near', seller_tax_id: '', candidates: [makeEinvoiceTarget({ id: 11, amount: '-300', date: '2026-10-22' })] })]);
    expect(text(el.querySelector('.einv-reason'))).toBe('商家名稱對得上，金額不同');
    expect(el.querySelectorAll('.einv-answer.solid').length).toBe(0);
    expect(shownNumber()).toBe('2026/10/22 · AB00000001 · 沒有統編');
  });

  it('記成新的一筆 names the record by the seller when the invoice has several items', () => {
    open([coffee(1, { items: [{ name: '甲', subtotal: '40' }, { name: '乙', subtotal: '45' }] })]);
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    (Array.from(el.querySelectorAll<HTMLButtonElement>('.einv-link')).find((b) => text(b) === '記成新的一筆'))!.click();
    expect(navigate.mock.calls[0][1]!.queryParams!['name']).toBe('測試咖啡');
  });

  it('full screen: h1, short progress, no key caps or hint', () => {
    open(THREE, 'phone');
    expect(text(el.querySelector('h1'))).toBe('確認發票');
    expect(progressText()).toBe('1 / 3');
    expect(el.querySelector('kbd')).toBeNull();
    expect(el.querySelector('.flow-hint')).toBeNull();
  });

  it('a failed first read shows the banner; 重試 reads again', () => {
    render();
    read().flush('x', { status: 500, statusText: 'err' });
    detect();
    expect(text(el.querySelector('app-banner'))).toContain('發票資料讀取失敗');
    (el.querySelector<HTMLButtonElement>('app-banner button'))!.click();
    detect();
    flushRead(THREE);
    expect(progressText()).toBe('第 1 / 3 張');
  });
});
