import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { FuturesSummary } from '../../../../models/portfolio.model';
import { PortfolioService } from '../../../../services/portfolio.service';
import { FuturesCardComponent } from './futures-card';

function summary(over: Partial<FuturesSummary> = {}): FuturesSummary {
  return {
    as_of: '2026-11-23',
    value: { total_equity: '500000', balance: '500000', source_date: '2026-11-23', source_kind: 'daily', positions_open: false, frozen: false },
    latest_daily: {
      statement_date: '2026-11-23',
      withdrawable: '300000',
      initial_margin: '100000',
      maintenance_margin: '80000',
      excess_margin: '120000',
      margin_call: '0',
    },
    month: { month: '2026-11', pnl: '12000', pnl_complete: true, pnl_reason: null, futures_realized: '8000' },
    ...over,
  };
}

function render(result: FuturesSummary | Error) {
  const getFuturesSummary = vi.fn(() => (result instanceof Error ? throwError(() => result) : of(result)));
  TestBed.configureTestingModule({ providers: [{ provide: PortfolioService, useValue: { getFuturesSummary } }] });
  const fixture = TestBed.createComponent(FuturesCardComponent);
  fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  return { fixture, el, text: () => el.textContent!.replace(/\s+/g, ' ') };
}

describe('FuturesCardComponent', () => {
  it('renders summary fields', () => {
    const { text } = render(summary());
    const t = text();
    expect(t).toContain('永豐期貨');
    expect(t).toContain('權益總值');
    expect(t).toContain('$500,000');
    expect(t).toContain('可動用(出金)保證金');
    expect(t).toContain('$300,000');
    expect(t).toContain('原始 / 維持保證金');
    expect(t).toContain('$100,000 / $80,000');
    expect(t).toContain('超額 / 追繳保證金');
    expect(t).toContain('$120,000 / $0');
    expect(t).toContain('本月損益');
    expect(t).toContain('+$12,000');
    expect(t).toContain('本月期貨平倉損益');
    expect(t).toContain('+$8,000');
    expect(t).toContain('11/23');
    expect(t).not.toContain('持倉中');
    expect(t).not.toContain('風險指標');
  });

  it('frozen note shows source date', () => {
    const base = summary();
    const { text } = render(summary({ value: { ...base.value!, frozen: true, source_date: '2026-10-31', source_kind: 'monthly' } }));
    expect(text()).toContain('持倉中，數字停在 10/31');
  });

  it('empty state', () => {
    const { text } = render(summary({ value: null, latest_daily: null, month: null }));
    expect(text()).toContain('尚無期貨對帳單');
    expect(text()).not.toContain('權益總值');
  });

  it('negative equity renders', () => {
    const base = summary();
    const { el, text } = render(summary({ value: { ...base.value!, total_equity: '-1234' } }));
    expect(text()).toContain('−$1,234');
    expect(el.querySelector('[data-testid="futures-equity"]')!.classList.contains('down')).toBe(true);
  });

  it('no baseline shows dash', () => {
    const base = summary();
    const { text } = render(summary({ month: { ...base.month!, pnl: null, pnl_complete: false, pnl_reason: 'no_baseline' } }));
    expect(text()).toContain('—（沒有上月底基準）');
  });

  it('findings marks incomplete', () => {
    const base = summary();
    const { text } = render(summary({ month: { ...base.month!, pnl_complete: false, pnl_reason: 'findings' } }));
    expect(text()).toContain('+$12,000');
    expect(text()).toContain('可能不完整');
  });

  it('margin rows show dash without a daily statement', () => {
    const { text } = render(summary({ latest_daily: null }));
    expect(text()).toContain('可動用(出金)保證金—');
    expect(text()).toContain('原始 / 維持保證金—');
  });

  it('margin call above zero uses the danger style', () => {
    const base = summary();
    const { el } = render(summary({ latest_daily: { ...base.latest_daily!, margin_call: '5000' } }));
    expect(el.querySelector('[data-testid="futures-margin-call"]')!.classList.contains('margin-call')).toBe(true);
  });

  it('shows an error state when the summary fails to load', () => {
    const { text } = render(new Error('boom'));
    expect(text()).toContain('期貨資料載入失敗');
  });
});
