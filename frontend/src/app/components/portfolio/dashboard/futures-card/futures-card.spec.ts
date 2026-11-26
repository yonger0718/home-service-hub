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
  it('dates the margins when the latest daily is older than the monthly source', () => {
    const base = summary();
    const { text } = render(summary({ value: { ...base.value!, source_kind: 'monthly', source_date: '2026-11-30' }, latest_daily: { ...base.latest_daily!, statement_date: '2026-11-23' } }));
    expect(text()).toContain('保證金資料日 11/23');
  });

  it('shows no margin date when the daily is the source date', () => {
    expect(render(summary()).text()).not.toContain('保證金資料日');
  });

  it('does not colour a positive equity red, keeps the negative style', () => {
    const pos = render(summary());
    expect(pos.el.querySelector('[data-testid="futures-equity"]')!.classList.contains('up')).toBe(false);
    TestBed.resetTestingModule();
    const base = summary();
    const neg = render(summary({ value: { ...base.value!, total_equity: '-500' } }));
    expect(neg.el.querySelector('[data-testid="futures-equity"]')!.classList.contains('down')).toBe(true);
  });

  it('reloads when the refresh token changes, not on the first input', () => {
    const { fixture, el } = render(summary());
    const service = TestBed.inject(PortfolioService) as unknown as { getFuturesSummary: ReturnType<typeof vi.fn> };
    expect(service.getFuturesSummary).toHaveBeenCalledTimes(1);
    fixture.componentRef.setInput('refresh', 0);
    fixture.detectChanges();
    expect(service.getFuturesSummary).toHaveBeenCalledTimes(1);
    fixture.componentRef.setInput('refresh', 1);
    fixture.detectChanges();
    expect(service.getFuturesSummary).toHaveBeenCalledTimes(2);
    expect(el.textContent).toContain('永豐期貨');
  });

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

  it('empty state is one compact line: title and text on one row, no full card title block', () => {
    const { el, text } = render(summary({ value: null, latest_daily: null, month: null }));
    const line = el.querySelector('[data-testid="futures-compact"]');
    expect(line).not.toBeNull();
    expect(line!.textContent!.replace(/\s+/g, ' ').trim()).toBe('永豐期貨 · 尚無期貨對帳單');
    expect(el.querySelectorAll('h2').length).toBe(1);
    expect(el.querySelector('.card-title')).toBeNull();
    expect(el.querySelector('.futures-cols')).toBeNull();
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

  function marginRow(el: HTMLElement): HTMLElement {
    return el.querySelector('[data-testid="futures-margin-row"]') as HTMLElement;
  }

  function daily(over: Partial<NonNullable<FuturesSummary['latest_daily']>>): FuturesSummary {
    const base = summary();
    return summary({ latest_daily: { ...base.latest_daily!, ...over } });
  }

  it('a margin call above zero warns: amber row class, ⚠ icon (aria-hidden) and the word 追繳', () => {
    const { el } = render(daily({ excess_margin: '-110000', margin_call: '110000' }));
    const row = marginRow(el);
    expect(row.classList.contains('futures-row--warn')).toBe(true);
    const tag = row.querySelector('[data-testid="futures-margin-warn"]')!;
    expect(tag.textContent!.trim()).toBe('追繳');
    const icon = tag.querySelector('i.pi.pi-exclamation-triangle')!;
    expect(icon.getAttribute('aria-hidden')).toBe('true');
    expect(row.textContent!.replace(/\s+/g, ' ')).toContain('−$110,000 / $110,000');
  });

  it('a negative excess margin with no call yet warns with 注意追繳', () => {
    const { el } = render(daily({ excess_margin: '-5000', margin_call: '0' }));
    const row = marginRow(el);
    expect(row.classList.contains('futures-row--warn')).toBe(true);
    expect(row.querySelector('[data-testid="futures-margin-warn"]')!.textContent!.trim()).toBe('注意追繳');
  });

  it('a margin call alone (excess ≥ 0) still warns with 追繳', () => {
    const { el } = render(daily({ excess_margin: '0', margin_call: '1' }));
    expect(marginRow(el).classList.contains('futures-row--warn')).toBe(true);
    expect(marginRow(el).querySelector('[data-testid="futures-margin-warn"]')!.textContent!.trim()).toBe('追繳');
  });

  it('boundary: 超額 $0 and 追繳 $0 is the plain state (no warning, no icon, no accent class)', () => {
    const { el } = render(daily({ excess_margin: '0', margin_call: '0' }));
    const row = marginRow(el);
    expect(row.classList.contains('futures-row--warn')).toBe(false);
    expect(row.querySelector('[data-testid="futures-margin-warn"]')).toBeNull();
    expect(el.querySelector('.pi-exclamation-triangle')).toBeNull();
    expect(el.querySelector('.margin-call')).toBeNull();
    expect(row.textContent!.replace(/\s+/g, ' ')).toContain('$0 / $0');
  });

  it('the normal state carries no accent / danger class on the margin values', () => {
    const { el } = render(summary());
    expect(marginRow(el).classList.contains('futures-row--warn')).toBe(false);
    expect(el.querySelector('.margin-call')).toBeNull();
    expect(el.querySelector('[data-testid="futures-margin-warn"]')).toBeNull();
  });

  it('two groups in order: equity, month P&L and 資料日 first; margins and 保證金資料日 second', () => {
    const base = summary();
    const { el } = render(
      summary({
        value: { ...base.value!, frozen: true, source_kind: 'monthly', source_date: '2026-11-30' },
        latest_daily: { ...base.latest_daily!, statement_date: '2026-11-23' },
      }),
    );
    const main = el.querySelector('[data-testid="futures-col-main"]')!;
    const margin = el.querySelector('[data-testid="futures-col-margin"]')!;
    expect(main.compareDocumentPosition(margin) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const mainText = main.textContent!.replace(/\s+/g, ' ');
    expect(mainText).toContain('權益總值');
    expect(mainText).toContain('本月損益');
    expect(mainText).toContain('本月期貨平倉損益');
    expect(mainText).toContain('資料日 11/30');
    expect(mainText).toContain('持倉中，數字停在 11/30');
    expect(mainText).not.toContain('保證金');
    const marginText = margin.textContent!.replace(/\s+/g, ' ');
    expect(marginText).toContain('可動用(出金)保證金');
    expect(marginText).toContain('原始 / 維持保證金');
    expect(marginText).toContain('超額 / 追繳保證金');
    // 保證金資料日 sits in the margin column, after the margin rows.
    const date = margin.querySelector('[data-testid="futures-margin-date"]')!;
    expect(date.textContent!.replace(/\s+/g, ' ').trim()).toBe('保證金資料日 11/23');
    expect(marginRow(el).compareDocumentPosition(date) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(margin.lastElementChild).toBe(date);
  });

  it('shows a compact error line when the summary fails to load', () => {
    const { el } = render(new Error('boom'));
    const line = el.querySelector('[data-testid="futures-compact"]')!;
    expect(line.textContent!.replace(/\s+/g, ' ').trim()).toBe('永豐期貨 · 期貨資料載入失敗');
    expect(line.querySelector('[role="alert"]')!.textContent).toContain('期貨資料載入失敗');
  });
});
