import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import type { TodayCard } from './calendar-pane.service';
import { CalendarTodayBarComponent } from './calendar-today-bar';

const CARD: TodayCard = { date: '2026-11-09', count: 3, names: '全聯、電費、Netflix', netText: '−$250', net: -250 };

describe('CalendarTodayBarComponent', () => {
  function render(card: TodayCard | null, current: boolean) {
    const fixture = TestBed.createComponent(CalendarTodayBarComponent);
    fixture.componentRef.setInput('card', card);
    fixture.componentRef.setInput('current', current);
    fixture.componentRef.setInput('today', '2026-11-09');
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('reads 今天 · 3 筆, the names and the net in the current month', () => {
    const el = render(CARD, true);
    const text = el.textContent ?? '';
    expect(text).toContain('今天 · 3 筆');
    expect(text).toContain('全聯、電費、Netflix');
    expect(text).toContain('−$250');
    expect(el.querySelector('.tb-net')?.classList.contains('neg')).toBe(true);
    expect(el.querySelector('.tb-disc')?.textContent?.trim()).toBe('9');
    expect(el.querySelector('button')?.getAttribute('aria-label')).toBe('今天 · 3 筆，−$250');
  });

  it('reads 回到今天 in another month', () => {
    const el = render(null, false);
    expect(el.textContent).toContain('回到今天');
    expect(el.textContent).not.toContain('筆');
    expect(el.querySelector('button')?.getAttribute('aria-label')).toBe('回到今天');
    expect(el.querySelector('.tb-disc')?.textContent?.trim()).toBe('9');
  });

  it('emits pick on click', () => {
    const fixture = TestBed.createComponent(CalendarTodayBarComponent);
    fixture.componentRef.setInput('current', true);
    fixture.componentRef.setInput('today', '2026-11-09');
    const spy = vi.fn();
    fixture.componentInstance.pick.subscribe(spy);
    fixture.detectChanges();
    (fixture.nativeElement as HTMLElement).querySelector('button')!.click();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('shows 0 筆 while the card is null in the current month', () => {
    const el = render(null, true);
    expect(el.textContent).toContain('今天 · 0 筆');
    expect(el.querySelector('.tb-net')?.classList.contains('neg')).toBe(false);
  });
});
