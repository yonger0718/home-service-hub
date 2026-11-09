import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { BillLine, TimelineDay, TimelineRow } from '../timeline/timeline';
import { CalendarDayPanelComponent } from './calendar-day-panel';

function row(id: number, extra: Partial<TimelineRow> = {}): TimelineRow {
  return {
    key: `e${id}`,
    entryId: id,
    memberIds: [id],
    icon: '🍜',
    color: '#fde68a',
    title: `午餐 ${id}`,
    sub: '現金',
    amountText: '−$120',
    tone: 'out',
    fx: null,
    pills: [],
    groupCount: null,
    folder: null,
    invoiceHits: null,
    time: null,
    ...extra,
  };
}

const BILL: BillLine = { key: 'b1', accountId: 7, name: '範例卡', amountText: '$1,000', paidText: null };

describe('CalendarDayPanelComponent', () => {
  function render(inputs: Record<string, unknown> = {}) {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ imports: [CalendarDayPanelComponent] });
    const fixture = TestBed.createComponent(CalendarDayPanelComponent);
    fixture.componentRef.setInput('today', '2026-10-25');
    fixture.componentRef.setInput('day', '2026-10-21');
    for (const [key, value] of Object.entries(inputs)) {
      fixture.componentRef.setInput(key, value);
    }
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const cmp = fixture.componentInstance;
    const spies = { step: vi.fn(), clear: vi.fn(), open: vi.fn(), add: vi.fn(), openCard: vi.fn(), retry: vi.fn(), loadMore: vi.fn() };
    cmp.retry.subscribe(spies.retry);
    cmp.loadMore.subscribe(spies.loadMore);
    cmp.step.subscribe(spies.step);
    cmp.clear.subscribe(spies.clear);
    cmp.open.subscribe(spies.open);
    cmp.add.subscribe(spies.add);
    cmp.openCard.subscribe(spies.openCard);
    return { fixture, el, spies };
  }
  const text = (node: Element | null) => node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const key = (el: Element, k: string, init: KeyboardEventInit = {}) => {
    const event = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init });
    el.dispatchEvent(event);
    return event;
  };

  it('shows the day header with count and net, the bill line and rows with time', () => {
    const rows = [row(1, { time: '12:30' }), row(2), row(3)];
    const { el } = render({ rows, net: { net: -3096, text: '−$3,096', excluded: false }, billLines: [BILL] });
    expect(text(el.querySelector('.dp-label'))).toBe('10/21 週三');
    expect(text(el.querySelector('.dp-sub'))).toBe('3 筆 · −$3,096');
    expect(el.querySelectorAll('.dp-row').length).toBe(3);
    expect(text(el.querySelector('.dp-row .sub')).startsWith('12:30 · ')).toBe(true);
    expect(text(el.querySelectorAll('.dp-row .sub')[1])).toBe('現金');
    expect(text(el.querySelector('button.bill'))).toContain('範例卡 帳單 $1,000 · 到期');
  });

  it('shows 這天沒有記錄 and ＋ 在這天記一筆 for a past day, 還沒到這天 for a future one, and emits add with the date', () => {
    const past = render();
    expect(text(past.el.querySelector('.dp-empty'))).toBe('這天沒有記錄');
    past.el.querySelector<HTMLButtonElement>('.dp-add')!.click();
    expect(past.spies.add).toHaveBeenCalledWith('2026-10-21');
    const future = render({ day: '2026-10-30' });
    expect(text(future.el.querySelector('.dp-empty'))).toBe('還沒到這天');
    expect(text(future.el.querySelector('.dp-add'))).toBe('＋ 在這天記一筆');
    const filled = render({ rows: [row(1)] });
    expect(filled.el.querySelector('.dp-empty')).toBeNull();
    expect(text(filled.el.querySelector('.dp-add'))).toBe('＋ 在這天記一筆');
  });

  it('emits step from ‹ › and from [ ] keys, clear from 本月摘要, open from a row, openCard from a bill line', () => {
    const rows = [row(1)];
    const { fixture, el, spies } = render({ rows, billLines: [BILL] });
    el.querySelector<HTMLButtonElement>('[aria-label="前一天"]')!.click();
    el.querySelector<HTMLButtonElement>('[aria-label="後一天"]')!.click();
    expect(spies.step.mock.calls).toEqual([[-1], [1]]);
    spies.step.mockClear();
    const a = key(el.querySelector('.dp-row')!, ']');
    const b = key(el.querySelector('.dp-row')!, '[');
    expect(spies.step.mock.calls).toEqual([[1], [-1]]);
    expect(a.defaultPrevented && b.defaultPrevented).toBe(true);
    spies.step.mockClear();
    expect(key(el.querySelector('.dp-row')!, ']', { ctrlKey: true }).defaultPrevented).toBe(false);
    expect(key(el.querySelector('.dp-row')!, 'a').defaultPrevented).toBe(false);
    const editable = document.createElement('div');
    editable.setAttribute('contenteditable', 'true');
    for (const field of [document.createElement('input'), document.createElement('textarea'), document.createElement('select'), editable]) {
      el.appendChild(field);
      const event = key(field, ']');
      expect(event.defaultPrevented).toBe(false);
      field.remove();
    }
    expect(spies.step).not.toHaveBeenCalled();
    el.querySelector<HTMLButtonElement>('.dp-summary')!.click();
    expect(spies.clear).toHaveBeenCalled();
    el.querySelector<HTMLButtonElement>('.dp-row')!.click();
    expect(spies.open).toHaveBeenCalledWith(rows[0]);
    el.querySelector<HTMLButtonElement>('button.bill')!.click();
    expect(spies.openCard).toHaveBeenCalledWith(7);
    fixture.componentRef.setInput('mode', 'results');
    fixture.detectChanges();
    spies.step.mockClear();
    key(el, ']');
    expect(spies.step).not.toHaveBeenCalled();
  });

  it("marks the open entry's row aria-current and keeps data-entry-id / data-member-ids / data-split", () => {
    const rows = [row(1), row(2, { memberIds: [2, 3], folder: { icons: [], count: 2 } })];
    const { el } = render({ rows, selectedId: 2 });
    const [first, second] = Array.from(el.querySelectorAll('.dp-row'));
    expect(first.getAttribute('aria-current')).toBeNull();
    expect(first.getAttribute('data-split')).toBeNull();
    expect(second.getAttribute('aria-current')).toBe('true');
    expect(second.getAttribute('data-entry-id')).toBe('2');
    expect(second.getAttribute('data-member-ids')).toBe('2 3');
    expect(second.getAttribute('data-split')).toBe('');
    expect(second.querySelector('app-split-folder')).not.toBeNull();
  });

  it('shows skeleton while loading and 記錄讀取失敗 on error', () => {
    expect(render({ state: 'loading' }).el.querySelector('app-skeleton')).not.toBeNull();
    const error = render({ state: 'error' });
    expect(text(error.el)).toContain('記錄讀取失敗');
    expect(error.el.querySelector('.dp-row')).toBeNull();
  });

  it('offers 重試 under 記錄讀取失敗 in day mode', () => {
    const { el, spies } = render({ state: 'error' });
    const retry = el.querySelector<HTMLButtonElement>('.dp-retry')!;
    expect(text(retry)).toBe('重試');
    retry.click();
    expect(spies.retry).toHaveBeenCalledTimes(1);
    expect(render().el.querySelector('.dp-retry')).toBeNull();
  });

  it('says 僅計主幣種 when the day net leaves out rows in another currency', () => {
    const { el } = render({ rows: [row(1)], net: { net: -120, text: '−$120', excluded: true } });
    expect(text(el.querySelector('.dp-sub'))).toBe('1 筆 · 僅計主幣種 −$120');
    expect(el.querySelector('.dp-sub .net-hint')).not.toBeNull();
    const plain = render({ rows: [row(1)], net: { net: -120, text: '−$120', excluded: false } });
    expect(plain.el.querySelector('.net-hint')).toBeNull();
  });

  it('says 這天沒有符合條件的記錄 for an empty day while a search or filter is active', () => {
    expect(text(render({ filtered: true }).el.querySelector('.dp-empty'))).toBe('這天沒有符合條件的記錄');
    expect(text(render({ filtered: false }).el.querySelector('.dp-empty'))).toBe('這天沒有記錄');
    expect(text(render({ filtered: true, day: '2026-10-30' }).el.querySelector('.dp-empty'))).toBe('還沒到這天');
  });

  it('results mode shows a skeleton while loading and 記錄讀取失敗 with 重試 on error, never 沒有符合的記錄 then', () => {
    const loading = render({ mode: 'results', query: '午餐', state: 'loading' });
    expect(loading.el.querySelector('app-skeleton')).not.toBeNull();
    expect(text(loading.el)).not.toContain('沒有符合的記錄');

    const error = render({ mode: 'results', query: '午餐', state: 'error' });
    expect(text(error.el)).toContain('記錄讀取失敗');
    expect(text(error.el)).not.toContain('沒有符合的記錄');
    error.el.querySelector<HTMLButtonElement>('.dp-retry')!.click();
    expect(error.spies.retry).toHaveBeenCalledTimes(1);
    // The header (and its ✕) stays in every state.
    expect(text(error.el.querySelector('.dp-label'))).toBe('「午餐」');
  });

  it('results mode offers 載入更多 while the month has more matches', () => {
    const days: TimelineDay[] = [{ date: '2026-10-21', label: '10/21 週三', net: -120, excluded: false, rows: [row(1)] }];
    const { el, spies } = render({ mode: 'results', query: '午餐', days, hasMore: true });
    const more = el.querySelector<HTMLButtonElement>('.dp-more')!;
    expect(text(more)).toBe('載入更多');
    more.click();
    expect(spies.loadMore).toHaveBeenCalledTimes(1);
    expect(render({ mode: 'results', query: '午餐', days }).el.querySelector('.dp-more')).toBeNull();
    expect(render({ mode: 'results', query: '午餐', days, hasMore: true, state: 'loading' }).el.querySelector('.dp-more')).toBeNull();
  });

  it('results mode lists day groups with labels and nets and 沒有符合的記錄 when empty', () => {
    const days: TimelineDay[] = [
      { date: '2026-10-21', label: '10/21 週三', net: -3096, excluded: false, rows: [row(1), row(2)] },
      { date: '2026-10-19', label: '10/19 週一', net: 250, excluded: false, rows: [row(3)] },
    ];
    const { el, spies } = render({ mode: 'results', query: '午餐', days });
    expect(text(el.querySelector('.dp-label'))).toBe('「午餐」');
    expect(Array.from(el.querySelectorAll('.dp-day')).map(text)).toEqual(['10/21 週三 −$3,096', '10/19 週一 +$250']);
    expect(el.querySelectorAll('.dp-row').length).toBe(3);
    el.querySelector<HTMLButtonElement>('[aria-label="清除搜尋"]')!.click();
    expect(spies.clear).toHaveBeenCalled();
    const empty = render({ mode: 'results', query: 'x' });
    expect(text(empty.el)).toContain('沒有符合的記錄');
  });
});
