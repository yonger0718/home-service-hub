import { ChangeDetectionStrategy, Component, OnChanges, OnInit, SimpleChanges, inject, input, signal } from '@angular/core';

import { RouterLink } from '@angular/router';

import { FuturesSummary } from '../../../../models/portfolio.model';
import { PortfolioService } from '../../../../services/portfolio.service';
import { BentoComponent } from '../../../ui/bento/bento';
import { Amount, displayedSign, formatMoney } from '../../../ui/format';

type LoadState = 'loading' | 'ready' | 'error';

/**
 * The 永豐期貨 dashboard card (spec §9): equity, month P&L and the frozen note on the left, the printed margins on
 * the right of one shared row grid; the card's own width picks one or two columns (container query, spec §9).
 * Loading, error and no statement yet are one compact line.
 */
@Component({
  selector: 'app-futures-card',
  imports: [BentoComponent, RouterLink],
  templateUrl: './futures-card.html',
  styleUrl: './futures-card.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FuturesCardComponent implements OnInit, OnChanges {
  private readonly portfolio = inject(PortfolioService);

  protected readonly state = signal<LoadState>('loading');
  protected readonly data = signal<FuturesSummary | null>(null);

  /** A changing value asks for a fresh read (the first value is the initial load's). */
  readonly refresh = input(0);

  ngOnInit(): void {
    this.load();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['refresh'] && !changes['refresh'].firstChange) this.load();
  }

  private load(): void {
    this.portfolio.getFuturesSummary().subscribe({
      next: summary => {
        this.data.set(summary);
        this.state.set('ready');
      },
      error: () => this.state.set('error'),
    });
  }

  protected money(value: Amount, change = false): string {
    return formatMoney(value, { sign: change ? 'change' : undefined });
  }

  protected sign(value: Amount): -1 | 0 | 1 {
    return displayedSign(value, formatMoney(value));
  }

  protected showMarginDate(d: FuturesSummary): boolean {
    return !!d.latest_daily && !!d.value && d.latest_daily.statement_date !== d.value.source_date;
  }

  protected md(date: string): string {
    return `${date.slice(5, 7)}/${date.slice(8, 10)}`;
  }

  /**
   * The margin warning (spec §9): 追繳 when a call is printed, 注意追繳 when the excess is already below zero but no
   * call yet; null in the plain state (追繳 $0 and 超額 ≥ 0). Judged on the displayed figure, like the trend colours.
   */
  protected marginWarning(daily: NonNullable<FuturesSummary['latest_daily']>): '追繳' | '注意追繳' | null {
    if (this.sign(daily.margin_call) > 0) return '追繳';
    if (this.sign(daily.excess_margin) < 0) return '注意追繳';
    return null;
  }
}
