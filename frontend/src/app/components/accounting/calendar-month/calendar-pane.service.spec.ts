import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { currentMonth, todayIso } from '../dates';
import { CalendarPaneService } from './calendar-pane.service';
import { TimelineRow } from '../timeline/timeline';

describe('CalendarPaneService', () => {
  it('clear() puts every signal back to its initial value', () => {
    const pane = TestBed.inject(CalendarPaneService);
    pane.month.set('2020-01');
    pane.today.set('2020-01-02');
    pane.selectedDay.set('2020-01-02');
    pane.dayRows.set([{ key: 'e1' } as TimelineRow]);
    pane.dayNet.set({ net: 1, text: '+$1', excluded: false });
    pane.dayState.set('error');
    pane.billLines.set([{ key: 'b', accountId: 1, name: 'x', amountText: '$1', paidText: null }]);
    pane.todayCard.set({ date: '2020-01-02', count: 1, names: 'a', netText: '$1', net: 1 });
    pane.merchants.set([{ key: 'm' } as never]);
    pane.merchantsState.set('error');
    pane.invoices.set([{ id: 1 } as never]);
    pane.monthDues.set([{ key: 'k', accountId: 1, name: 'n', date: '2020-01-02', amountText: '$1' }]);
    pane.search.set('abc');
    pane.searchDays.set([{ date: '2020-01-02' } as never]);
    pane.searchState.set('loading');
    pane.commands.set({ stepEntry: vi.fn(), openInvoice: vi.fn(), setQuery: vi.fn() });

    pane.clear();

    expect(pane.month()).toBe(currentMonth());
    expect(pane.today()).toBe(todayIso());
    expect(pane.selectedDay()).toBeNull();
    expect(pane.dayRows()).toEqual([]);
    expect(pane.dayNet()).toBeNull();
    expect(pane.dayState()).toBe('ready');
    expect(pane.billLines()).toEqual([]);
    expect(pane.todayCard()).toBeNull();
    expect(pane.merchants()).toEqual([]);
    expect(pane.merchantsState()).toBe('loading');
    expect(pane.invoices()).toEqual([]);
    expect(pane.monthDues()).toEqual([]);
    expect(pane.search()).toBe('');
    expect(pane.searchDays()).toEqual([]);
    expect(pane.searchState()).toBe('ready');
    expect(pane.commands()).toBeNull();
  });
});
