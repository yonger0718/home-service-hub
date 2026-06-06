import { CommonModule } from '@angular/common';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { NativeAmountPipe } from '../../../pipes/native-amount.pipe';
import { RealizedPnlEvent } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { PortfolioRealizedPnlComponent } from './realized-pnl';

describe('PortfolioRealizedPnlComponent foreign columns', () => {
  let portfolioService: {
    getRealizedPnl: ReturnType<typeof vi.fn>;
    getSymbolNames: ReturnType<typeof vi.fn>;
    getTransactionBrokers: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    portfolioService = {
      getRealizedPnl: vi.fn().mockReturnValue(of(paged([event()]))),
      getSymbolNames: vi.fn().mockReturnValue(of({})),
      getTransactionBrokers: vi.fn().mockReturnValue(of([])),
    };

    await TestBed.configureTestingModule({
      imports: [PortfolioRealizedPnlComponent],
      providers: [{ provide: PortfolioService, useValue: portfolioService }],
    })
      .overrideComponent(PortfolioRealizedPnlComponent, {
        set: {
          imports: [CommonModule, NativeAmountPipe],
          template: `
            @for (event of events(); track eventKey(event)) {
              <article class="event-row">
                @if (showForeignColumns()) {
                  <span class="market-column">{{ event.market }}</span>
                  <span class="native-column">
                    {{ event.native_proceeds | nativeAmount: event.native_currency }}
                    /
                    {{ event.native_cost | nativeAmount: event.native_currency }}
                  </span>
                }
              </article>
            }
          `,
        },
      })
      .compileComponents();
  });

  it('shows market and native amount columns for mixed datasets', () => {
    portfolioService.getRealizedPnl.mockReturnValue(of(paged([
      event(),
      event({
        symbol: 'AAPL',
        market: 'US',
        native_proceeds: '190.5',
        native_cost: '150',
        native_currency: 'USD',
      }),
    ])));
    const fixture = TestBed.createComponent(PortfolioRealizedPnlComponent);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.market-column')?.textContent).toContain('TW');
    expect(fixture.nativeElement.textContent).toContain('190.50 USD');
  });

  it('hides market and native amount columns for TW-only datasets', () => {
    const fixture = TestBed.createComponent(PortfolioRealizedPnlComponent);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.market-column')).toBeNull();
    expect(fixture.nativeElement.querySelector('.native-column')).toBeNull();
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
