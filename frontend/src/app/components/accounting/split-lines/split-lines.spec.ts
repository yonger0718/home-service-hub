import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { CategoryNode, LedgerAccount } from '../../../models/accounting.model';
import { emptyEntryInput } from '../entry-form/entry-save';
import { SplitLinesComponent } from './split-lines';

const ACCOUNTS = [
  { id: 1, name: '玉山 UNI', currency: 'TWD', is_archived: false },
  { id: 7, name: '錢包', currency: 'TWD', is_archived: false },
] as unknown as LedgerAccount[];

const RECEIVABLE_TREE = [
  { id: 50, kind: 'receivable', parent_id: null, name: '應收款項', icon: '🤝', color: '#84dccf', is_hidden: false,
    children: [{ id: 51, kind: 'receivable', parent_id: 50, name: '代付', icon: '🤝', color: '#84dccf', is_hidden: false, children: [] }] },
] as unknown as CategoryNode[];

describe('SplitLinesComponent', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<SplitLinesComponent>;
  let el: HTMLElement;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SplitLinesComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(SplitLinesComponent);
    fixture.componentRef.setInput('accounts', ACCOUNTS);
    fixture.componentRef.setInput('defaultAccountId', 1);
    fixture.detectChanges();
    el = fixture.nativeElement as HTMLElement;
  });

  afterEach(() => http.verify());

  function openSheet(): void {
    el.querySelector<HTMLButtonElement>('.add')!.click();
    fixture.detectChanges();
    http.expectOne(r => r.url.startsWith('/api/accounting/categories') && r.urlWithParams.includes('kind=expense')).flush([]);
    http.expectOne('/api/accounting/counterparties').flush([{ id: 5, name: 'Alan', open_amounts: [] }]);
    fixture.detectChanges();
  }

  function setValue(selector: string, value: string, event = 'input'): void {
    const field = el.querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
    field.value = value;
    field.dispatchEvent(new Event(event));
    fixture.detectChanges();
  }

  it('opens the 新增拆帳行 sheet with type, category, amount, account and counterparty rows', () => {
    openSheet();
    const sheet = el.querySelector('.sheet')!;
    expect(sheet.querySelector('h3')?.textContent).toBe('新增拆帳行');
    expect(Array.from(sheet.querySelectorAll('.k')).map(k => k.textContent?.trim().slice(0, 2))).toEqual(['類型', '類別', '金額', '帳戶']);
    expect(el.querySelector<HTMLSelectElement>('.line-account')!.value).toBe('1');
  });

  it('adds a 代付 receivable line for an existing counterparty', () => {
    openSheet();
    el.querySelectorAll<HTMLButtonElement>('.line-kind button')[2].click();
    fixture.detectChanges();
    http.expectOne(r => r.url.startsWith('/api/accounting/categories') && r.urlWithParams.includes('kind=receivable')).flush(RECEIVABLE_TREE);
    fixture.detectChanges();

    setValue('.line-category', '51', 'change');
    setValue('.line-amount', '180');
    setValue('.line-account', '7', 'change');
    setValue('.line-counterparty', 'Alan');
    el.querySelector<HTMLButtonElement>('.line-add')!.click();
    fixture.detectChanges();

    expect(fixture.componentInstance.members()).toEqual([
      emptyEntryInput({ account_id: 7, kind: 'receivable', amount: '180', category_id: 51, counterparty_id: 5 }),
    ]);
    expect(el.querySelector('.sheet')).toBeNull();
    expect(el.querySelector('.member')?.textContent).toContain('代付 · Alan');
    expect(el.querySelector('.member')?.textContent).toContain('180');
  });

  it('creates a new counterparty when the typed name is unknown', () => {
    openSheet();
    el.querySelectorAll<HTMLButtonElement>('.line-kind button')[3].click();
    fixture.detectChanges();
    http.expectOne(r => r.urlWithParams.includes('kind=payable')).flush([]);
    setValue('.line-amount', '100');
    setValue('.line-counterparty', 'Bob');
    el.querySelector<HTMLButtonElement>('.line-add')!.click();

    const req = http.expectOne('/api/accounting/counterparties');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ name: 'Bob' });
    req.flush({ id: 9, name: 'Bob', open_amounts: [] });
    fixture.detectChanges();

    expect(fixture.componentInstance.members()[0]).toMatchObject({ kind: 'payable', counterparty_id: 9, amount: '100' });
  });

  it('requires a counterparty for receivables and removes lines', () => {
    openSheet();
    el.querySelectorAll<HTMLButtonElement>('.line-kind button')[2].click();
    fixture.detectChanges();
    http.expectOne(r => r.urlWithParams.includes('kind=receivable')).flush(RECEIVABLE_TREE);
    setValue('.line-amount', '180');
    el.querySelector<HTMLButtonElement>('.line-add')!.click();
    fixture.detectChanges();
    expect(el.querySelector('.line-error')?.textContent).toContain('需要對象');

    fixture.componentInstance.members.set([emptyEntryInput({ account_id: 1, kind: 'expense', amount: '50' })]);
    fixture.detectChanges();
    el.querySelector<HTMLButtonElement>('.member-remove')!.click();
    fixture.detectChanges();
    expect(fixture.componentInstance.members()).toEqual([]);
  });
  it('adds the line on ⏎ and closes on Esc inside the sheet, without letting the form see either key', () => {
    openSheet();
    setValue('.line-amount', '60');
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    el.querySelector('.line-amount')!.dispatchEvent(enter);
    fixture.detectChanges();
    expect(enter.defaultPrevented).toBe(true);
    expect(fixture.componentInstance.members()).toEqual([emptyEntryInput({ account_id: 1, kind: 'expense', amount: '60' })]);
    expect(el.querySelector('.sheet')).toBeNull();

    el.querySelector<HTMLButtonElement>('.add')!.click();
    fixture.detectChanges();
    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    el.querySelector('.line-amount')!.dispatchEvent(escape);
    fixture.detectChanges();
    expect(escape.defaultPrevented).toBe(true);
    expect(el.querySelector('.sheet')).toBeNull();
    expect(fixture.componentInstance.members().length).toBe(1);
  });
});
