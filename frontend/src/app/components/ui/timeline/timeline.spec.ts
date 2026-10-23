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


/** Tag, sorted classes, attributes (minus Angular's `_ng*`) and leaf text of every element, depth-first. */
function shape(node: Element): string {
  const cls = Array.from(node.classList).sort().join('.');
  const attrs = Array.from(node.attributes)
    .filter(a => !a.name.startsWith('_ng') && a.name !== 'class')
    .map(a => `${a.name}=${a.value}`)
    .sort()
    .join(',');
  const kids = Array.from(node.children).map(shape).join('');
  const text = node.children.length === 0 ? (node.textContent ?? '').trim() : '';
  return `<${node.tagName.toLowerCase()}${cls ? '.' + cls : ''}${attrs ? '[' + attrs + ']' : ''}${text ? ' "' + text + '"' : ''}>${kids}</>`;
}

const DAY_ROWS: TimelineRow[] = [
  { id: 1, date: '2026-05-01', side: 'buy', sideLabel: '買進', primary: '台積電 2330', meta: '10 × 650.00', metaBadge: '國泰', amount: '-6,500 TWD', amountVariant: 'buy' },
  { id: 2, date: '2026-05-01', side: 'sell', primary: '鴻海', amount: '+900', amountVariant: 'sell' },
  { date: '2026-05-02', side: 'cash', primary: '股利', meta: '每股 1.00' },
];

/** Captured from the day-mode timeline before month grouping and the new row fields existed (8b1b31d). */
const DAY_SNAPSHOT = '<div.timeline><div.tl-item><div.tl-date><span.d "01"></><span.m "MAY"></></><article.tl-card[data-row-id=1]><div.tl-lhs><app-side-tag><span.buy.side-tag "買進"></></><div><div.tl-name "台積電 2330"></><div.tl-meta><span.tl-meta-badge "國泰"></><span "10 × 650.00"></></></></><div.buy.tl-amt "-6,500 TWD"></><button.tl-action[aria-haspopup=menu,aria-label=操作 台積電 2330,type=button] "⋯"></></></><div.tl-item><div.tl-date[aria-hidden=true]></><article.tl-card[data-row-id=2]><div.tl-lhs><app-side-tag><span.sell.side-tag "賣"></></><div><div.tl-name "鴻海"></></></><div.sell.tl-amt "+900"></><button.tl-action[aria-haspopup=menu,aria-label=操作 鴻海,type=button] "⋯"></></></><div.tl-item><div.tl-date><span.d "02"></><span.m "MAY"></></><article.tl-card><div.tl-lhs><app-side-tag><span.cash.side-tag "息"></></><div><div.tl-name "股利"></><div.tl-meta><span "每股 1.00"></></></></></></></>';

describe('Timeline day mode', () => {
  it('keeps the pre-month-grouping element structure for rows without the new fields', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('rows', DAY_ROWS);
    fixture.componentRef.setInput('rowActions', true);
    fixture.detectChanges();
    expect(shape(fixture.nativeElement.querySelector('.timeline'))).toBe(DAY_SNAPSHOT);

    fixture.componentRef.setInput('groupBy', 'day');
    fixture.detectChanges();
    expect(shape(fixture.nativeElement.querySelector('.timeline'))).toBe(DAY_SNAPSHOT);
    expect(fixture.nativeElement.querySelector('.tl-month')).toBeNull();
  });
});

