import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { MonthSummary } from '../../../models/accounting.model';
import { monthLabel } from '../dates';
import { categoryDistribution, distributionAriaText } from '../timeline/category-distribution';
import { DueItem } from './due-items';
import type { OverviewState } from './overview.service';
import { MonthOverviewComponent } from './month-overview';
import type { InboxEinvoice } from '../inbox/einvoice.model';
import type { BreakdownRow } from '../../../models/reports.model';
import type { MonthDue, TodayCard } from '../calendar-month/calendar-pane.service';
import type { CalendarPartState } from '../calendar-month/calendar-data.service';

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

  describe('calendar mode', () => {
    const TODAY: TodayCard = { date: '2026-10-21', count: 3, names: '早餐、午餐', netText: '−$3,096', net: -3096 };
    const DUE_IN: MonthDue = { key: 'd1', accountId: 1, name: '玉山 UNI', date: '2026-10-28', amountText: '$12,345' };
    const DUE_OUT: MonthDue = { key: 'd2', accountId: 2, name: '台新', date: '2026-11-02', amountText: '$900' };
    function invoice(id: number, issue: string, seller: string, total: string): InboxEinvoice {
      return { id, number: `AB-${id}`, issue_date: issue, seller_name: seller, seller_tax_id: '', total_amount: total, status_raw: '', state: 'unmatched', items: [] } as unknown as InboxEinvoice;
    }
    function row(i: number): BreakdownRow {
      return { key: `m${i}`, label: `商家${i}`, color: null, amount: String(1000 * (6 - i)), refund: '0', count: i } as BreakdownRow;
    }
    function createCal(inputs: { today?: TodayCard | null; merchants?: BreakdownRow[]; merchantsState?: CalendarPartState; invoices?: InboxEinvoice[]; dues?: MonthDue[] }) {
      TestBed.configureTestingModule({ providers: [provideRouter([])] });
      const fixture = TestBed.createComponent(MonthOverviewComponent);
      const ref = fixture.componentRef;
      ref.setInput('month', '2026-10');
      ref.setInput('summary', SUMMARY);
      ref.setInput('calendar', true);
      ref.setInput('today', inputs.today === undefined ? TODAY : inputs.today);
      ref.setInput('merchants', inputs.merchants ?? []);
      ref.setInput('merchantsState', inputs.merchantsState ?? 'ready');
      ref.setInput('invoices', inputs.invoices ?? []);
      ref.setInput('dues', inputs.dues ?? []);
      fixture.detectChanges();
      return { fixture, el: fixture.nativeElement as HTMLElement };
    }

    it('renders 今天, distribution with 看報表, merchants and 這個月要處理 in that order', () => {
      const { el } = createCal({});
      expect(el.querySelector('.ov-today')).not.toBeNull();
      expect(el.querySelector('.ov-today')!.textContent).toContain('今天 · 10/21 週三');
      expect(el.querySelector('.ov-today')!.textContent).toContain('3 筆');
      expect(el.querySelector('.ov-today')!.textContent).toContain('−$3,096');
      expect(Array.from(el.querySelectorAll('h3.ov-title')).map(h => h.textContent!.trim())).toEqual(['支出類別', '花最多的商家', '這個月要處理']);
      expect(el.querySelector('a[href*="/accounting/reports?month=2026-10"]')!.textContent).toContain('看報表 ›');
      expect(el.textContent).not.toContain('近期到期');
      expect(el.querySelector('.ov-today')!.compareDocumentPosition(el.querySelector('h3.ov-title')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it("colours the 今天 card's net like every amount (.neg / .pos with the 支出收入顏色 tones)", () => {
      const { el } = createCal({});
      expect(el.querySelector('.today-net')!.classList.contains('neg')).toBe(true);
      const scss = readFileSync(join(process.cwd(), 'src/app/components/accounting/month-overview/month-overview.scss'), 'utf8');
      expect(scss).toMatch(/\.today-net\.neg \{\s*color: var\(--tone-neg, var\(--c-red\)\);/);
      expect(scss).toMatch(/\.today-net\.pos \{\s*color: var\(--tone-pos, var\(--c-green\)\);/);
    });

    it('shows no 今天 card without one (another month)', () => {
      const { el } = createCal({ today: null });
      expect(el.querySelector('.ov-today')).toBeNull();
    });

    it("emits pickDay with today's date from the 今天 card and with the due date from a bill row", () => {
      const { fixture, el } = createCal({ dues: [DUE_IN, DUE_OUT] });
      const picked: string[] = [];
      fixture.componentInstance.pickDay.subscribe(d => picked.push(d));
      (el.querySelector('.ov-today') as HTMLButtonElement).click();
      const bills = el.querySelectorAll<HTMLButtonElement>('.todo-bill');
      expect(bills[0].textContent).toContain('玉山 UNI 帳單 $12,345 · 10/28 到期');
      expect(bills[1].textContent).toContain('台新 帳單 $900 · 11/02 到期');
      bills[1].click();
      expect(picked).toEqual(['2026-10-21', '2026-11-02']);
      bills[0].click();
      expect(picked[2]).toBe('2026-10-28');
    });

    it('emits openInvoice for an invoice row and lists invoices before dues', () => {
      const inv = invoice(1, '2026-10-10', '全家便利商店', '85');
      const { fixture, el } = createCal({ invoices: [inv], dues: [DUE_IN] });
      const opened: InboxEinvoice[] = [];
      fixture.componentInstance.openInvoice.subscribe(i => opened.push(i));
      const items = Array.from(el.querySelectorAll('.ov-todo button'));
      expect(items[0].classList.contains('todo-invoice')).toBe(true);
      expect(items[1].classList.contains('todo-bill')).toBe(true);
      expect(items[0].textContent).toContain('發票待對帳 · 10/10 全家便利商店 $85');
      (items[0] as HTMLButtonElement).click();
      expect(opened).toEqual([inv]);
    });

    it('shows 沒有要處理的項目 when nothing is pending', () => {
      const { el } = createCal({});
      expect(el.textContent).toContain('沒有要處理的項目');
    });

    it('replaces the merchant card with 商家排行讀取失敗 on error and lists five rows with count and amount when ready', () => {
      const err = createCal({ merchantsState: 'error' });
      expect(err.el.querySelector('.ov-merchants-error')!.textContent).toContain('商家排行讀取失敗');
      expect(err.el.querySelector('.ov-merchants')).toBeNull();
      TestBed.resetTestingModule();
      const ok = createCal({ merchants: [1, 2, 3, 4, 5, 6].map(row) });
      const items = ok.el.querySelectorAll('.ov-merchants li');
      expect(items.length).toBe(5);
      expect(items[0].textContent).toContain('1');
      expect(items[0].textContent).toContain('商家1');
      expect(items[0].textContent).toContain('1 筆');
      expect(items[0].textContent).toContain('$5,000');
    });

    it('shows 本月尚無商家支出 for an empty ready ranking', () => {
      const { el } = createCal({ merchants: [] });
      expect(el.textContent).toContain('本月尚無商家支出');
    });

    it('keeps the 清單 overview unchanged when calendar is false', () => {
      const el = create({ summary: SUMMARY });
      expect(el.querySelector('.ov-today, .ov-merchants, .ov-todo')).toBeNull();
      expect(Array.from(el.querySelectorAll('h3.ov-title')).map(h => h.textContent!.trim())).toEqual(['支出類別', '近期到期']);
    });
  });
});
