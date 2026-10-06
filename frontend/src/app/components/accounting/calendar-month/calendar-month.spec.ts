import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DailySummaryDay } from '../../../models/accounting.model';
import { BillingEvent } from '../billing/billing-math';
import { CalendarMonthComponent } from './calendar-month';

const DAYS: DailySummaryDay[] = [
  { date: '2026-10-02', expense: '-1234.0000', income: '500.0000', count: 3 },
  { date: '2026-10-05', expense: '0.0000', income: '50042.0000', count: 2 },
];

describe('CalendarMonthComponent', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 2, 12, 0, 0));
  });

  afterEach(() => vi.useRealTimers());

  function render(inputs: Partial<{ weekStart: number; selected: string | null; today: string; billing: BillingEvent[] }> = {}) {
    TestBed.configureTestingModule({ imports: [CalendarMonthComponent] });
    const fixture = TestBed.createComponent(CalendarMonthComponent);
    fixture.componentRef.setInput('month', '2026-10');
    fixture.componentRef.setInput('days', DAYS);
    fixture.componentRef.setInput('currency', 'TWD');
    for (const [key, value] of Object.entries(inputs)) {
      fixture.componentRef.setInput(key, value);
    }
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const cell = (date: string) => el.querySelector<HTMLButtonElement>(`button.cell[data-date="${date}"]`)!;
    return { fixture, el, cell };
  }

  const text = (node: Element | null) => node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

  it('renders 42 day cells under a Sunday-first weekday header', () => {
    const { el } = render();
    expect(el.querySelectorAll('button.cell').length).toBe(42);
    expect(Array.from(el.querySelectorAll('.weekdays span')).map(text)).toEqual(['日', '一', '二', '三', '四', '五', '六']);
  });

  it('starts the header on Monday when weekStart is 1', () => {
    const { el } = render({ weekStart: 1 });
    expect(Array.from(el.querySelectorAll('.weekdays span')).map(text)).toEqual(['一', '二', '三', '四', '五', '六', '日']);
    expect(el.querySelector('button.cell')!.getAttribute('data-date')).toBe('2026-09-28');
  });

  it('shows the figures in their cells with the tone classes and an aria label', () => {
    const { cell } = render();
    const busy = cell('2026-10-02');
    expect(text(busy.querySelector('.d'))).toBe('2');
    expect(text(busy.querySelector('.exp'))).toBe('1,234');
    expect(busy.querySelector('.exp')!.classList).toContain('neg');
    expect(text(busy.querySelector('.inc'))).toBe('500');
    expect(busy.querySelector('.inc')!.classList).toContain('pos');
    expect(busy.getAttribute('aria-label')).toBe('10月2日，支出 1,234，收入 500');

    const payday = cell('2026-10-05');
    expect(payday.querySelector('.exp')).toBeNull();
    expect(text(payday.querySelector('.inc'))).toBe('5萬');
    expect(payday.getAttribute('aria-label')).toBe('10月5日，收入 50,042');
    expect(cell('2026-10-06').getAttribute('aria-label')).toBe('10月6日');
  });

  it('emits the date of a tapped cell', () => {
    const { fixture, cell } = render();
    const emitted: string[] = [];
    fixture.componentInstance.daySelected.subscribe(date => emitted.push(date));
    cell('2026-10-05').click();
    expect(emitted).toEqual(['2026-10-05']);
  });

  it('disables the cells of the adjacent months and leaves them blank', () => {
    const { cell } = render();
    const outside = cell('2026-09-30');
    expect(outside.disabled).toBe(true);
    expect(outside.classList).toContain('outside');
    expect(outside.querySelector('.exp, .inc')).toBeNull();
    expect(cell('2026-10-01').disabled).toBe(false);
  });

  it("outlines today's cell and presses the selected one", () => {
    const { cell } = render({ selected: '2026-10-05' });
    expect(cell('2026-10-02').classList).toContain('today');
    expect(cell('2026-10-02').getAttribute('aria-current')).toBe('date');
    expect(cell('2026-10-05').getAttribute('aria-pressed')).toBe('true');
    expect(cell('2026-10-05').classList).toContain('sel');
    expect(cell('2026-10-02').getAttribute('aria-pressed')).toBe('false');
  });

  describe('billing hints', () => {
    const period = { start: '2026-08-16', end: '2026-09-15' };
    const BILLING: BillingEvent[] = [
      { accountId: 1, name: '玉山 UNI', kind: 'due', date: '2026-10-05', period, nextClosing: '2026-10-15' },
      { accountId: 7, name: '富邦 J卡', kind: 'due', date: '2026-10-05', period, nextClosing: '2026-10-15' },
      { accountId: 3, name: '國泰 CUBE', kind: 'due', date: '2026-10-12', period, nextClosing: '2026-10-15' },
      { accountId: 1, name: '玉山 UNI', kind: 'closing', date: '2026-10-15', period: { start: '2026-09-16', end: '2026-10-15' }, nextClosing: '2026-11-15' },
    ];

    it('badges due days with the card count and says so in the aria label', () => {
      const { cell } = render({ billing: BILLING });
      const twoCards = cell('2026-10-05');
      const badge = twoCards.querySelector('.due-badge')!;
      expect(text(badge)).toBe('💳2');
      expect(badge.getAttribute('aria-hidden')).toBe('true');
      expect(twoCards.classList).toContain('due');
      expect(twoCards.getAttribute('aria-label')).toBe('10月5日，收入 50,042，2 張卡繳費到期');

      const oneCard = cell('2026-10-12');
      expect(text(oneCard.querySelector('.due-badge'))).toBe('💳');
      expect(oneCard.getAttribute('aria-label')).toBe('10月12日，1 張卡繳費到期');
    });

    it('marks closing days with a border class and no badge', () => {
      const { cell } = render({ billing: BILLING });
      const closing = cell('2026-10-15');
      expect(closing.classList).toContain('closing');
      expect(closing.querySelector('.due-badge')).toBeNull();
      expect(closing.getAttribute('aria-label')).toBe('10月15日');
      expect(cell('2026-10-05').classList).not.toContain('closing');
    });

    it('leaves other days without a badge', () => {
      const { el, cell } = render({ billing: BILLING });
      expect(el.querySelectorAll('.due-badge').length).toBe(2);
      expect(cell('2026-10-02').querySelector('.due-badge')).toBeNull();
      expect(cell('2026-10-02').classList).not.toContain('due');
    });

    it('shows no billing marks without events', () => {
      expect(render().el.querySelectorAll('.due-badge, .cell.closing, .cell.due').length).toBe(0);
    });
  });
  it('uses a .72rem caption fallback when rendered outside the accounting layout', () => {
    const { el, cell } = render();
    // jsdom exposes the custom-property expression; real-browser checks verify its inherited computed value.
    expect(getComputedStyle(el.querySelector('.weekdays span')!).fontSize).toBe('var(--fs-micro, 0.72rem)');
    expect(getComputedStyle(cell('2026-10-02').querySelector('.fig')!).fontSize).toBe('var(--fs-micro, 0.72rem)');
  });

});
