// frontend/src/app/components/accounting/account-picker/account-picker.spec.ts
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LedgerAccount } from '../../../models/accounting.model';
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { makeAccount } from '../testing/fixtures';
import { AccountPickerComponent } from './account-picker';
import { RECENT_ACCOUNTS_KEY } from './recent-accounts';

const WALLET = makeAccount({ id: 1, name: '錢包', icon: '👛', balance: '3070', group_name: '現金' });
const CARD = makeAccount({ id: 2, name: '玉山 UNI', icon: '💳', balance: '-27218', is_credit: true, available_credit: '251678', group_name: '信用卡' });
const BANK = makeAccount({ id: 3, name: 'Line Bank', icon: '🏦', balance: '100000', group_name: '銀行' });
const YEN = makeAccount({ id: 4, name: '日幣現金', currency: 'JPY', balance: '53635', group_name: '現金' });
const OLD = makeAccount({ id: 9, name: '舊卡', is_archived: true, group_name: '信用卡' });

function many(count: number): LedgerAccount[] {
  return Array.from({ length: count }, (_, index) => makeAccount({ id: 100 + index, name: `帳戶${index}`, group_name: '銀行' }));
}

interface Setup {
  accounts?: LedgerAccount[];
  value?: number | null;
  mode?: LayoutMode;
  allowArchived?: boolean;
  exclude?: number[];
  compact?: boolean;
}

function render(setup: Setup = {}): { fixture: ComponentFixture<AccountPickerComponent>; el: HTMLElement; picker: AccountPickerComponent } {
  TestBed.configureTestingModule({ imports: [AccountPickerComponent] });
  TestBed.inject(LayoutModeService).set(setup.mode ?? 'panes');
  const fixture = TestBed.createComponent(AccountPickerComponent);
  const ref = fixture.componentRef;
  ref.setInput('accounts', setup.accounts ?? [WALLET, CARD, BANK, YEN, OLD]);
  ref.setInput('label', '帳戶');
  ref.setInput('value', setup.value ?? null);
  ref.setInput('allowArchived', setup.allowArchived ?? false);
  ref.setInput('exclude', setup.exclude ?? []);
  ref.setInput('compact', setup.compact ?? false);
  fixture.detectChanges();
  return { fixture, el: fixture.nativeElement as HTMLElement, picker: fixture.componentInstance };
}

function text(node: Element | null | undefined): string {
  return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
}

