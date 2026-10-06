import { By } from '@angular/platform-browser';
import { AccountPickerComponent } from '../account-picker/account-picker';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { buildDefinitionInput } from '../entry-form/schedule-save';
import { nextOccurrences } from '../schedule-math';
import { makeAccount } from '../testing/fixtures';
import { ScheduleDraft, defaultDraft, tabsFor } from './schedule-draft';
import { ScheduleTabsComponent } from './schedule-tabs';

const ACCOUNTS = [makeAccount({ id: 1, name: '薪轉' }), makeAccount({ id: 2, name: '範例卡' })];

interface Setup {
  showTabs?: boolean;
  kind?: string;
  entryDate?: string;
  amount?: number | null;
  accountId?: number | null;
  errors?: Record<string, string>;
  definitionMode?: boolean;
  editing?: boolean;
  draft?: ScheduleDraft;
}

function render(setup: Setup = {}): { fixture: ComponentFixture<ScheduleTabsComponent>; el: HTMLElement } {
  TestBed.configureTestingModule({ imports: [ScheduleTabsComponent] });
  const fixture = TestBed.createComponent(ScheduleTabsComponent);
  const ref = fixture.componentRef;
  ref.setInput('kind', setup.kind ?? 'expense');
  ref.setInput('entryDate', setup.entryDate ?? '2026-10-22');
  ref.setInput('amount', setup.amount ?? null);
  ref.setInput('currency', 'TWD');
  ref.setInput('accounts', ACCOUNTS);
  ref.setInput('accountId', setup.accountId ?? 1);
  ref.setInput('errors', setup.errors ?? {});
  ref.setInput('definitionMode', setup.definitionMode ?? false);
  ref.setInput('editing', setup.editing ?? false);
  ref.setInput('showTabs', setup.showTabs ?? true);
  ref.setInput('draft', setup.draft ?? defaultDraft(setup.entryDate ?? '2026-10-22'));
  fixture.detectChanges();
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

function text(node: Element | null | undefined): string {
  return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
}

function tab(fixture: ComponentFixture<ScheduleTabsComponent>, label: string): void {
  const el = fixture.nativeElement as HTMLElement;
  Array.from(el.querySelectorAll<HTMLButtonElement>('.schedule-tab')).find(button => text(button) === label)!.click();
  fixture.detectChanges();
}

function set(fixture: ComponentFixture<ScheduleTabsComponent>, selector: string, value: string, event = 'input'): void {
  const field = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
  field.value = value;
  field.dispatchEvent(new Event(event));
  fixture.detectChanges();
}

function labels(el: HTMLElement): string[] {
  return Array.from(el.querySelectorAll('.schedule-tab')).map(text);
}

describe('ScheduleTabsComponent', () => {
  it('offers the tabs each record type allows', () => {
    const { fixture, el } = render();
    expect(labels(el)).toEqual(['單次', '週期', '分期']);
    expect(el.querySelector('.schedule-tab.on')?.textContent?.trim()).toBe('單次');
    tab(fixture, '分期');
    fixture.componentRef.setInput('kind', 'transfer');
    fixture.detectChanges();
    expect(labels(el)).toEqual(['單次', '週期']);
    expect(fixture.componentInstance.draft().tab).toBe('single');
    fixture.componentRef.setInput('kind', 'income');
    fixture.detectChanges();
    expect(labels(el)).toEqual(['單次', '週期']);
    fixture.componentRef.setInput('kind', 'system');
    fixture.detectChanges();
    expect(labels(el)).toEqual(['單次']);
  });

  it('drops 單次 in definition mode', () => {
    // A separate test: render() configures TestBed, which cannot be configured again once a component exists.
    const { el } = render({ definitionMode: true, draft: { ...defaultDraft('2026-10-22'), tab: 'recurring' } });
    expect(labels(el)).toEqual(['週期', '分期']);
  });

  it('offers only 單次 while editing an entry', () => {
    // Final review F1: an entry edit edits that one record; it never becomes a schedule.
    expect(tabsFor('expense', { editing: true })).toEqual(['single']);
    expect(tabsFor('expense')).toEqual(['single', 'recurring', 'installment']);
    const { fixture, el } = render({ editing: true, draft: { ...defaultDraft('2026-10-22'), tab: 'recurring' } });
    expect(labels(el)).toEqual(['單次']);
    expect(fixture.componentInstance.draft().tab).toBe('single');
  });

  it('starts 週期 at 每 1 月 from the entry date, 無限期, 自動入帳, with the MOZE footer', () => {
    const { fixture, el } = render();
    tab(fixture, '週期');
    expect((el.querySelector('.sched-every') as HTMLInputElement).value).toBe('1');
    expect((el.querySelector('.sched-unit') as HTMLSelectElement).value).toBe('month');
    expect((el.querySelector('.sched-start') as HTMLInputElement).value).toBe('2026-10-22');
    expect((el.querySelector('.sched-end') as HTMLSelectElement).value).toBe('never');
    expect(el.querySelector('.sched-mode[data-mode="auto"]')?.getAttribute('aria-checked')).toBe('true');
    expect(text(el.querySelector('.schedule-footer'))).toBe('週期：#1 / 無限期（每月 / 22號）');
  });

  it('counts a finite run in the footer and switches to 提醒入帳', () => {
    const { fixture, el } = render();
    tab(fixture, '週期');
    set(fixture, '.sched-end', 'times', 'change');
    expect((el.querySelector('.sched-end-times') as HTMLInputElement).value).toBe('12');
    expect(text(el.querySelector('.schedule-footer'))).toBe('週期：#1 / 12（每月 / 22號）');
    (el.querySelector('.sched-mode[data-mode="confirm"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(fixture.componentInstance.draft().mode).toBe('confirm');
    set(fixture, '.sched-every', '2');
    set(fixture, '.sched-unit', 'week', 'change');
    expect(text(el.querySelector('.schedule-footer'))).toBe('週期：#1 / 12（每 2 週 / 星期四）');
  });

  it('splits a card installment and words the 分期 footer', () => {
    // Spec "Card installment split".
    const { fixture, el } = render({ entryDate: '2026-10-03', amount: 10000 });
    tab(fixture, '分期');
    set(fixture, '.sched-periods', '3');
    expect((el.querySelector('.sched-per') as HTMLInputElement).value).toBe('3,333');
    expect(text(el.querySelector('.sched-total'))).toBe('$10,000');
    expect(text(el.querySelector('.schedule-footer'))).toBe('分期：#1 / 3（$10,000） 首期入帳日將從 2026/11/03 開始（3 期）');
    expect(text(el.querySelector('.sched-first')!.closest('.sched-row')!.querySelector('.sched-label'))).toBe('首期入帳日');
    expect(el.querySelector('.sched-repay')).toBeNull();
  });

  it('offers 還款帳戶 for 應付款項, defaulting to the entry account', () => {
    const { fixture, el } = render({ kind: 'payable', amount: 300000 });
    tab(fixture, '分期');
    const repay = fixture.debugElement.query(By.css('app-account-picker.sched-repay')).componentInstance as AccountPickerComponent;
    expect(repay.value()).toBe(1);
    expect(repay.label()).toBe('還款帳戶');
    expect(el.querySelector('.sched-repay .acct-trigger')!.getAttribute('aria-label')).toBe('還款帳戶：薪轉');
    repay.choose(2);
    fixture.detectChanges();
    expect(fixture.componentInstance.draft().repayAccountId).toBe(2);

  });

  it('moves the start and the first repayment with the entry date until they are edited', () => {
    const { fixture } = render({ entryDate: '2026-10-03' });
    tab(fixture, '分期');
    fixture.componentRef.setInput('entryDate', '2026-10-10');
    fixture.detectChanges();
    expect(fixture.componentInstance.draft().firstDate).toBe('2026-11-10');
    expect(fixture.componentInstance.draft().start).toBe('2026-10-10');
    set(fixture, '.sched-first', '2026-11-09', 'change');
    fixture.componentRef.setInput('entryDate', '2026-10-12');
    fixture.detectChanges();
    expect(fixture.componentInstance.draft().firstDate).toBe('2026-11-09');
  });

  it('shows a server field error beside its field', () => {
    const { fixture, el } = render({ errors: { times: '分期至少 2 期', 'lines[1].amount': '金額格式錯誤' } });
    tab(fixture, '分期');
    expect(text(el.querySelector('.field-error[data-field="times"]'))).toBe('分期至少 2 期');
    expect(text(el.querySelector('.field-error[data-field="interest"]'))).toBe('金額格式錯誤');
  });

  it('keeps intermediate values while typing and clamps once the field is left', () => {
    const { fixture, el } = render({ amount: 10000 });
    tab(fixture, '分期');
    set(fixture, '.sched-periods', '1');
    expect(fixture.componentInstance.draft().periods).toBe(12);
    expect((el.querySelector('.sched-periods') as HTMLInputElement).value).toBe('1');
    set(fixture, '.sched-periods', '18');
    expect(fixture.componentInstance.draft().periods).toBe(18);
    set(fixture, '.sched-periods', '', 'blur');
    expect(fixture.componentInstance.draft().periods).toBe(2);
    expect((el.querySelector('.sched-periods') as HTMLInputElement).value).toBe('2');
    tab(fixture, '週期');
    set(fixture, '.sched-every', '');
    expect(fixture.componentInstance.draft().every).toBe(1);
    set(fixture, '.sched-every', '3');
    expect(fixture.componentInstance.draft().every).toBe(3);
    set(fixture, '.sched-every', '', 'blur');
    expect((el.querySelector('.sched-every') as HTMLInputElement).value).toBe('1');
    set(fixture, '.sched-end', 'times', 'change');
    set(fixture, '.sched-end-times', '0');
    expect(fixture.componentInstance.draft().endTimes).toBe(12);
    set(fixture, '.sched-end-times', '0', 'blur');
    expect(fixture.componentInstance.draft().endTimes).toBe(1);
    expect((el.querySelector('.sched-end-times') as HTMLInputElement).value).toBe('1');
  });

  it('words the footer of a run that ends on a date', () => {
    const { fixture, el } = render();
    tab(fixture, '週期');
    set(fixture, '.sched-end', 'date', 'change');
    set(fixture, '.sched-end-date', '2027-01-31', 'change');
    expect(text(el.querySelector('.schedule-footer'))).toBe('週期：#1 / 至 2027/01/31（每月 / 22號）');
  });

  describe('R3: an explicit date change replaces the hidden rule day', () => {
    const hydrated = (overrides: Partial<ScheduleDraft>): ScheduleDraft => ({
      ...defaultDraft('2026-10-31'), start: '2026-10-31', startTouched: true, firstDate: '2026-10-31', firstTouched: true,
      dayOfMonth: 31, ...overrides,
    });
    const payload = (draft: ScheduleDraft) =>
      buildDefinitionInput({
        kind: 'expense', draft, name: '', merchant: '', description: '', tags: [], projectId: null, categoryId: 41,
        categoryName: '房租', account: ACCOUNTS[0], amount: 900, counterpartyId: null, transfer: null, transferFrom: null,
        transferTo: null, repayAccount: null, entryDate: '2026-10-03', loanEntryId: null,
      });

    it('週期: a monthly 31st definition keeps 31 until 起始日 is chosen, then takes the new day', () => {
      const { fixture, el } = render({ definitionMode: true, draft: hydrated({ tab: 'recurring', unit: 'month' }) });
      expect(text(el.querySelector('.schedule-footer'))).toContain('31號');
      set(fixture, '.sched-start', '2026-11-15', 'change');
      const draft = fixture.componentInstance.draft();
      expect(draft.dayOfMonth).toBe(15);
      expect(payload(draft)).toMatchObject({ anchor_date: '2026-11-15', day_of_month: 15 });
      expect(nextOccurrences(fixture.componentInstance.rule(), 1)[0]).toBe('2026-11-15');
      expect(text(el.querySelector('.schedule-footer'))).toContain('15號');
    });

    it('週期 by week: choosing 起始日 clears the rule day', () => {
      const { fixture } = render({ definitionMode: true, draft: hydrated({ tab: 'recurring', unit: 'week' }) });
      set(fixture, '.sched-start', '2026-11-15', 'change');
      expect(fixture.componentInstance.draft().dayOfMonth).toBeNull();
    });

    it('分期: choosing 首次還款日 takes its day', () => {
      const { fixture } = render({ definitionMode: true, amount: 27000, draft: hydrated({ tab: 'installment', periods: 3 }) });
      set(fixture, '.sched-first', '2026-11-15', 'change');
      const draft = fixture.componentInstance.draft();
      expect(draft.dayOfMonth).toBe(15);
      expect(payload(draft)).toMatchObject({ anchor_date: '2026-11-15', day_of_month: 15 });
    });
  });

  describe('N1: the rule day follows the unit and the active tab', () => {
    const payload = (draft: ScheduleDraft) =>
      buildDefinitionInput({
        kind: 'expense', draft, name: '', merchant: '', description: '', tags: [], projectId: null, categoryId: 41,
        categoryName: '房租', account: ACCOUNTS[0], amount: 900, counterpartyId: null, transfer: null, transferFrom: null,
        transferTo: null, repayAccount: null, entryDate: '2026-10-03', loanEntryId: null,
      });

    it('create: pick 起始日 on 每月, then switch the unit to 每週 / 每天 → no day_of_month', () => {
      const { fixture } = render();
      tab(fixture, '週期');
      set(fixture, '.sched-start', '2026-11-15', 'change');
      expect(fixture.componentInstance.draft().dayOfMonth).toBe(15);
      set(fixture, '.sched-unit', 'week', 'change');
      expect(payload(fixture.componentInstance.draft()).day_of_month).toBeNull();
      set(fixture, '.sched-unit', 'day', 'change');
      expect(payload(fixture.componentInstance.draft()).day_of_month).toBeNull();
      set(fixture, '.sched-unit', 'month', 'change');
      expect(payload(fixture.componentInstance.draft()).day_of_month).toBe(15);
    });

    it('the builder sends no day_of_month for a day / week unit even when the draft holds one', () => {
      const draft: ScheduleDraft = { ...defaultDraft('2026-10-22'), tab: 'recurring', unit: 'week', dayOfMonth: 31 };
      expect(payload(draft).day_of_month).toBeNull();
    });

    it('create: 分期 首次還款日, then 週期 monthly without touching 起始日 → 起始日\'s day', () => {
      const { fixture } = render({ entryDate: '2026-10-22', amount: 9000 });
      tab(fixture, '分期');
      set(fixture, '.sched-first', '2026-11-05', 'change');
      expect(fixture.componentInstance.draft().dayOfMonth).toBe(5);
      tab(fixture, '週期');
      const draft = fixture.componentInstance.draft();
      expect(payload(draft)).toMatchObject({ anchor_date: '2026-10-22', day_of_month: 22 });
      expect(nextOccurrences(fixture.componentInstance.rule(), 1)[0]).toBe('2026-10-22');
    });

    it('definition mode: switching tabs keeps a hydrated rule day whose date was not touched', () => {
      const draft: ScheduleDraft = {
        // an imported 31st anchored in November (2026-11-30 is its normalised first date)
        ...defaultDraft('2026-11-30'), tab: 'recurring', start: '2026-11-30', startTouched: true, firstDate: '2026-11-30',
        firstTouched: true, dayOfMonth: 31, dayHydrated: true,
      };
      const { fixture } = render({ definitionMode: true, kind: 'expense', draft });
      tab(fixture, '分期');
      tab(fixture, '週期');
      expect(fixture.componentInstance.draft().dayOfMonth).toBe(31);
    });
  });
  it('renders no tablist of its own when the host shows the tabs', () => {
    const { el } = render({ showTabs: false, draft: { ...defaultDraft('2026-10-22'), tab: 'recurring' } });
    expect(el.querySelector('[role="tablist"]')).toBeNull();
    expect(el.querySelector('.sched-start')).not.toBeNull();
    expect(el.querySelector('.schedule-footer')!.classList).toContain('event-summary');
  });

});
