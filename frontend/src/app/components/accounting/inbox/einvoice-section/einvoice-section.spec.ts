import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { INBOX_GENERIC_ERROR } from '../inbox-view';
import { EinvoiceTarget, InboxEinvoice } from '../einvoice.model';
import { AppEinvoiceSectionComponent, einvoiceErrorText } from './einvoice-section';

const API = '/api/accounting/einvoices';

function target(overrides: Partial<EinvoiceTarget> = {}): EinvoiceTarget {
  return {
    kind: 'entry',
    id: 3,
    account_name: '玉山',
    date: '2026-09-03',
    amount: '120.0000',
    currency: 'TWD',
    category: '餐飲',
    name: '早餐',
    ...overrides,
  };
}

function invoice(overrides: Partial<InboxEinvoice> = {}): InboxEinvoice {
  return {
    id: 7,
    number: 'AB12345678',
    issue_date: '2026-09-03',
    seller_name: '示範早餐店',
    seller_tax_id: '12345678',
    total_amount: '120.0000',
    status_raw: '開立',
    state: 'ambiguous',
    items: [{ name: '蛋餅', subtotal: '120.0000' }],
    candidates: [target(), target({ id: 4, name: null })],
    offer_mute: false,
    ...overrides,
  };
}

describe('AppEinvoiceSectionComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AppEinvoiceSectionComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function render(items: InboxEinvoice[], mode: 'full' | 'preview' = 'full') {
    const fixture = TestBed.createComponent(AppEinvoiceSectionComponent);
    fixture.componentRef.setInput('items', items);
    fixture.componentRef.setInput('mode', mode);
    const changed = vi.fn();
    fixture.componentInstance.changed.subscribe(changed);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement, changed };
  }

  const buttons = (el: HTMLElement) => Array.from(el.querySelectorAll<HTMLButtonElement>('button'));
  const byText = (el: HTMLElement, label: string) =>
    buttons(el).find((button) => button.textContent?.trim() === label);

  it('renders the three card kinds with items', () => {
    const { el } = render([
      invoice(),
      invoice({ id: 8, state: 'near', items: [{ name: '紅茶', subtotal: '30.0000' }] }),
      invoice({ id: 9, state: 'unmatched', candidates: [], items: [{ name: '原子筆', subtotal: '25.0000' }] }),
    ]);
    expect(el.querySelector('h3')?.textContent).toBe('發票待對帳');
    const text = el.textContent ?? '';
    expect(text).toContain('哪一筆是這張發票？');
    expect(text).toContain('金額或商家相近，是這筆嗎？');
    expect(text).toContain('有發票沒有帳');
    expect(text).toContain('蛋餅');
    expect(text).toContain('紅茶');
    expect(text).toContain('原子筆');
    expect(el.querySelectorAll('.inbox-card').length).toBe(3);
  });

  it('formats the candidate label without stray separators', () => {
    const { el } = render([invoice()]);
    const labels = Array.from(el.querySelectorAll('.candidate')).map((b) => b.textContent?.trim());
    expect(labels[0]).toBe(`玉山 · 2026/09/03 · $120 · 餐飲 · 早餐`);
    expect(labels[1]).toBe(`玉山 · 2026/09/03 · $120 · 餐飲`);
  });

  it('one-tap link posts and removes the card', () => {
    const { el, fixture, changed } = render([invoice()]);
    (el.querySelector('.candidate') as HTMLButtonElement).click();
    const req = http.expectOne(`${API}/7/link`);
    expect(req.request.body).toEqual({ entry_id: 3 });
    req.flush({ id: 7, state: 'linked', dismiss_reason: null, linked_by: 'owner', entry_id: 3, group_id: null });
    fixture.detectChanges();
    expect(el.querySelectorAll('.inbox-card').length).toBe(0);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('都不是 and 不用記帳 dismiss', () => {
    const { el, fixture } = render([
      invoice(),
      invoice({ id: 9, state: 'unmatched', candidates: [], offer_mute: true }),
    ]);
    byText(el, '都不是')!.click();
    const first = http.expectOne(`${API}/7/dismiss`);
    expect(first.request.body).toEqual({ mute_seller: false });
    first.flush({});
    byText(el, '不用記帳')!.click();
    const second = http.expectOne(`${API}/9/dismiss`);
    expect(second.request.body).toEqual({ mute_seller: false });
    second.flush({});
    fixture.detectChanges();
    expect(el.querySelectorAll('.inbox-card').length).toBe(0);
  });

  it('mute offer only when offered', () => {
    const { el } = render([invoice({ state: 'unmatched', candidates: [], offer_mute: true })]);
    byText(el, '這家以後都不提醒')!.click();
    expect(http.expectOne(`${API}/7/dismiss`).request.body).toEqual({ mute_seller: true });

    const second = render([invoice({ id: 8, state: 'unmatched', candidates: [], offer_mute: false })]);
    expect(byText(second.el, '這家以後都不提醒')).toBeUndefined();
  });

  it('group candidate links by group_id', () => {
    const { el } = render([invoice({ candidates: [target({ kind: 'group', id: 11 })] })]);
    (el.querySelector('.candidate') as HTMLButtonElement).click();
    expect(http.expectOne(`${API}/7/link`).request.body).toEqual({ group_id: 11 });
  });

  it('refusal shows the mapped text', () => {
    const { el, fixture, changed } = render([invoice()]);
    (el.querySelector('.candidate') as HTMLButtonElement).click();
    http
      .expectOne(`${API}/7/link`)
      .flush({ detail: 'target_ineligible' }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    expect(el.querySelector('.refused')?.textContent).toContain('沒成功：這筆帳不能連結');
    expect(el.querySelectorAll('.inbox-card').length).toBe(1);
    expect(changed).not.toHaveBeenCalled();
  });

  it('shows a fractional invoice total as a plain number, not rounded TWD', () => {
    const { el } = render([invoice({ total_amount: '20.9900', items: [{ name: '糖果', subtotal: '20.9900' }] })]);
    expect(el.textContent).toContain('20.99');
    expect(el.textContent).not.toContain('$21');
  });

  it('clears a stale refusal when a retry starts', () => {
    const { el, fixture } = render([invoice()]);
    const tap = () => (el.querySelector('.candidate') as HTMLButtonElement).click();
    tap();
    http.expectOne(`${API}/7/link`).flush({ detail: 'retry' }, { status: 409, statusText: 'Conflict' });
    fixture.detectChanges();
    expect(el.querySelector('.refused')).not.toBeNull();
    tap();
    fixture.detectChanges();
    expect(el.querySelector('.refused')).toBeNull();
    http.expectOne(`${API}/7/link`);
  });

  it('preview hides unmatched reminders', () => {
    const { el } = render(
      [invoice(), invoice({ id: 9, state: 'unmatched', candidates: [] })],
      'preview',
    );
    expect(el.querySelectorAll('.inbox-card').length).toBe(1);
    expect(el.textContent).not.toContain('有發票沒有帳');
  });

  it('選帳… opens the target sheet and a pick links', () => {
    const { el, fixture } = render([invoice({ state: 'unmatched', candidates: [] })]);
    byText(el, '選帳…')!.click();
    fixture.detectChanges();
    http.expectOne((r) => r.url === `${API}/7/targets`).flush([target({ id: 5 })]);
    fixture.detectChanges();
    (el.querySelector('.target-row') as HTMLButtonElement).click();
    expect(http.expectOne(`${API}/7/link`).request.body).toEqual({ entry_id: 5 });
  });
});

describe('einvoiceErrorText', () => {
  const err = (detail: unknown) =>
    new HttpErrorResponse({ status: 409, error: { detail } });

  it('maps the 409 codes, with or without a detail suffix', () => {
    expect(einvoiceErrorText(err('einvoice_void'))).toBe('沒成功：發票已作廢');
    expect(einvoiceErrorText(err('not_linked'))).toBe('沒成功：已解除連結');
    expect(einvoiceErrorText(err('seller_muted: x'))).toBe('沒成功：這家已設為不提醒');
    expect(einvoiceErrorText(err('retry'))).toBe('沒成功：請再試一次');
  });

  it('falls back to the generic text', () => {
    expect(einvoiceErrorText(err('???'))).toBe(INBOX_GENERIC_ERROR);
    expect(einvoiceErrorText(new Error('x'))).toBe(INBOX_GENERIC_ERROR);
  });
});