function openPanel(fixture: ComponentFixture<AccountPickerComponent>): HTMLElement {
  (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('.acct-trigger')!.click();
  fixture.detectChanges();
  return (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('.acct-panel')!;
}

describe('AccountPickerComponent', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.removeItem(RECENT_ACCOUNTS_KEY);
  });

  it('shows icon, name and balance on the trigger, 可用 for a card, and 選擇帳戶 when empty', () => {
    const { fixture, el } = render({ value: 1 });
    const trigger = el.querySelector('.acct-trigger')!;
    expect(text(trigger.querySelector('.acct-ico'))).toBe('👛');
    expect(text(trigger.querySelector('.acct-name'))).toBe('錢包');
    expect(text(trigger.querySelector('.acct-trigger-balance'))).toBe('· $3,070');
    expect(trigger.getAttribute('aria-haspopup')).toBe('dialog');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(trigger.getAttribute('aria-label')).toBe('帳戶：錢包');
    expect(trigger.getAttribute('data-value')).toBe('1');

    fixture.componentRef.setInput('value', 2);
    fixture.detectChanges();
    expect(text(trigger.querySelector('.acct-name'))).toBe('玉山 UNI');
    expect(text(trigger.querySelector('.acct-trigger-balance'))).toBe('· 可用 $251,678');

    fixture.componentRef.setInput('value', null);
    fixture.detectChanges();
    expect(text(trigger)).toContain('選擇帳戶');
  });

  it('shows the name only when compact', () => {
    const { el } = render({ value: 1, compact: true });
    expect(text(el.querySelector('.acct-trigger'))).not.toContain('$3,070');
  });

  it('lists groups with currency badges and balances, marks the selected row, archived only when allowed', () => {
    const { fixture, el } = render({ value: 4 });
    const panel = openPanel(fixture);
    expect(el.querySelector('.acct-trigger')!.getAttribute('aria-expanded')).toBe('true');
    expect(Array.from(panel.querySelectorAll('.acct-group-name')).map(text)).toEqual(['現金', '信用卡', '銀行']);
    const yen = panel.querySelector('.acct-option[data-account-id="4"]')!;
    expect(yen.getAttribute('aria-selected')).toBe('true');
    expect(text(yen.querySelector('.acct-currency'))).toBe('JPY');
    expect(text(yen.querySelector('.acct-balance'))).toBe('¥53,635');
    expect(yen.querySelector('.acct-check')).not.toBeNull();
    expect(panel.querySelector('[role="listbox"]')!.getAttribute('aria-label')).toBe('帳戶');
    expect(panel.querySelector('.acct-option[data-account-id="9"]')).toBeNull();
  });

  it('puts archived accounts in a trailing 已封存 group when allowed', () => {
    const { fixture } = render({ allowArchived: true });
    const panel = openPanel(fixture);
    const groups = Array.from(panel.querySelectorAll('.acct-group-name')).map(text);
    expect(groups[groups.length - 1]).toBe('已封存');
  });

  it('shows the search box from 8 visible accounts and narrows by name or group', () => {
    const seven = render({ accounts: many(7) });
    expect(openPanel(seven.fixture).querySelector('.acct-search')).toBeNull();
    TestBed.resetTestingModule();

    const { fixture } = render({ accounts: [...many(7), BANK] });
    const panel = openPanel(fixture);
    const search = panel.querySelector<HTMLInputElement>('.acct-search')!;
    expect(search.getAttribute('placeholder')).toBe('搜尋帳戶');
    search.value = 'line';
    search.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(Array.from(panel.querySelectorAll('.acct-option')).map(row => row.getAttribute('data-account-id'))).toEqual(['3']);
  });

  it('hides excluded accounts', () => {
    const { fixture } = render({ exclude: [1, 2] });
    const panel = openPanel(fixture);
    expect(Array.from(panel.querySelectorAll('.acct-option')).map(row => row.getAttribute('data-account-id'))).toEqual(['4', '3']);
  });

  it('emits once on selection, closes and returns focus to the trigger; the current value emits nothing', () => {
    const { fixture, el, picker } = render({ value: 1 });
    const emitted = vi.fn();
    picker.value.subscribe(emitted);
    openPanel(fixture).querySelector<HTMLElement>('.acct-option[data-account-id="3"]')!.click();
    fixture.detectChanges();
    expect(emitted).toHaveBeenCalledTimes(1);
    expect(emitted).toHaveBeenCalledWith(3);
    expect(el.querySelector('.acct-panel')).toBeNull();
    expect(document.activeElement).toBe(el.querySelector('.acct-trigger'));

    openPanel(fixture).querySelector<HTMLElement>('.acct-option[data-account-id="3"]')!.click();
    fixture.detectChanges();
    expect(emitted).toHaveBeenCalledTimes(1);
  });

  it('offers up to four 最近使用 chips, hidden while searching', () => {
    localStorage.setItem(RECENT_ACCOUNTS_KEY, JSON.stringify([3, 999, 2, 1, 4, 100]));
    const { fixture, picker } = render({ accounts: [...many(7), WALLET, CARD, BANK, YEN] });
    const panel = openPanel(fixture);
    const chips = Array.from(panel.querySelectorAll('.acct-recent-chip'));
    expect(chips.map(chip => chip.getAttribute('data-account-id'))).toEqual(['3', '2', '1', '4']);
    (chips[1] as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(picker.value()).toBe(2);

    const again = openPanel(fixture);
    const search = again.querySelector<HTMLInputElement>('.acct-search')!;
    search.value = '錢';
    search.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(again.querySelector('.acct-recent')).toBeNull();
  });

  it('works without a 最近使用 row when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const { fixture, picker } = render();
    const panel = openPanel(fixture);
    expect(panel.querySelector('.acct-recent')).toBeNull();
    panel.querySelector<HTMLElement>('.acct-option[data-account-id="1"]')!.click();
    expect(picker.value()).toBe(1);
  });

  it('uses a bottom sheet on a phone and a popover elsewhere, both marked as overlays', () => {
    const phone = render({ mode: 'phone' });
    openPanel(phone.fixture);
    expect(phone.el.querySelector('.overlay[data-overlay] .sheet.acct-panel')).not.toBeNull();
    phone.el.querySelector<HTMLElement>('.overlay')!.click();
    phone.fixture.detectChanges();
    expect(phone.el.querySelector('.acct-panel')).toBeNull();
    TestBed.resetTestingModule();

    const desk = render({ mode: 'sheet' });
    openPanel(desk.fixture);
    expect(desk.el.querySelector('.acct-popover.acct-panel[data-overlay]')).not.toBeNull();
  });
});
