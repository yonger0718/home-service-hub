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

/** A top-level expense: the shape that may be a link target. */
const PLAIN = { kind: 'expense', parent_entry_id: null, group: null };

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
    fixture.componentRef.setInput('detail', { ...PLAIN, id: 42, ...fields } as unknown as EntryDetail);
    fixture.componentInstance.changed.subscribe(() => changed++);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  function button(root: HTMLElement, text: string): HTMLButtonElement {
    const found = [...root.querySelectorAll('button')].find((b) => b.textContent?.includes(text));
    if (!found) throw new Error(`no button ${text}`);
    return found as HTMLButtonElement;
  }

  /** Opens the ⋯ menu (if closed) and returns its item. */
  function menuItem(root: HTMLElement, text: string): HTMLButtonElement {
    if (!root.querySelector('.einv-menu')) {
      (root.querySelector('.einv-more') as HTMLButtonElement).click();
      fixture.detectChanges();
    }
    const found = [...root.querySelectorAll('.einv-menu button')].find((b) => b.textContent?.includes(text));
    if (!found) throw new Error(`no menu item ${text}`);
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
    menuItem(root, '解除連結').click();
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
    menuItem(root, '解除連結').click();
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
    menuItem(root, '解除連結').click();
    http.expectOne('/api/accounting/einvoices/7/unlink').flush({ id: 7, state: 'unmatched' });
    fixture.componentRef.setInput('detail', { ...PLAIN, id: 42 } as unknown as EntryDetail);
    fixture.detectChanges();
    expect(button(root, '連結發票…')).toBeTruthy();
    expect(root.textContent).not.toContain('AB-12345678');
  });

  it('a stale picker list is cleared when the detail reloads', () => {
    const root = render({});
    open(root, { entry_eligible: true, group_id: null, group_eligible: false, invoices: [invoice(1, '甲店')] });
    fixture.componentRef.setInput('detail', { ...PLAIN, id: 43 } as unknown as EntryDetail);
    fixture.detectChanges();
    expect(root.querySelector('.einvoice-pick')).toBeNull();
    expect(button(root, '連結發票…')).toBeTruthy();
  });

  it('drops an in-flight picker response from an older entry', () => {
    const root = render({});
    button(root, '連結發票…').click();
    const stale = http.expectOne('/api/accounting/einvoices/picker?entry_id=42');
    fixture.componentRef.setInput('detail', { ...PLAIN, id: 43 } as unknown as EntryDetail);
    fixture.detectChanges();
    expect(stale.cancelled).toBe(true);
    expect(button(root, '連結發票…')).toBeTruthy();
  });

  it('a double tap on 解除連結 sends one request', () => {
    const root = render({ einvoice: LINKED });
    const unlink = menuItem(root, '解除連結');
    unlink.click();
    unlink.click();
    fixture.detectChanges();
    http.expectOne('/api/accounting/einvoices/7/unlink').flush({ id: 7, state: 'unmatched' });
    fixture.detectChanges();
    expect((root.querySelector('.einv-more') as HTMLButtonElement).disabled).toBe(true);
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

  describe('unlinked: only where the entry may be a target', () => {
    it('an income entry shows no card', () => {
      const root = render({ kind: 'income' });
      expect(root.textContent).not.toContain('發票');
      expect(root.querySelector('.einvoice-open')).toBeNull();
    });

    it('a transfer or a child entry shows no card', () => {
      expect(render({ kind: 'transfer_out' }).querySelector('.einvoice-open')).toBeNull();
      expect(render({ parent_entry_id: 9 }).querySelector('.einvoice-open')).toBeNull();
    });

    it('a split-group member that is not an expense still shows 連結發票…', () => {
      const root = render({ kind: 'receivable', group: { id: 5, kind: 'split', name: null, merchant: null, description: null, count: 2, total: '120', currency: 'TWD' } });
      expect(root.textContent).toContain('發票');
      open(root, { entry_eligible: false, group_id: 5, group_eligible: true, invoices: [invoice(1, '甲店')] });
      expect(root.textContent).toContain('甲店');
    });

    it('a member of a non-split group that is not an expense shows no card', () => {
      const root = render({ kind: 'income', group: { id: 5, kind: 'installment', name: null, merchant: null, description: null, count: 2, total: '120', currency: 'TWD' } });
      expect(root.querySelector('.einvoice-open')).toBeNull();
    });

    it('a linked invoice shows on any entry', () => {
      const root = render({ kind: 'income', einvoice: LINKED });
      expect(root.textContent).toContain('AB-12345678');
    });
  });
  describe('compact (the calendar drawer): the idle row hints how many invoices can be picked', () => {
    const URL42 = '/api/accounting/einvoices/picker?entry_id=42';
    const PICK2 = { entry_eligible: true, group_id: null, group_eligible: false, invoices: [invoice(1, '甲店'), invoice(2, '乙店')] };

    function renderCompact(fields: Record<string, unknown> = {}): HTMLElement {
      fixture = TestBed.createComponent(AppEntryEinvoiceCardComponent);
      fixture.componentRef.setInput('detail', { ...PLAIN, id: 42, ...fields } as unknown as EntryDetail);
      fixture.componentRef.setInput('compact', true);
      fixture.componentInstance.changed.subscribe(() => changed++);
      fixture.detectChanges();
      return fixture.nativeElement as HTMLElement;
    }

    function settle(body: Record<string, unknown>, url = URL42): void {
      http.expectOne(url).flush(body);
      fixture.detectChanges();
    }

    const hint = (root: HTMLElement) => root.querySelector('.einvoice-hint')?.textContent?.trim();

    it('reads the picker once and shows the count; the list waits for the click', () => {
      const root = renderCompact();
      settle(PICK2);
      const row = root.querySelector('button.einvoice-open.einvoice-row')!;
      expect(row.textContent).toContain('連結發票…');
      expect(hint(root)).toBe('前後 7 天有 2 張可選');
      expect(fixture.componentInstance.pickerState().status).toBe('idle');
      expect(root.querySelector('.einvoice-pick')).toBeNull();
      (row as HTMLButtonElement).click();
      fixture.detectChanges();
      http.expectNone(URL42);
      expect(root.querySelectorAll('.einvoice-pick')).toHaveLength(2);
    });

    it('shows the row without a hint while the read is pending, and a click reads afresh', () => {
      const root = renderCompact();
      expect(root.querySelector('button.einvoice-open.einvoice-row')).not.toBeNull();
      expect(root.querySelector('.einvoice-hint')).toBeNull();
      (root.querySelector('.einvoice-open') as HTMLButtonElement).click();
      fixture.detectChanges();
      const open = http.match(URL42).filter((req) => !req.cancelled);
      expect(open).toHaveLength(1);
      open[0].flush(PICK2);
      fixture.detectChanges();
      expect(root.querySelectorAll('.einvoice-pick')).toHaveLength(2);
    });

    it('still asks 這筆 / 整組 from the held picker', () => {
      const root = renderCompact();
      settle({ entry_eligible: true, group_id: 5, group_eligible: true, invoices: [invoice(1, '甲店')] });
      (root.querySelector('.einvoice-open') as HTMLButtonElement).click();
      fixture.detectChanges();
      http.expectNone(URL42);
      expect(root.textContent).toContain('這筆');
      expect(root.textContent).toContain('整組');
    });

    it('says so when no invoice can be picked', () => {
      const root = renderCompact();
      settle({ entry_eligible: true, group_id: null, group_eligible: false, invoices: [] });
      expect(hint(root)).toBe('前後 7 天沒有可選的發票');
    });

    it('hides the card when the held picker says the entry is not eligible', () => {
      const root = renderCompact();
      settle({ entry_eligible: false, group_id: null, group_eligible: false, invoices: [] });
      expect(root.querySelector('.einvoice-open')).toBeNull();
      expect(root.textContent).not.toContain('發票');
    });

    it('shows no hint and no error when the read fails, and a click reads again', () => {
      const root = renderCompact();
      http.expectOne(URL42).flush({}, { status: 500, statusText: 'err' });
      fixture.detectChanges();
      expect(root.querySelector('.einvoice-row')).not.toBeNull();
      expect(root.querySelector('.einvoice-hint')).toBeNull();
      expect(root.textContent).not.toContain('讀取失敗');
      (root.querySelector('.einvoice-open') as HTMLButtonElement).click();
      fixture.detectChanges();
      settle(PICK2);
      expect(root.querySelectorAll('.einvoice-pick')).toHaveLength(2);
    });

    it('drops the hint and reads once more when the entry changes', () => {
      const root = renderCompact();
      settle(PICK2);
      fixture.componentRef.setInput('detail', { ...PLAIN, id: 43 } as unknown as EntryDetail);
      fixture.detectChanges();
      expect(root.querySelector('.einvoice-hint')).toBeNull();
      settle({ entry_eligible: true, group_id: null, group_eligible: false, invoices: [invoice(3, '丙店')] }, '/api/accounting/einvoices/picker?entry_id=43');
      expect(hint(root)).toBe('前後 7 天有 1 張可選');
    });

    it('does not read for a linked invoice', () => {
      renderCompact({ einvoice: LINKED });
    });

    it('does not read before the click when compact is off', () => {
      const root = render({});
      expect(root.querySelector('.einvoice-hint')).toBeNull();
      expect(root.querySelector('.einvoice-row')).toBeNull();
    });
  });

  describe('linked card (D12)', () => {
    const SEVEN: EntryEinvoice = {
      id: 9, number: 'AB00000009', issue_date: '2026-10-21', seller_name: '測試超市 一號店', total_amount: '1286.0000',
      linked_by: 'auto', linked_to: 'entry', recorded_amount: '1286.0000',
      items: [
        { name: '測試鮮奶', subtotal: '95' }, { name: '測試吐司', subtotal: '65' }, { name: '測試雞蛋', subtotal: '425' },
        { name: '測試米', subtotal: '200' }, { name: '測試麵', subtotal: '150' }, { name: '測試油', subtotal: '251' },
        { name: '測試鹽', subtotal: '100' },
      ],
    };
    const TWELVE: EntryEinvoice = {
      id: 10, number: 'AB00000010', issue_date: '2026-10-19', seller_name: '測試量販 二號店', total_amount: '2463.0000',
      linked_by: 'owner', linked_to: 'group', recorded_amount: '2460.0000',
      items: [...Array.from({ length: 11 }, (_, i) => ({ name: `測試品項${i + 1}`, subtotal: '200' })), { name: '測試品項12', subtotal: '263' }],
    };
    const text = (root: HTMLElement, sel: string) => root.querySelector(sel)?.textContent?.replace(/\s+/g, ' ').trim();
    const parts = (el: Element) => [...el.children].map((c) => c.textContent?.trim()).join(' ');
    const rows = (root: HTMLElement) => [...root.querySelectorAll('.einv-item')].map(parts);

    it('entry match: 發票, ✓ 金額相符, 自動連結, three items, the rest summed, 合計', () => {
      const root = render({ einvoice: SEVEN });
      expect(text(root, '.einv-title')).toBe('發票');
      const chips = [...root.querySelectorAll('.einv-chip')].map((c) => c.textContent?.trim());
      expect(chips).toEqual(['✓ 金額相符', '自動連結']);
      expect(root.querySelector('.einv-chip[data-tone="ok"]')).not.toBeNull();
      expect(text(root, '.einv-meta')).toBe('測試超市 一號店 · 10/21 · AB00000009');
      expect(rows(root)).toEqual(['測試鮮奶 $95', '測試吐司 $65', '測試雞蛋 $425', '… 另外 4 項 $701', '合計 7 項 $1,286']);
    });

    it('group off by $3: 發票 · 整組, 差 $3, 手動連結, 這組記了, items behind 看 12 項明細', () => {
      const root = render({ einvoice: TWELVE, group: { id: 5, kind: 'split' } });
      expect(text(root, '.einv-title')).toBe('發票 · 整組');
      const chips = [...root.querySelectorAll('.einv-chip')].map((c) => c.textContent?.trim());
      expect(chips).toEqual(['差 $3', '手動連結']);
      expect(root.querySelector('.einv-chip[data-tone="warn"]')).not.toBeNull();
      expect(text(root, '.einv-meta')).toBe('測試量販 二號店 · 10/19 · 12 項 · $2,463（這組記了 $2,460）');
      expect(rows(root)).toEqual([]);
      const toggle = root.querySelector('.einv-toggle') as HTMLButtonElement;
      expect(toggle.textContent?.replace(/\s+/g, ' ').trim()).toBe('看 12 項明細 ›');
      expect(toggle.getAttribute('aria-expanded')).toBe('false');
      toggle.click();
      fixture.detectChanges();
      expect(rows(root).length).toBe(13);
      expect(rows(root)[12]).toBe('合計 12 項 $2,463');
      expect(toggle.getAttribute('aria-expanded')).toBe('true');
      toggle.click();
      fixture.detectChanges();
      expect(rows(root)).toEqual([]);
    });

    it('group that matches has no 這組記了 note', () => {
      const root = render({ einvoice: { ...TWELVE, recorded_amount: '2463' }, group: { id: 5, kind: 'split' } });
      expect(text(root, '.einv-meta')).toBe('測試量販 二號店 · 10/19 · 12 項 · $2,463');
      expect(text(root, '.einv-chip[data-tone="ok"]')).toBe('✓ 金額相符');
    });

    it('no match chip when recorded_amount is null or absent; the card still shows', () => {
      for (const recorded of [null, undefined]) {
        const root = render({ einvoice: { ...SEVEN, recorded_amount: recorded } });
        expect([...root.querySelectorAll('.einv-chip')].map((c) => c.textContent?.trim())).toEqual(['自動連結']);
        expect(text(root, '.einv-meta')).toContain('AB00000009');
      }
    });

    it('foreign amounts compare and show without the TWD symbol', () => {
      const root = render({ einvoice: { ...SEVEN, total_amount: '20.99', recorded_amount: '20.49', items: [{ name: 'Plan', subtotal: '20.99' }] } });
      expect(text(root, '.einv-chip[data-tone="warn"]')).toBe('差 0.50');
      expect(rows(root)).toEqual(['Plan 20.99', '合計 1 項 20.99']);
    });

    it('three items or fewer: no 另外 row', () => {
      const root = render({ einvoice: { ...SEVEN, items: SEVEN.items.slice(0, 3), total_amount: '585', recorded_amount: '585' } });
      expect(rows(root)).toEqual(['測試鮮奶 $95', '測試吐司 $65', '測試雞蛋 $425', '合計 3 項 $585']);
    });

    it('⋯ menu: opens, Esc closes and returns focus, outside click closes', () => {
      const root = render({ einvoice: SEVEN });
      document.body.appendChild(root);
      const more = root.querySelector('.einv-more') as HTMLButtonElement;
      expect(more.getAttribute('aria-label')).toBe('發票動作');
      expect(more.getAttribute('aria-haspopup')).toBe('menu');
      more.click();
      fixture.detectChanges();
      expect(more.getAttribute('aria-expanded')).toBe('true');
      const items = [...root.querySelectorAll('.einv-menu [role="menuitem"]')].map((b) => (b.children.length ? parts(b) : b.textContent?.trim()));
      expect(items).toEqual(['換一張發票', '解除連結 之後不會再自動連回']);
      expect(document.activeElement?.textContent).toContain('換一張發票');
      root.querySelector('.einv-menu')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      fixture.detectChanges();
      expect(root.querySelector('.einv-menu')).toBeNull();
      expect(document.activeElement).toBe(more);
      more.click();
      fixture.detectChanges();
      document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      fixture.detectChanges();
      expect(root.querySelector('.einv-menu')).toBeNull();
      root.remove();
    });

    it('換一張發票 is not offered where an unlinked entry could not be a target', () => {
      const root = render({ kind: 'income', einvoice: SEVEN });
      (root.querySelector('.einv-more') as HTMLButtonElement).click();
      fixture.detectChanges();
      expect([...root.querySelectorAll('.einv-menu button')].map((b) => b.textContent?.trim())).toEqual([expect.stringContaining('解除連結')]);
    });

    it('換一張發票 unlinks, then opens the picker once the reloaded detail has no invoice', () => {
      const root = render({ einvoice: SEVEN });
      menuItem(root, '換一張發票').click();
      http.expectOne('/api/accounting/einvoices/9/unlink').flush({ id: 9, state: 'dismissed' });
      expect(changed).toBe(1);
      fixture.componentRef.setInput('detail', { ...PLAIN, id: 42 } as unknown as EntryDetail);
      fixture.detectChanges();
      http.expectOne('/api/accounting/einvoices/picker?entry_id=42')
        .flush({ entry_eligible: true, group_id: null, group_eligible: false, invoices: [invoice(1, '測試甲店')] });
      fixture.detectChanges();
      expect(root.querySelectorAll('.einvoice-pick').length).toBe(1);
    });

    it('換一張發票 with a failed unlink shows the error and opens nothing', () => {
      const root = render({ einvoice: SEVEN });
      menuItem(root, '換一張發票').click();
      http.expectOne('/api/accounting/einvoices/9/unlink').flush({ detail: 'x' }, { status: 500, statusText: 'err' });
      fixture.detectChanges();
      expect(root.textContent).toContain('沒成功');
      expect(changed).toBe(0);
      http.expectNone('/api/accounting/einvoices/picker?entry_id=42');
    });

    it('a plain 解除連結 does not open the picker after the reload', () => {
      const root = render({ einvoice: SEVEN });
      menuItem(root, '解除連結').click();
      http.expectOne('/api/accounting/einvoices/9/unlink').flush({ id: 9, state: 'dismissed' });
      fixture.componentRef.setInput('detail', { ...PLAIN, id: 42 } as unknown as EntryDetail);
      fixture.detectChanges();
      http.expectNone('/api/accounting/einvoices/picker?entry_id=42');
      expect(button(root, '連結發票…')).toBeTruthy();
    });
  });
});
