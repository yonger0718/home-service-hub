import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { MonthSummary } from '../../../models/accounting.model';
import { monthLabel } from '../dates';
import { categoryDistribution, distributionAriaText } from '../timeline/category-distribution';
import { DueItem } from './due-items';
import type { OverviewState } from './overview.service';
import { MonthOverviewComponent } from './month-overview';

function cat(name: string, expense: string, id: number) {
  return { category_id: id, name, color: '#123456', expense };
}

const SUMMARY: MonthSummary = {
  month: '2026-10',
  currency: 'TWD',
  expense: '-4700',
  income: '0',
  net: '-4700',
  missing_rates: [],
  by_category: [cat('餐飲', '-3000', 1), cat('交通', '-1200', 2), cat('娛樂', '-500', 3)],
};

function create(inputs: { summary?: MonthSummary | null; dueItems?: DueItem[]; summaryState?: OverviewState; dueState?: OverviewState }) {
  const fixture = TestBed.createComponent(MonthOverviewComponent);
  fixture.componentRef.setInput('month', '2026-10');
  if (inputs.summary !== undefined) fixture.componentRef.setInput('summary', inputs.summary);
  if (inputs.dueItems) fixture.componentRef.setInput('dueItems', inputs.dueItems);
  if (inputs.summaryState) fixture.componentRef.setInput('summaryState', inputs.summaryState);
  if (inputs.dueState) fixture.componentRef.setInput('dueState', inputs.dueState);
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('MonthOverviewComponent', () => {
  it('titles the month and lists the distribution with amounts', () => {
    const el = create({ summary: SUMMARY });

    expect(el.querySelector('h2.ov-month')!.textContent!.trim()).toBe(monthLabel('2026-10'));
    expect(el.querySelectorAll('.dist-bar span').length).toBe(3);
    const rows = Array.from(el.querySelectorAll('.dist-row')).map(r => r.textContent!);
    for (const [name, amount] of [['餐飲', '3,000'], ['交通', '1,200'], ['娛樂', '500']]) {
      expect(rows.some(t => t.includes(name) && t.includes(amount))).toBe(true);
    }
    const bar = el.querySelector('.dist-bar')!;
    expect(bar.getAttribute('role')).toBe('img');
    expect(bar.getAttribute('aria-label')).toBe(distributionAriaText(categoryDistribution(SUMMARY)));
  });

  it('renders the empty lines without data', () => {
    const el = create({ summary: null, dueItems: [] });

    expect(el.textContent).toContain('本月尚無支出');
    expect(el.textContent).toContain('沒有即將到期的項目');
    expect(el.querySelector('.dist-bar')).toBeNull();
    expect(el.querySelector('.due-row')).toBeNull();
  });

  it('lists at most the given due items with icon, name, countdown and amount', () => {
    const el = create({
      summary: SUMMARY,
      dueItems: [
        { key: 'b1', kind: 'bill', name: '玉山 UNI', date: '2026-10-28', amountText: '$12,345', dueText: '還有 3 天' },
        { key: 's1', kind: 'schedule', name: '房租', date: '2026-10-30', amountText: null, dueText: '還有 5 天' },
      ],
    });

    const rows = el.querySelectorAll('.due-row');
    expect(rows.length).toBe(2);
    expect(rows[0].querySelector('i.pi-credit-card[aria-hidden="true"]')).not.toBeNull();
    expect(rows[1].querySelector('i.pi-replay')).not.toBeNull();
    expect(rows[0].textContent).toContain('玉山 UNI');
    expect(rows[0].textContent).toContain('還有 3 天');
    expect(rows[0].textContent).toContain('$12,345');
    expect(el.querySelector('a, button')).toBeNull();
  });

  describe('states', () => {
    it('summary loading: muted 讀取中, no empty copy, no distribution', () => {
      const el = create({ summary: null, summaryState: 'loading' });
      const titles = el.querySelectorAll('h3.ov-title');
      expect(titles[0].nextElementSibling!.textContent!.trim()).toBe('讀取中');
      expect(el.textContent).not.toContain('本月尚無支出');
      expect(el.querySelector('.dist-bar')).toBeNull();
    });
    it('summary error: 摘要讀取失敗, no link, no empty copy', () => {
      const el = create({ summary: null, summaryState: 'error' });
      expect(el.querySelector('h3.ov-title')!.nextElementSibling!.textContent!.trim()).toBe('摘要讀取失敗');
      expect(el.textContent).not.toContain('本月尚無支出');
      expect(el.querySelector('a, button')).toBeNull();
    });
    it('summary ready without spend: 本月尚無支出', () => {
      const el = create({ summary: null, summaryState: 'ready' });
      expect(el.textContent).toContain('本月尚無支出');
      expect(el.textContent).not.toContain('讀取');
    });
    it('due loading: 讀取中 and not the empty copy', () => {
      const el = create({ summary: SUMMARY, dueItems: [], dueState: 'loading' });
      expect(el.querySelectorAll('h3.ov-title')[1].nextElementSibling!.textContent!.trim()).toBe('讀取中');
      expect(el.textContent).not.toContain('沒有即將到期的項目');
    });
    it('due error: 讀取失敗, no link, no empty copy', () => {
      const el = create({ summary: SUMMARY, dueItems: [], dueState: 'error' });
      expect(el.querySelectorAll('h3.ov-title')[1].nextElementSibling!.textContent!.trim()).toBe('讀取失敗');
      expect(el.textContent).not.toContain('沒有即將到期的項目');
      expect(el.querySelector('a, button')).toBeNull();
    });
    it('due ready and empty: 沒有即將到期的項目', () => {
      const el = create({ summary: SUMMARY, dueItems: [], dueState: 'ready' });
      expect(el.textContent).toContain('沒有即將到期的項目');
    });
  });
});