describe('Timeline month mode', () => {
  const rich: TimelineRow = {
    id: 7,
    date: '2026-10-15',
    side: 'buy',
    sideLabel: '買',
    primary: '國泰永續高股息 00878',
    square: { text: '78', token: '--app-dividend', ink: 'dark' },
    tags: ['當沖'],
    meta: '5,000 股 @ 22.05',
    sub: '手續費 157',
    broker: '元大',
    amount: '−110,407',
    amountNote: '總付出',
  };
  const rows: TimelineRow[] = [
    rich,
    { id: 8, date: '2026-10-02', side: 'sell', primary: '鴻海 2317', amount: '+174,226' },
    { id: 9, date: new Date(2026, 8, 18), side: 'buy', primary: 'Apple AAPL', amount: '−73,220' },
  ];

  function render(inputs: Record<string, unknown> = {}) {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('rows', rows);
    fixture.componentRef.setInput('groupBy', 'month');
    for (const [key, value] of Object.entries(inputs)) fixture.componentRef.setInput(key, value);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders one heading per month with its label and the groupHeading text', () => {
    const keys: string[] = [];
    const el = render({ groupHeading: (key: string) => { keys.push(key); return `sum ${key}`; } });
    const headings = Array.from(el.querySelectorAll('.tl-month'));
    expect(headings.map(h => h.querySelector('.tl-month-label')?.textContent?.trim())).toEqual(['2026 年 10 月', '2026 年 9 月']);
    expect(headings.map(h => h.querySelector('.tl-month-sum')?.textContent?.trim())).toEqual(['sum 2026-10', 'sum 2026-09']);
    expect(new Set(keys)).toEqual(new Set(['2026-10', '2026-09']));
    const groups = Array.from(el.querySelectorAll('.tl-month-group'));
    expect(groups.map(g => g.querySelectorAll('.tl-card').length)).toEqual([2, 1]);
  });

  it('omits the right-hand text without a groupHeading', () => {
    const el = render();
    expect(el.querySelectorAll('.tl-month').length).toBe(2);
    expect(el.querySelector('.tl-month-sum')).toBeNull();
  });

  it('shows the day and weekday on every row', () => {
    const el = render();
    const days = Array.from(el.querySelectorAll('.tl-item')).map(item => [
      item.querySelector('.tl-date .d')?.textContent?.trim(),
      item.querySelector('.tl-date .m')?.textContent?.trim(),
    ]);
    expect(days).toEqual([['10/15', '週四'], ['10/02', '週五'], ['09/18', '週五']]);
  });

  it('renders the square, name line with SideTag and tags, quantity, fees, broker and amount note', () => {
    const el = render();
    const card = el.querySelector('[data-row-id="7"]') as HTMLElement;
    const square = card.querySelector('.tl-square') as HTMLElement;
    expect(square.textContent?.trim()).toBe('78');
    expect(square.style.background).toBe('var(--app-dividend)');
    expect(square.classList).toContain('ink-dark');
    expect(square.classList).not.toContain('ink-light');
    expect(card.classList).toContain('tl-card-rich');
    const nameLine = card.querySelector('.tl-name') as HTMLElement;
    expect(nameLine.querySelector('.tl-primary')?.textContent?.trim()).toBe('國泰永續高股息 00878');
    expect(nameLine.querySelector('app-side-tag')?.textContent?.trim()).toBe('買');
    expect(Array.from(nameLine.querySelectorAll('app-tag')).map(t => t.textContent?.trim())).toEqual(['當沖']);
    expect(card.querySelector('.tl-qty-main')?.textContent?.trim()).toBe('5,000 股 @ 22.05');
    expect(card.querySelector('.tl-sub')?.textContent?.trim()).toBe('手續費 157');
    expect(card.querySelector('.tl-broker')?.textContent?.trim()).toBe('元大');
    expect(card.querySelector('.tl-amt-col .tl-amt')?.textContent?.trim()).toBe('−110,407');
    expect(card.querySelector('.tl-amt-col .tl-amt-note')?.textContent?.trim()).toBe('總付出');
  });

  it('uses light ink when the square says so and no tags when there are none', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('rows', [{ ...rich, tags: undefined, square: { text: '積', token: '--app-primary', ink: 'light' } }]);
    fixture.componentRef.setInput('groupBy', 'month');
    fixture.detectChanges();
    const square = fixture.nativeElement.querySelector('.tl-square') as HTMLElement;
    expect(square.classList).toContain('ink-light');
    expect(square.style.background).toBe('var(--app-primary)');
    expect(fixture.nativeElement.querySelector('app-tag')).toBeNull();
  });

  it('collapses rows in compact mode: no date column, quantity · broker, the date beneath the amount', () => {
    const el = render({ compact: true });
    expect(el.querySelector('.timeline')?.classList).toContain('tl-compact');
    expect(el.querySelector('.tl-date')).toBeNull();
    const card = el.querySelector('[data-row-id="7"]') as HTMLElement;
    expect(card.querySelector('.tl-square')?.textContent?.trim()).toBe('78');
    expect(card.querySelector('.tl-name app-side-tag')?.textContent?.trim()).toBe('買');
    expect(card.querySelector('.tl-meta')?.textContent?.trim()).toBe('5,000 股 @ 22.05 · 元大');
    expect(card.querySelector('.tl-sub')).toBeNull();
    expect(card.querySelector('.tl-broker')).toBeNull();
    expect(card.querySelector('.tl-amt-col .tl-amt')?.textContent?.trim()).toBe('−110,407');
    expect(card.querySelector('.tl-amt-col .tl-amt-date')?.textContent?.trim()).toBe('10/15');
    expect(card.textContent).not.toContain('總付出');
  });

  it('keeps an unparseable date in its own group without throwing', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('rows', [{ id: 1, date: '', side: 'buy', primary: 'x' }]);
    fixture.componentRef.setInput('groupBy', 'month');
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.tl-month-label')?.textContent?.trim()).toBe('—');
    expect(fixture.nativeElement.querySelectorAll('.tl-card').length).toBe(1);
  });
});
