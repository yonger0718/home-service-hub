import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { ToastService } from '../../../services/toast.service';
import { InboxEinvoice } from '../../accounting/inbox/einvoice.model';
import { InboxCounts } from '../../accounting/inbox/inbox.model';
import { makeEinvoice, makeEinvoiceTarget, makeInbox, text } from '../../accounting/inbox/testing';
import { EinvoiceUnmatchedComponent } from './einvoice-unmatched';

const INBOX_URL = '/api/accounting/reconciliation/inbox';
const EINVOICE_URL = '/api/accounting/einvoices';

const BOOKS = { seller_name: '測試書店', seller_tax_id: '00000011' };
const MART = { seller_name: '測試超商', seller_tax_id: '00000022' };
const NO_TAX = { seller_name: '測試小吃', seller_tax_id: '' };

function inv(id: number, over: Partial<InboxEinvoice> = {}): InboxEinvoice {
  return makeEinvoice({ id, number: `AB0000000${id}`, state: 'unmatched', issue_date: `2026-10-0${id}`, ...over });
}

/** 測試書店 ×2 (muting offered, dismissed 3 times before), 測試超商 ×1, 測試小吃 ×1 (no tax id). */
const LIST = [
  inv(1, { ...BOOKS, total_amount: '680', seller_dismissed: 3, offer_mute: true }),
  inv(2, { ...BOOKS, total_amount: '420', seller_dismissed: 3, offer_mute: true }),
  inv(3, { ...MART, total_amount: '312' }),
  inv(4, { ...NO_TAX, total_amount: '159', items: [{ name: '測試便當', subtotal: '159' }] }),
];

