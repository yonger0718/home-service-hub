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


describe('Timeline row actions', () => {
  it('keeps stable identities after reordering duplicate-looking rows and hides actions without IDs', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    const row: TimelineRow = { date: '2026-05-01', side: 'buy', primary: 'duplicate', amount: '100' };
    fixture.componentRef.setInput('rows', [{ ...row, id: 1 }, { ...row, id: 2 }, row]);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('button').length).toBe(0);
    fixture.componentRef.setInput('rowActions', true);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('button').length).toBe(2);
    const ids: (string | number)[] = [];
    fixture.componentInstance.rowAction.subscribe(action => ids.push(action.id));
    fixture.nativeElement.querySelector('[data-row-id="2"] button').click();
    fixture.componentRef.setInput('rows', [{ ...row, id: 2 }, { ...row, id: 1 }]);
    fixture.detectChanges();
    fixture.nativeElement.querySelector('button').click();
    expect(ids).toEqual([2, 2]);
  });
});
