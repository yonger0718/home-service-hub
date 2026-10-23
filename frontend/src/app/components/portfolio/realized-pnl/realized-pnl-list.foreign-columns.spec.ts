import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RealizedPnlEvent } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { PortfolioRealizedPnlComponent } from './realized-pnl';

describe('PortfolioRealizedPnlComponent foreign sells', () => {
  let portfolioService: {
    getRealizedPnl: ReturnType<typeof vi.fn>;
    getSymbolNames: ReturnType<typeof vi.fn>;
    getTransactionBrokers: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    localStorage.clear();
    portfolioService = {
      getRealizedPnl: vi.fn().mockReturnValue(of(paged([event()]))),
      getSymbolNames: vi.fn().mockReturnValue(of({})),
      getTransactionBrokers: vi.fn().mockReturnValue(of([])),
    };
    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: PortfolioService, useValue: portfolioService }],
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  function render() {
    const fixture = TestBed.createComponent(PortfolioRealizedPnlComponent);
    fixture.detectChanges();
    return fixture;
  }

  function row(root: HTMLElement, symbol: string): HTMLButtonElement {
    return Array.from(root.querySelectorAll<HTMLButtonElement>('button.rz-row')).find(
      button => button.querySelector('.rz-symbol')?.textContent?.includes(symbol),
    )!;
  }

  it('shows a foreign sell in native figures next to a TW sell in TWD', () => {
    portfolioService.getRealizedPnl.mockReturnValue(of(paged([
      event(),
      event({
        symbol: 'AAPL',
        name: 'Apple',
        market: 'US',
        quantity: 2,
        native_sell_price: '95.25',
        native_proceeds_gross: '190.5',
        native_proceeds: '189.5',
        native_cost: '150',
        native_fee: '1',
        native_tax: '0',
        native_currency: 'USD',
      }),
    ])));
    const fixture = render();
    const root = fixture.nativeElement as HTMLElement;

    const aapl = row(root, 'AAPL');
    expect(aapl.querySelector('.rz-symbol')?.textContent).toContain('美股');
    expect(aapl.querySelector('.rz-meta')?.textContent).toContain('均價 75.00 USD → 賣出 95.25 USD');
    aapl.click();
    fixture.detectChanges();
    const mains = Array.from(root.querySelectorAll('.rz-detail .main')).map(main => main.textContent?.trim());
    expect(mains.slice(0, 5)).toEqual(['190.50 USD', '1.00 USD', '0.00 USD', '189.50 USD', '150.00 USD']);
    expect(root.querySelector('.rz-detail .sub')?.textContent?.trim()).toBe('80,000');

    const tw = row(root, '2330');
    expect(tw.querySelector('.rz-symbol')?.textContent).not.toContain('美股');
    expect(tw.querySelector('.rz-meta')?.textContent).toContain('均價 700.00 → 賣出 800.00');
  });

  it('shows no native figures for TW-only datasets', () => {
    const fixture = render();
    const root = fixture.nativeElement as HTMLElement;

    row(root, '2330').click();
    fixture.detectChanges();

    const list = root.querySelector('.rz-list')!;
    expect(list.textContent).not.toContain('USD');
    expect(list.textContent).not.toContain('美股');
    expect(root.querySelector('.rz-detail .sub')).toBeNull();
    expect(root.querySelector('.rz-detail .main')?.textContent?.trim()).toBe('80,000');
  });
});

function paged(items: RealizedPnlEvent[]) {
  return {
    items,
    total: items.length,
    summary: {
      filter_scope_total: '0',
      filter_scope_count: items.length,
      ytd_total: '0',
      ytd_count: items.length,
    },
  };
}

function event(overrides: Partial<RealizedPnlEvent> = {}): RealizedPnlEvent {
  return {
    trade_date: '2026-05-01',
    symbol: '2330',
    name: '台積電',
    quantity: 100,
    sell_price: '800',
    avg_cost_at_sale: '700',
    fee: '10',
    tax: '20',
    proceeds_gross: '80000',
    proceeds_net: '79970',
    cost_out: '70000',
    realized_pnl: '9970',
    is_day_trade: false,
    position_side: 'LONG' as const,
    note: null,
    market: 'TW' as const,
    broker: 'TW_MANUAL' as const,
    native_proceeds: '80000',
    native_cost: '70000',
    native_currency: 'TWD',
    ...overrides,
  };
}
