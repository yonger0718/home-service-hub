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

  const OCT: DailySummaryDay[] = [
    { date: '2026-10-05', expense: '0.0000', income: '72000.0000', count: 1 },
    { date: '2026-10-19', expense: '-12120.0000', income: '0.0000', count: 4 },
    { date: '2026-10-21', expense: '-3096.0000', income: '0.0000', count: 3 },
    { date: '2026-10-23', expense: '250.0000', income: '0.0000', count: 1 },
  ];

  function render(
    inputs: Partial<{
      weekStart: number;
      selected: string | null;
      today: string;
      billing: BillingEvent[];
      days: DailySummaryDay[];
      dots: ReadonlyMap<string, readonly string[]>;
      invoiceDays: ReadonlyMap<string, number>;
      paidDays: ReadonlySet<string>;
      compact: boolean;
      weekOf: string | null;
      loading: boolean;
      failed: boolean;
    }> = {},
  ) {
    TestBed.resetTestingModule();
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

  it('shows the net in the cell with tone classes and an aria label', () => {
    const { cell } = render({ today: '2026-10-20' });
    const busy = cell('2026-10-02');
    expect(text(busy.querySelector('.d'))).toBe('2');
    expect(text(busy.querySelector('.net'))).toBe('−734');
    expect(busy.querySelector('.net')!.classList).toContain('neg');
    expect(busy.getAttribute('aria-label')).toBe('10 月 2 日 週五，3 筆，支出 $1,234，收入 $500');

    const payday = cell('2026-10-05');
    expect(text(payday.querySelector('.net'))).toBe('+50,042');
    expect(payday.querySelector('.net')!.classList).toContain('pos');
    expect(payday.getAttribute('aria-label')).toContain('收入');
    expect(cell('2026-10-06').getAttribute('aria-label')).toBe('10 月 6 日 週二');
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
    expect(outside.querySelector('.net, .heat, .dots')).toBeNull();
    expect(cell('2026-10-01').disabled).toBe(false);
  });

  it('week mode: an outside cell is enabled and emits its date when picked', () => {
    const { fixture, cell } = render({ weekStart: 0, weekOf: '2026-10-01' });
    const emitted: string[] = [];
    fixture.componentInstance.daySelected.subscribe(date => emitted.push(date));
    const outside = cell('2026-09-28');
    expect(outside.disabled).toBe(false);
    expect(outside.classList).toContain('outside');
    expect(outside.getAttribute('aria-pressed')).toBe('false');
    outside.click();
    expect(emitted).toEqual(['2026-09-28']);
  });

  it('month mode: an outside cell stays disabled', () => {
    const { cell } = render({ weekStart: 0 });
    expect(cell('2026-09-28').disabled).toBe(true);
    expect(cell('2026-09-28').hasAttribute('aria-pressed')).toBe(false);
  });

  it('week mode: ArrowRight from 10/31 moves focus and tabindex to the outside cell 11/01 (Review Focus 8)', () => {
    const { fixture, el, cell } = render({ weekStart: 1, weekOf: '2026-10-31', selected: '2026-10-31', today: '2026-10-20' });
    document.body.appendChild(el);
    cell('2026-10-31').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    fixture.detectChanges();
    expect(document.activeElement).toBe(cell('2026-11-01'));
    expect(cell('2026-11-01').getAttribute('tabindex')).toBe('0');
    el.remove();
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

    it('marks due days with the card count in the aria label and no badge', () => {
      const { cell } = render({ billing: BILLING });
      const twoCards = cell('2026-10-05');
      expect(twoCards.querySelector('.due-badge')).toBeNull();
      expect(twoCards.querySelector('i.pi-credit-card')).toBeNull();
      expect(text(twoCards.querySelector('.bill-mark'))).toBe('帳單');
      expect(twoCards.classList).toContain('due');
      expect(twoCards.getAttribute('aria-label')).toContain('2 張卡繳費到期');
      expect(cell('2026-10-12').getAttribute('aria-label')).toContain('1 張卡繳費到期');
    });

    it('marks closing days with a border class and no mark', () => {
      const { cell } = render({ billing: BILLING });
      const closing = cell('2026-10-15');
      expect(closing.classList).toContain('closing');
      expect(closing.querySelector('.bill-mark, .bill-dot')).toBeNull();
      expect(closing.getAttribute('aria-label')).toBe('10 月 15 日 週四');
      expect(cell('2026-10-05').classList).not.toContain('closing');
    });

    it('leaves other days without a mark', () => {
      const { el, cell } = render({ billing: BILLING });
      expect(el.querySelectorAll('.bill-mark').length).toBe(2);
      expect(cell('2026-10-02').querySelector('.bill-mark')).toBeNull();
      expect(cell('2026-10-02').classList).not.toContain('due');
    });

    it('shows no billing marks without events', () => {
      expect(render().el.querySelectorAll('.bill-mark, .bill-dot, .cell.closing, .cell.due').length).toBe(0);
    });
  });
  it('uses a .72rem caption fallback when rendered outside the accounting layout', () => {
    const { el, cell } = render();
    // jsdom exposes the custom-property expression; real-browser checks verify its inherited computed value.
    expect(getComputedStyle(el.querySelector('.weekdays span')!).fontSize).toBe('var(--fs-micro, 0.72rem)');
    expect(getComputedStyle(cell('2026-10-02').querySelector('.net')!).fontSize).toBe('var(--fs-micro, 0.72rem)');
  });


  describe('ledger cells', () => {
    const base = { days: OCT, today: '2026-10-22' };
    const BILL: BillingEvent[] = [
      { accountId: 1, name: 'A', kind: 'due', date: '2026-10-05', period: { start: '2026-08-16', end: '2026-09-15' }, nextClosing: '2026-10-15' },
      { accountId: 7, name: 'B', kind: 'due', date: '2026-10-05', period: { start: '2026-08-16', end: '2026-09-15' }, nextClosing: '2026-10-15' },
      { accountId: 3, name: 'C', kind: 'due', date: '2026-10-12', period: { start: '2026-08-16', end: '2026-09-15' }, nextClosing: '2026-10-15' },
    ];

    it('spans the heaviest day and about a quarter on 10/21; no bar on a refund-heavy or income-only day', () => {
      const { cell } = render(base);
      const width = (d: string) => cell(d).querySelector<HTMLElement>('.heat')?.style.width;
      expect(width('2026-10-19')).toBe('100%');
      expect(parseFloat(width('2026-10-21')!)).toBeCloseTo(25.5, 0);
      expect(cell('2026-10-23').querySelector('.heat')).toBeNull();
      expect(cell('2026-10-05').querySelector('.heat')).toBeNull();
    });

    it('shows net = income + expense: -3,096 wide, -3.1k compact, +72,000 / +72k', () => {
      const wide = render(base);
      expect(text(wide.cell('2026-10-21').querySelector('.net'))).toBe('−3,096');
      expect(text(wide.cell('2026-10-05').querySelector('.net'))).toBe('+72,000');
      const narrow = render({ ...base, compact: true });
      expect(text(narrow.cell('2026-10-21').querySelector('.net'))).toBe('−3.1k');
      expect(text(narrow.cell('2026-10-05').querySelector('.net'))).toBe('+72k');
    });

    it('shows a dash for a quiet past day on wide, nothing compact, and dims future days without a figure', () => {
      const wide = render(base);
      expect(text(wide.cell('2026-10-06').querySelector('.net'))).toBe('—');
      expect(wide.cell('2026-10-06').querySelector('.net')!.classList).toContain('quiet');
      expect(wide.cell('2026-10-25').classList).toContain('future');
      expect(wide.cell('2026-10-25').querySelector('.net')).toBeNull();
      const narrow = render({ ...base, compact: true });
      expect(narrow.cell('2026-10-06').querySelector('.net')).toBeNull();
      expect(narrow.cell('2026-10-25').querySelector('.net')).toBeNull();
    });

    it('draws no bar and no dots on a future day, and ignores future spend for the month maximum', () => {
      const days = [...OCT, { date: '2026-10-28', expense: '-99999.0000', income: '0.0000', count: 2 }];
      const dots = new Map([['2026-10-28', ['#a']], ['2026-10-21', ['#b']]]);
      const { cell } = render({ ...base, days, dots });
      expect(cell('2026-10-28').querySelector('.heat, .dots')).toBeNull();
      expect(cell('2026-10-19').querySelector<HTMLElement>('.heat')!.style.width).toBe('100%');
      expect(cell('2026-10-21').querySelector('.dots')).not.toBeNull();
    });

    it('shows no quiet-day dash while loading or after a failed daily summary', () => {
      for (const state of [{ loading: true }, { failed: true }]) {
        const { cell } = render({ ...base, days: [], ...state });
        expect(cell('2026-10-06').querySelector('.net')).toBeNull();
        expect(cell('2026-10-06').getAttribute('aria-label')).toBe('10 月 6 日 週二');
      }
    });

    it('draws up to three dots then +N, none in week mode, none when the map is empty', () => {
      const dots = new Map([['2026-10-21', ['#a', '#b', '#c', '#d']]]);
      const { cell, el, fixture } = render({ ...base, dots });
      expect(cell('2026-10-21').querySelectorAll('span.dot').length).toBe(3);
      expect(text(cell('2026-10-21').querySelector('.more'))).toBe('+1');
      expect(cell('2026-10-19').querySelector('.dot')).toBeNull();
      fixture.componentRef.setInput('weekOf', '2026-10-21');
      fixture.detectChanges();
      expect(el.querySelectorAll('.week').length).toBe(1);
      expect(el.querySelector('.week button[data-date="2026-10-21"]')).not.toBeNull();
      expect(el.querySelectorAll('.dot').length).toBe(0);
      expect(render(base).el.querySelectorAll('.dot').length).toBe(0);
    });

    it('marks bills as text wide and as dots compact; a paid day reads paid in the name', () => {
      const paidDays = new Set(['2026-10-12']);
      const wide = render({ ...base, billing: BILL, paidDays });
      expect(text(wide.cell('2026-10-05').querySelector('.bill-mark'))).toBe('帳單');
      expect(wide.cell('2026-10-05').getAttribute('aria-label')).toContain('2 張卡繳費到期');
      expect(text(wide.cell('2026-10-12').querySelector('.bill-mark'))).toBe('已繳');
      expect(wide.cell('2026-10-12').getAttribute('aria-label')).toContain('已繳');
      const narrow = render({ ...base, billing: BILL, paidDays, compact: true });
      expect(narrow.cell('2026-10-05').querySelector('.bill-dot')).not.toBeNull();
      expect(narrow.cell('2026-10-05').querySelector('.bill-mark')).toBeNull();
      expect(text(narrow.cell('2026-10-05'))).not.toContain('帳單');
    });

    it('marks invoice days and ends the accessible name with the reconcile hint', () => {
      const invoiceDays = new Map([['2026-10-22', 1]]);
      const wide = render({ ...base, invoiceDays });
      expect(text(wide.cell('2026-10-22').querySelector('.inv-mark'))).toBe('發票待對帳');
      expect(wide.cell('2026-10-22').getAttribute('aria-label')).toBe('10 月 22 日 週四，發票待對帳');
      const narrow = render({ ...base, invoiceDays, compact: true });
      expect(narrow.cell('2026-10-22').querySelector('.inv-dot')).not.toBeNull();
      expect(narrow.cell('2026-10-22').querySelector('.inv-mark')).toBeNull();
    });

    it('hides the bar and dots from assistive technology', () => {
      const dots = new Map([['2026-10-19', ['#a']]]);
      const { cell } = render({ ...base, dots });
      expect(cell('2026-10-19').querySelector('.heat')!.getAttribute('aria-hidden')).toBe('true');
      expect(cell('2026-10-19').querySelector('.dots')!.getAttribute('aria-hidden')).toBe('true');
    });

    it('[ and ] emit dayStep and are cancelled; with a modifier they are ignored', () => {
      const { fixture, el } = render({ ...base, selected: '2026-10-21' });
      const steps: number[] = [];
      fixture.componentInstance.dayStep.subscribe(step => steps.push(step));
      const press = (key: string, init: KeyboardEventInit = {}) => {
        const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
        el.querySelector('.cal')!.dispatchEvent(event);
        return event;
      };
      expect(press(']').defaultPrevented).toBe(true);
      expect(press('[').defaultPrevented).toBe(true);
      expect(press(']', { ctrlKey: true }).defaultPrevented).toBe(false);
      expect(press('[', { shiftKey: true }).defaultPrevented).toBe(false);
      expect(steps).toEqual([1, -1]);
    });

    it('an arrow after ] still moves the roving focus', () => {
      const { fixture, el, cell } = render({ ...base, selected: '2026-10-21' });
      const press = (key: string) => {
        el.querySelector('.cal')!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
        fixture.detectChanges();
      };
      cell('2026-10-21').focus();
      press(']');
      press('ArrowRight');
      expect(document.activeElement).toBe(cell('2026-10-22'));
    });

    it('is one tab stop: the selected day (else today, else the 1st) has tabindex 0 and arrows move the focus', () => {
      const { fixture, el, cell } = render({ ...base, selected: '2026-10-21' });
      const stops = () => Array.from(el.querySelectorAll('button.cell')).filter(b => b.getAttribute('tabindex') === '0').map(b => b.getAttribute('data-date'));
      expect(stops()).toEqual(['2026-10-21']);
      const press = (key: string) => {
        el.querySelector('.cal')!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
        fixture.detectChanges();
      };
      cell('2026-10-21').focus();
      press('ArrowDown');
      expect(document.activeElement).toBe(cell('2026-10-28'));
      expect(stops()).toEqual(['2026-10-28']);
      press('ArrowRight');
      press('ArrowRight');
      press('ArrowRight');
      expect(stops()).toEqual(['2026-10-31']);
      press('ArrowRight');
      expect(stops()).toEqual(['2026-10-31']);
      press('Home');
      expect(stops()).toEqual(['2026-10-01']);
      press('ArrowUp');
      expect(stops()).toEqual(['2026-10-01']);
      press('End');
      expect(stops()).toEqual(['2026-10-31']);
      press('ArrowLeft');
      press('ArrowUp');
      expect(stops()).toEqual(['2026-10-23']);
      press('ArrowDown');
      press('ArrowDown');
      expect(stops()).toEqual(['2026-10-30']);

      const emitted: string[] = [];
      fixture.componentInstance.daySelected.subscribe(date => emitted.push(date));
      cell('2026-10-30').click();
      expect(emitted).toEqual(['2026-10-30']);
      fixture.detectChanges();
      expect(stops()).toEqual(['2026-10-30']);
      cell('2026-10-12').click();
      fixture.detectChanges();
      expect(stops()).toEqual(['2026-10-12']);
    });

    it('keeps one tab stop when the selection is in another month', () => {
      const { el } = render({ ...base, selected: '2026-09-30' });
      const stops = Array.from(el.querySelectorAll('button.cell')).filter(b => b.getAttribute('tabindex') === '0');
      expect(stops.map(b => b.getAttribute('data-date'))).toEqual(['2026-10-22']);
      const other = render({ days: OCT, today: '2026-11-20', selected: '2026-09-30' });
      const first = Array.from(other.el.querySelectorAll('button.cell')).filter(b => b.getAttribute('tabindex') === '0');
      expect(first.map(b => b.getAttribute('data-date'))).toEqual(['2026-10-01']);
    });

    it('keeps one tab stop in week mode and ignores arrows leaving the visible week', () => {
      const { fixture, el } = render({ ...base, weekOf: '2026-10-21', selected: '2026-10-05', today: '2026-11-20' });
      const stops = () => Array.from(el.querySelectorAll('button.cell')).filter(b => b.getAttribute('tabindex') === '0').map(b => b.getAttribute('data-date'));
      expect(el.querySelectorAll('button.cell').length).toBe(7);
      expect(stops()).toEqual(['2026-10-18']);
      const first = stops()[0];
      const event = new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true });
      el.querySelector('.cal')!.dispatchEvent(event);
      fixture.detectChanges();
      // A handled key is always cancelled, even when the move is refused, so the layout's ↑/↓ row stepping stays out.
      expect(event.defaultPrevented).toBe(true);
      expect(stops()).toEqual([first]);
    });

    it('cancels a refused arrow at the grid edge but leaves Alt/Ctrl/Meta/Shift chords to the browser', () => {
      const { el } = render({ ...base, selected: '2026-10-01' });
      const press = (init: KeyboardEventInit) => {
        const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
        el.querySelector('.cal')!.dispatchEvent(event);
        return event;
      };
      expect(press({ key: 'ArrowLeft' }).defaultPrevented).toBe(true);
      expect(press({ key: 'ArrowLeft', altKey: true }).defaultPrevented).toBe(false);
      expect(press({ key: 'ArrowRight', ctrlKey: true }).defaultPrevented).toBe(false);
      expect(press({ key: 'ArrowRight', metaKey: true }).defaultPrevented).toBe(false);
      expect(press({ key: 'ArrowRight', shiftKey: true }).defaultPrevented).toBe(false);
    });

    it('does not cancel Enter or Space', () => {
      const { el } = render({ ...base, selected: '2026-10-21' });
      for (const key of ['Enter', ' ']) {
        const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
        el.querySelector('.cal')!.dispatchEvent(event);
        expect(event.defaultPrevented).toBe(false);
      }
    });

    it('falls back to today, then the 1st, for the tab stop', () => {
      const inMonth = render(base);
      expect(inMonth.cell('2026-10-22').getAttribute('tabindex')).toBe('0');
      const elsewhere = render({ days: OCT, today: '2026-11-20' });
      expect(elsewhere.cell('2026-10-01').getAttribute('tabindex')).toBe('0');
    });

    it('mutes weekend day numbers', () => {
      const { cell } = render(base);
      expect(cell('2026-10-03').querySelector('.d')!.classList).toContain('weekend');
      expect(cell('2026-10-04').querySelector('.d')!.classList).toContain('weekend');
      expect(cell('2026-10-05').querySelector('.d')!.classList).not.toContain('weekend');
    });

    it('shows the legend only on wide month mode', () => {
      expect(render(base).el.querySelector('.legend')).not.toBeNull();
      expect(render({ ...base, compact: true }).el.querySelector('.legend')).toBeNull();
      expect(render({ ...base, weekOf: '2026-10-21' }).el.querySelector('.legend')).toBeNull();
    });
  });
});
