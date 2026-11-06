import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { EntryDetail } from '../../../models/accounting.model';
import { EntryEinvoice, EinvoiceSummary } from '../inbox/einvoice.model';
import { AppEntryEinvoiceCardComponent } from './einvoice-card';

const LINKED: EntryEinvoice = {
  id: 7, number: 'AB-12345678', issue_date: '2026-10-03', seller_name: '範例超市', total_amount: '230',
  linked_by: 'auto', linked_to: 'entry',
  items: [{ name: '牛奶', subtotal: '90' }, { name: '麵包', subtotal: '140' }],
};

function invoice(id: number, seller: string): EinvoiceSummary {
  return {
    id, number: `CD-0000000${id}`, issue_date: '2026-10-02', seller_name: seller, seller_tax_id: '12345678',
    total_amount: '230', status_raw: '', state: 'unmatched', items: [{ name: '茶', subtotal: '230' }],
  };
}

describe('AppEntryEinvoiceCardComponent', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<AppEntryEinvoiceCardComponent>;
  let changed: number;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AppEntryEinvoiceCardComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
    changed = 0;
  });

  afterEach(() => http.verify());

  function render(fields: Record<string, unknown>): HTMLElement {
    fixture = TestBed.createComponent(AppEntryEinvoiceCardComponent);
    fixture.componentRef.setInput('detail', { id: 42, ...fields } as unknown as EntryDetail);
    fixture.componentInstance.changed.subscribe(() => changed++);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function button(root: HTMLElement, text: string): HTMLButtonElement {
    const found = [...root.querySelectorAll('button')].find((b) => b.textContent?.includes(text));
    if (!found) throw new Error(`no button ${text}`);
    return found as HTMLButtonElement;
  }

  function open(root: HTMLElement, picker: Record<string, unknown>): void {
    button(root, '連結發票…').click();
    http.expectOne('/api/accounting/einvoices/picker?entry_id=42').flush(picker);
    fixture.detectChanges();
  }

  it('shows the linked invoice and unlinks', () => {
    const root = render({ einvoice: LINKED });
    expect(root.textContent).toContain('發票');
    expect(root.textContent).toContain('AB-12345678');
    expect(root.textContent).toContain('範例超市');
    expect(root.textContent).toContain('牛奶');
    expect(root.textContent).toContain('自動連結');
    expect(root.textContent).not.toContain('整組');
    button(root, '解除連結').click();
    http.expectOne('/api/accounting/einvoices/7/unlink').flush({ id: 7, state: 'unmatched' });
    expect(changed).toBe(1);
  });

  it('shows 手動連結 for an owner link', () => {
    const root = render({ einvoice: { ...LINKED, linked_by: 'owner' } });
    expect(root.textContent).toContain('手動連結');
  });

  it('group invoice is marked 整組', () => {
    const root = render({ einvoice: { ...LINKED, linked_to: 'group' } });
    expect(root.textContent).toContain('整組');
  });

  it('shows an unlink error', () => {
    const root = render({ einvoice: LINKED });
    button(root, '解除連結').click();
    http.expectOne('/api/accounting/einvoices/7/unlink').flush({ detail: 'x' }, { status: 500, statusText: 'err' });
    fixture.detectChanges();
    expect(root.textContent).toContain('沒成功');
    expect(changed).toBe(0);
  });

  it('picker for a plain entry', () => {
    const root = render({});
    button(root, '連結發票…').click();
    fixture.detectChanges();
    expect(root.textContent).toContain('讀取中');
    http.expectOne('/api/accounting/einvoices/picker?entry_id=42').flush({
      entry_eligible: true, group_id: null, group_eligible: false, invoices: [invoice(1, '甲店'), invoice(2, '乙店')],
    });
    fixture.detectChanges();
    expect(root.textContent).toContain('甲店');
    expect(root.textContent).toContain('乙店');
    (root.querySelectorAll('.einvoice-pick')[1] as HTMLButtonElement).click();
    const req = http.expectOne('/api/accounting/einvoices/2/link');
    expect(req.request.body).toEqual({ entry_id: 42 });
    req.flush({ id: 2, state: 'linked' });
    expect(changed).toBe(1);
  });

  it('split member asks 這筆 or 整組', () => {
    const root = render({});
    open(root, { entry_eligible: true, group_id: 9, group_eligible: true, invoices: [invoice(1, '甲店')] });
    expect(root.querySelector('.einvoice-pick')).toBeNull();
    button(root, '整組').click();
    fixture.detectChanges();
    (root.querySelector('.einvoice-pick') as HTMLButtonElement).click();
    const req = http.expectOne('/api/accounting/einvoices/1/link');
    expect(req.request.body).toEqual({ group_id: 9 });
    req.flush({ id: 1, state: 'linked' });
    expect(changed).toBe(1);
  });

  it('split member can choose 這筆', () => {
    const root = render({});
    open(root, { entry_eligible: true, group_id: 9, group_eligible: true, invoices: [invoice(1, '甲店')] });
    button(root, '這筆').click();
    fixture.detectChanges();
    (root.querySelector('.einvoice-pick') as HTMLButtonElement).click();
    expect(http.expectOne('/api/accounting/einvoices/1/link').request.body).toEqual({ entry_id: 42 });
  });

  it('group-only eligibility links the group directly', () => {
    const root = render({});
    open(root, { entry_eligible: false, group_id: 9, group_eligible: true, invoices: [invoice(1, '甲店')] });
    (root.querySelector('.einvoice-pick') as HTMLButtonElement).click();
    expect(http.expectOne('/api/accounting/einvoices/1/link').request.body).toEqual({ group_id: 9 });
  });

  it('not eligible hides the picker', () => {
    const root = render({});
    open(root, { entry_eligible: false, group_id: null, group_eligible: false, invoices: [invoice(1, '甲店')] });
    expect(root.textContent?.trim()).toBe('');
  });

  it('says so when no invoice can be linked', () => {
    const root = render({});
    open(root, { entry_eligible: true, group_id: null, group_eligible: false, invoices: [] });
    expect(root.textContent).toContain('沒有可連結的發票');
  });

  it('shows 讀取失敗 when the picker fails', () => {
    const root = render({});
    button(root, '連結發票…').click();
    http.expectOne('/api/accounting/einvoices/picker?entry_id=42').flush('x', { status: 500, statusText: 'err' });
    fixture.detectChanges();
    expect(root.textContent).toContain('讀取失敗');
  });

  it('shows a link error and does not emit', () => {
    const root = render({});
    open(root, { entry_eligible: true, group_id: null, group_eligible: false, invoices: [invoice(1, '甲店')] });
    (root.querySelector('.einvoice-pick') as HTMLButtonElement).click();
    http.expectOne('/api/accounting/einvoices/1/link').flush({ detail: 'x' }, { status: 500, statusText: 'err' });
    fixture.detectChanges();
    expect(root.textContent).toContain('沒成功');
    expect(changed).toBe(0);
  });

  it('shows a foreign total without the TWD symbol', () => {
    const root = render({ einvoice: { ...LINKED, total_amount: '12.50' } });
    expect(root.textContent).toContain('12.50');
    expect(root.textContent).not.toContain('NT$12.50');
  });

  it('split member with no invoices sees the empty text, not the choice', () => {
    const root = render({});
    open(root, { entry_eligible: true, group_id: 9, group_eligible: true, invoices: [] });
    expect(root.textContent).toContain('沒有可連結的發票');
    expect(root.textContent).not.toContain('這筆');
  });

  it('after an unlink the reloaded detail shows the idle button', () => {
    const root = render({ einvoice: LINKED });
    button(root, '解除連結').click();
    http.expectOne('/api/accounting/einvoices/7/unlink').flush({ id: 7, state: 'unmatched' });
    fixture.componentRef.setInput('detail', { id: 42 } as unknown as EntryDetail);
    fixture.detectChanges();
    expect(button(root, '連結發票…')).toBeTruthy();
    expect(root.textContent).not.toContain('AB-12345678');
  });

  it('a stale picker list is cleared when the detail reloads', () => {
    const root = render({});
    open(root, { entry_eligible: true, group_id: null, group_eligible: false, invoices: [invoice(1, '甲店')] });
    fixture.componentRef.setInput('detail', { id: 43 } as unknown as EntryDetail);
    fixture.detectChanges();
    expect(root.querySelector('.einvoice-pick')).toBeNull();
    expect(button(root, '連結發票…')).toBeTruthy();
  });

  it('drops an in-flight picker response from an older entry', () => {
    const root = render({});
    button(root, '連結發票…').click();
    const stale = http.expectOne('/api/accounting/einvoices/picker?entry_id=42');
    fixture.componentRef.setInput('detail', { id: 43 } as unknown as EntryDetail);
    fixture.detectChanges();
    expect(stale.cancelled).toBe(true);
    expect(button(root, '連結發票…')).toBeTruthy();
  });

  it('a double tap on 解除連結 sends one request', () => {
    const root = render({ einvoice: LINKED });
    const unlink = button(root, '解除連結');
    unlink.click();
    unlink.click();
    http.expectOne('/api/accounting/einvoices/7/unlink').flush({ id: 7, state: 'unmatched' });
    unlink.click();
    http.expectNone('/api/accounting/einvoices/7/unlink');
    expect(changed).toBe(1);
  });

  it('a double tap on 連結發票… sends one picker request', () => {
    const root = render({});
    const open = button(root, '連結發票…');
    open.click();
    open.click();
    http.expectOne('/api/accounting/einvoices/picker?entry_id=42').flush({
      entry_eligible: true, group_id: null, group_eligible: false, invoices: [],
    });
  });
});
