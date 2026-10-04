import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { makeAccount } from '../testing/fixtures';
import { ScheduleDraft, defaultDraft } from './schedule-draft';
import { ScheduleTabsComponent } from './schedule-tabs';

const ACCOUNTS = [makeAccount({ id: 1, name: '薪轉' }), makeAccount({ id: 2, name: '範例卡' })];

interface Setup {
  kind?: string;
  entryDate?: string;
  amount?: number | null;
  accountId?: number | null;
  errors?: Record<string, string>;
  definitionMode?: boolean;
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
    expect(text(el.querySelector('.schedule-footer'))).toBe('分期：#1 / 3（$10,000） 首次還款日將從 2026/11/03 開始進行（3 期）');
    expect(el.querySelector('.sched-repay')).toBeNull();
  });

  it('offers 還款帳戶 for 應付款項, defaulting to the entry account', () => {
    const { fixture, el } = render({ kind: 'payable', amount: 300000 });
    tab(fixture, '分期');
    const repay = el.querySelector('.sched-repay') as HTMLSelectElement;
    expect(repay.value).toBe('1');
    set(fixture, '.sched-repay', '2', 'change');
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
});