describe('EinvoiceUnmatchedComponent', () => {
  let fixture: ComponentFixture<EinvoiceUnmatchedComponent>;
  let el: HTMLElement;
  let http: HttpTestingController;
  let closed: ReturnType<typeof vi.fn<() => void>>;
  const toast = () => TestBed.inject(ToastService).current();

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [EinvoiceUnmatchedComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    fixture?.destroy();
    http.verify();
  });

  function detect(): void {
    TestBed.tick();
    fixture.detectChanges();
  }

  function read(): TestRequest {
    return http.expectOne((r) => r.method === 'GET' && r.url === INBOX_URL && r.params.get('limit') === '1000');
  }

  function flushRead(invoices: InboxEinvoice[], counts: Partial<InboxCounts> = {}): void {
    detect(); // a write's entriesChanged bump runs the re-read effect
    read().flush({ ...makeInbox([], { einvoice_unmatched: invoices.length, ...counts }), einvoices: invoices });
    detect();
  }

  function open(invoices: InboxEinvoice[] = LIST, counts: Partial<InboxCounts> = {}, remind = true, mode: LayoutMode = 'phone'): void {
    TestBed.inject(LayoutModeService).set(mode);
    fixture = TestBed.createComponent(EinvoiceUnmatchedComponent);
    closed = vi.fn<() => void>();
    fixture.componentInstance.closed.subscribe(closed);
    el = fixture.nativeElement as HTMLElement;
    detect();
    http
      .expectOne(`${EINVOICE_URL}/preferences`)
      .flush({ enabled: true, remind_unmatched: remind, date_window_days: 3, mute_prompt_after: 3, version: 1 });
    flushRead(invoices, counts);
  }

  function post(path: string): TestRequest {
    return http.expectOne((r) => r.method === 'POST' && r.url === `${EINVOICE_URL}/${path}`);
  }

  const groups = () => Array.from(el.querySelectorAll<HTMLElement>('.unm-group'));
  const sellers = () => groups().map((g) => text(g.querySelector('.unm-seller')));
  function button(scope: Element, selector: string): HTMLButtonElement {
    return scope.querySelector<HTMLButtonElement>(selector)!;
  }
  function row(id: number): HTMLElement {
    return el.querySelector<HTMLElement>(`.unm-row[data-einvoice-id="${id}"]`)!;
  }

  it('groups by seller with the artboard meta, totals and headline', () => {
    open();
    expect(text(el.querySelector('h1'))).toBe('有發票沒有帳');
    expect(text(el.querySelector('.unm-headline'))).toBe('4 張 · 3 家 · 不計入通知數字');
    expect(sellers()).toEqual(['測試書店', '測試超商', '測試小吃']);
    expect(text(groups()[0].querySelector('.unm-meta'))).toBe('2 張 · 統編 00000011 · 已標不用記帳 3 次');
    expect(text(groups()[0].querySelector('.unm-total'))).toBe('$1,100');
    expect(text(groups()[2].querySelector('.unm-meta'))).toBe('1 張 · 沒有統編');
    expect(text(row(4).querySelector('.unm-items'))).toBe('測試便當 · 1 項');
    expect(text(row(2).querySelector('.unm-date'))).toBe('10/02');
  });

  it('group actions: 這 N 張都不用記帳 for two or more, mute only when offered, the note without a tax id', () => {
    open();
    const [books, mart, noTax] = groups();
    expect(text(books.querySelector('.unm-dismiss-all'))).toBe('這 2 張都不用記帳');
    expect(books.querySelector('.unm-mute')?.className).not.toContain('unm-btn--primary'); // outline, as dismiss-all
    expect(mart.querySelector('.unm-group-foot')).toBeNull();
    expect(noTax.querySelector('.unm-mute')).toBeNull();
    expect(text(noTax.querySelector('.unm-note'))).toBe('沒有統編的商家無法設定不提醒');
  });

  it('不用記帳: one dismiss, the row leaves, toast with 復原; 復原 undismisses and the row is back', () => {
    open();
    button(row(3), '.unm-dismiss').click();
    const req = post('3/dismiss');
    expect(req.request.body).toEqual({ mute_seller: false });
    req.flush({ id: 3, state: 'dismissed' });
    flushRead(LIST.filter((i) => i.id !== 3));
    expect(sellers()).toEqual(['測試書店', '測試小吃']);
    expect(toast()?.text).toBe('已把 測試超商 10/03 標成不用記帳');
    toast()!.action!.run();
    detect();
    post('3/undismiss').flush({ id: 3, state: 'unmatched' });
    detect();
    expect(sellers()).toEqual(['測試書店', '測試超商', '測試小吃']);
    expect(toast()?.text).toBe('已復原：測試超商 $312');
    flushRead(LIST);
  });

  it('這 N 張都不用記帳: one dismiss after another, one toast; 復原 undismisses each', () => {
    open();
    button(groups()[0], '.unm-dismiss-all').click();
    detect();
    post('2/dismiss').flush({ id: 2, state: 'dismissed' }); // newest first
    detect();
    post('1/dismiss').flush({ id: 1, state: 'dismissed' });
    detect();
    expect(sellers()).toEqual(['測試超商', '測試小吃']);
    expect(toast()?.text).toBe('已把 測試書店 的 2 張標成不用記帳');
    http.match((r) => r.url === INBOX_URL).forEach((r) => r.flush({ ...makeInbox([]), einvoices: LIST.slice(2) }));
    detect();
    toast()!.action!.run();
    detect();
    post('2/undismiss').flush({ id: 2, state: 'unmatched' });
    detect();
    post('1/undismiss').flush({ id: 1, state: 'unmatched' });
    detect();
    expect(sellers()[0]).toBe('測試書店');
    expect(toast()?.text).toBe('已復原：測試書店 2 張');
    http.match((r) => r.url === INBOX_URL).forEach((r) => r.flush({ ...makeInbox([]), einvoices: LIST }));
  });

  it('這 N 張都不用記帳 failing on the second: the first stays done and 復原 undoes only it', () => {
    open();
    button(groups()[0], '.unm-dismiss-all').click();
    detect();
    post('2/dismiss').flush({ id: 2, state: 'dismissed' });
    detect();
    post('1/dismiss').flush({ detail: 'x' }, { status: 500, statusText: 'err' });
    detect();
    expect(row(2)).toBeNull();
    expect(row(1)).not.toBeNull();
    expect(toast()?.text).toMatch(/^標了 1 張，第 2 張沒成功：/);
    http.match((r) => r.url === INBOX_URL).forEach((r) => r.flush({ ...makeInbox([]), einvoices: LIST.filter((i) => i.id !== 2) }));
    detect();
    toast()!.action!.run();
    detect();
    post('2/undismiss').flush({ id: 2, state: 'unmatched' });
    detect();
    http.expectNone((r) => r.url === `${EINVOICE_URL}/1/undismiss`);
    expect(row(2)).not.toBeNull();
    http.match((r) => r.url === INBOX_URL).forEach((r) => r.flush({ ...makeInbox([]), einvoices: LIST }));
  });

  it('這家以後都不提醒: one dismiss with mute_seller, the group leaves; 復原 deletes the mute, then undismisses', () => {
    open();
    button(groups()[0], '.unm-mute').click();
    const req = post('2/dismiss'); // the newest invoice
    expect(req.request.body).toEqual({ mute_seller: true });
    req.flush({ id: 2, state: 'dismissed' });
    flushRead(LIST.slice(2)); // the server dismissed 測試書店's other invoice (seller_muted)
    expect(sellers()).toEqual(['測試超商', '測試小吃']);
    expect(toast()?.text).toBe('以後不再提醒 測試書店（2 張一起收起）');
    toast()!.action!.run();
    detect();
    http.expectNone((r) => r.url.endsWith('/undismiss'));
    const del = http.expectOne((r) => r.method === 'DELETE' && r.url === `${EINVOICE_URL}/mutes/00000011`);
    del.flush(null);
    detect();
    post('2/undismiss').flush({ id: 2, state: 'unmatched' });
    detect();
    expect(sellers()[0]).toBe('測試書店');
    expect(groups()[0].querySelectorAll('.unm-row')).toHaveLength(2);
    http.match((r) => r.url === INBOX_URL).forEach((r) => r.flush({ ...makeInbox([]), einvoices: LIST }));
  });

  it('mute 復原 halfway (mute deleted, undismiss refused): says the invoice stays 不用記帳', () => {
    open();
    button(groups()[0], '.unm-mute').click();
    post('2/dismiss').flush({ id: 2, state: 'dismissed' });
    flushRead(LIST.slice(2));
    toast()!.action!.run();
    detect();
    http.expectOne((r) => r.method === 'DELETE').flush(null);
    detect();
    post('2/undismiss').flush({ detail: 'x' }, { status: 500, statusText: 'err' });
    detect();
    expect(toast()?.text).toMatch(/^已恢復提醒，但 10\/02 這張留在「不用記帳」：/);
    http.match((r) => r.url === INBOX_URL).forEach((r) => r.flush({ ...makeInbox([]), einvoices: LIST.filter((i) => i.id !== 2) }));
  });

  it('選帳… opens the target sheet; a pick links and toasts 已連結', () => {
    open();
    button(row(3), '.unm-pick').click();
    detect();
    http.expectOne(`${EINVOICE_URL}/3/targets?days=14`).flush([makeEinvoiceTarget({ id: 77 })]);
    detect();
    el.querySelector<HTMLButtonElement>('app-einvoice-target-sheet .target-row')!.click();
    detect();
    const req = post('3/link');
    expect(req.request.body).toEqual({ entry_id: 77 });
    req.flush({ id: 3, state: 'linked' });
    flushRead(LIST.filter((i) => i.id !== 3));
    expect(el.querySelector('app-einvoice-target-sheet')).toBeNull();
    expect(toast()?.text).toBe('已連結：測試超商 $312');
  });

  it('記一筆 opens the entry form prefilled with the invoice (the save links it)', () => {
    open();
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    button(row(4), '.unm-record').click();
    expect(navigate).toHaveBeenCalledWith(['/accounting/entry'], {
      queryParams: { kind: 'expense', date: '2026-10-04', amount: '159', name: '測試便當', merchant: '測試小吃', invoice: 'AB00000004' },
    });
  });

  it('a failed dismiss keeps the row and shows the error in its group', () => {
    open();
    button(row(3), '.unm-dismiss').click();
    post('3/dismiss').flush({ detail: 'x' }, { status: 500, statusText: 'err' });
    detect();
    expect(row(3)).not.toBeNull();
    expect(groups()[1].querySelector('.unm-error[role="alert"]')).not.toBeNull();
  });

  it('a cut read notes the invoices not listed', () => {
    open(LIST, { einvoice_unmatched: 9 });
    expect(text(el.querySelector('.unm-more'))).toBe('還有 5 張未列出，處理完上面的會接著顯示');
  });

  it('none listed while some exist (questions fill the page): points to the flow, never 都整理好了', () => {
    open([], { einvoice_unmatched: 3, einvoice_ask: 1000 });
    expect(text(el.querySelector('.unm-empty-title'))).toBe('先確認 1000 張要確認的發票，這裡才會列出');
    expect(el.querySelector('.unm-to-flow')?.getAttribute('href')).toBe('/notifications/einvoices');
    expect(el.textContent).not.toContain('都整理好了');
  });

  it('nothing left: 都整理好了; no settings footer until 記帳設定 › 發票 exists (F4)', () => {
    open([], { einvoice_unmatched: 0 });
    expect(text(el.querySelector('.unm-empty-title'))).toBe('都整理好了');
    expect(el.querySelector('.unm-settings')).toBeNull();
  });

  it('reminders off (direct URL): says so instead of 都整理好了', () => {
    open([], { einvoice_unmatched: 0 }, false);
    expect(text(el.querySelector('.unm-empty-title'))).toBe('提醒已關閉');
    expect(el.querySelector('.unm-settings')).toBeNull(); // no 「可以關掉提醒」 when they are off
  });

  it('a failed first read shows the retry banner', () => {
    TestBed.inject(LayoutModeService).set('phone');
    fixture = TestBed.createComponent(EinvoiceUnmatchedComponent);
    el = fixture.nativeElement as HTMLElement;
    detect();
    http.expectOne(`${EINVOICE_URL}/preferences`).flush({ detail: 'x' }, { status: 500, statusText: 'err' });
    read().flush({ detail: 'x' }, { status: 500, statusText: 'err' });
    detect();
    expect(el.querySelector('app-banner')).not.toBeNull();
  });

  it('Esc and the back button close', () => {
    open();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    expect(closed).toHaveBeenCalledTimes(1);
    el.querySelector<HTMLButtonElement>('.flow-back')!.click();
    expect(closed).toHaveBeenCalledTimes(2);
  });
});
