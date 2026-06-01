import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { TimelineComponent, TimelineRow } from './timeline';

@Component({
  imports: [TimelineComponent],
  template: `<app-timeline [rows]="rows"></app-timeline>`,
})
class TimelineHostComponent {
  rows: TimelineRow[] = [
    { date: '2026-05-01', side: 'buy', primary: '台積電', meta: '10 x 650', amount: 'NT$6,500', amountVariant: 'buy' },
    { date: '2026-05-01', side: 'sell', primary: '鴻海', meta: '5 x 180', amount: 'NT$900', amountVariant: 'sell' },
    { date: '2026-05-02', side: 'cash', primary: '股利', amount: 'NT$120', amountVariant: 'dividend' },
  ];
}

describe('TimelineComponent', () => {
  it('groups rows by date and renders row content', () => {
    const fixture = TestBed.createComponent(TimelineHostComponent);
    fixture.detectChanges();

    const dates = fixture.nativeElement.querySelectorAll('.tl-date .d') as NodeListOf<HTMLElement>;
    const rows = fixture.nativeElement.querySelectorAll('.tl-card') as NodeListOf<HTMLElement>;
    expect(dates.length).toBe(2);
    expect(rows.length).toBe(3);
    expect(rows[0].textContent).toContain('台積電');
    expect(rows[0].querySelector('.tl-amt')?.classList.contains('buy')).toBe(true);
  });
});
