import { ChangeDetectionStrategy, Component, OnChanges, OnInit, SimpleChanges, inject, input, signal } from '@angular/core';

import { FuturesSummary } from '../../../../models/portfolio.model';
import { PortfolioService } from '../../../../services/portfolio.service';
import { BentoComponent } from '../../../ui/bento/bento';
import { Amount, displayedSign, formatMoney } from '../../../ui/format';

type LoadState = 'loading' | 'ready' | 'error';

/** The 永豐期貨 dashboard card (spec §9): equity, printed margins, month P&L, frozen note. */
@Component({
  selector: 'app-futures-card',
  imports: [BentoComponent],
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

  protected marginCall(value: string): boolean {
    return Number(value) > 0;
  }
}
