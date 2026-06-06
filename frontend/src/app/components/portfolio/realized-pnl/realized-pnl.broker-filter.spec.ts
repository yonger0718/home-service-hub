import { CommonModule } from '@angular/common';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { RealizedPnlEvent } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { SegToggleComponent } from '../../ui/seg-toggle/seg-toggle';
import { PortfolioRealizedPnlComponent } from './realized-pnl';

describe('PortfolioRealizedPnlComponent broker filter', () => {
  let portfolioService: {
    getRealizedPnl: ReturnType<typeof vi.fn>;
    getSymbolNames: ReturnType<typeof vi.fn>;
    getTransactionBrokers: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    portfolioService = {
      getRealizedPnl: vi.fn().mockReturnValue(of(paged([]))),
      getSymbolNames: vi.fn().mockReturnValue(of({})),
      getTransactionBrokers: vi.fn().mockReturnValue(of([])),
    };

    await TestBed.configureTestingModule({
      imports: [PortfolioRealizedPnlComponent],
      providers: [{ provide: PortfolioService, useValue: portfolioService }],
    })
      .overrideComponent(PortfolioRealizedPnlComponent, {
        set: {
          imports: [CommonModule, SegToggleComponent],
          template: `
            @if (showBrokerFilter()) {
              <app-seg-toggle
                class="broker-filter"
                ariaLabel="券商篩選"
                [options]="brokerFilterOptions()"
                [value]="selectedBroker()"
                (change)="selectBrokerFilter($event)"
              ></app-seg-toggle>
            }
            @for (event of filteredEvents(); track eventKey(event)) {
              <article class="event-row">
                <span class="symbol">{{ event.symbol }}</span>
                @if (showBrokerColumn() && showBrokerBadge(event)) {
                  <span class="broker-badge">{{ brokerLabel(event.broker) }}</span>
                }
              </article>
            }
          `,
        },
      })
      .compileComponents();
  });

  it('renders broker filter chips for mixed broker datasets', () => {
    portfolioService.getTransactionBrokers.mockReturnValue(of(['IB', 'FIRSTRADE', 'TW_CATHAY']));
    portfolioService.getRealizedPnl.mockReturnValue(of(paged([
      event({ symbol: 'IB1', broker: 'IB' }),
      event({ symbol: 'FT1', broker: 'FIRSTRADE' }),
      event({ symbol: 'TW1', broker: 'TW_CATHAY' }),
    ])));

    const fixture = TestBed.createComponent(PortfolioRealizedPnlComponent);
    fixture.detectChanges();

    const buttons = Array.from(fixture.nativeElement.querySelectorAll('.broker-filter button')) as HTMLButtonElement[];
    expect(buttons.map(button => button.textContent?.trim())).toEqual(['全部', 'IB', 'Firstrade', '國泰']);
    expect(fixture.nativeElement.textContent).toContain('IB1');
    expect(fixture.nativeElement.textContent).toContain('Firstrade');
  });

  it('refetches with broker query when selecting a chip', () => {
    portfolioService.getTransactionBrokers.mockReturnValue(of(['IB', 'FIRSTRADE']));
    portfolioService.getRealizedPnl.mockReturnValue(of(paged([
      event({ symbol: 'IB1', broker: 'IB' }),
      event({ symbol: 'FT1', broker: 'FIRSTRADE' }),
    ])));
    const fixture = TestBed.createComponent(PortfolioRealizedPnlComponent);
    fixture.detectChanges();

    const ibButton = Array.from(fixture.nativeElement.querySelectorAll('.broker-filter button') as NodeListOf<HTMLButtonElement>)
      .find(button => button.textContent?.trim() === 'IB')!;
    expect(ibButton).toBeDefined();
    portfolioService.getRealizedPnl.mockClear();
    ibButton.click();
    fixture.detectChanges();

    expect(fixture.componentInstance.selectedBroker()).toBe('IB');
    const lastCall = portfolioService.getRealizedPnl.mock.calls.at(-1)?.[0];
    expect(lastCall?.broker).toBe('IB');
    expect(lastCall?.offset).toBe(0);
  });

  it('shows broker filter chip for TW manual rows labeled as 國泰', () => {
    portfolioService.getTransactionBrokers.mockReturnValue(of(['TW_MANUAL']));
    portfolioService.getRealizedPnl.mockReturnValue(of(paged([
      event({ symbol: 'TW1', broker: 'TW_MANUAL' }),
      event({ symbol: 'TW2', broker: null }),
    ])));

    const fixture = TestBed.createComponent(PortfolioRealizedPnlComponent);
    fixture.detectChanges();

    const buttons = Array.from(fixture.nativeElement.querySelectorAll('.broker-filter button')) as HTMLButtonElement[];
    expect(buttons.map(button => button.textContent?.trim())).toEqual(['全部', '國泰']);
    expect(fixture.nativeElement.querySelector('.broker-badge')?.textContent?.trim()).toBe('國泰');
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
    symbol: 'AAPL',
    market: 'US',
    name: null,
    quantity: 1,
    sell_price: '190',
    avg_cost_at_sale: '150',
    fee: '1',
    tax: '0',
    proceeds_gross: '190',
    proceeds_net: '189',
    cost_out: '150',
    realized_pnl: '39',
    native_proceeds: '189',
    native_cost: '150',
    native_currency: 'USD',
    is_day_trade: false,
    position_side: 'LONG',
    note: null,
    broker: 'IB',
    ...overrides,
  };
}
