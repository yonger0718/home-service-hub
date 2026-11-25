import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';

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
export class FuturesCardComponent implements OnInit {
  private readonly portfolio = inject(PortfolioService);

  protected readonly state = signal<LoadState>('loading');
  protected readonly data = signal<FuturesSummary | null>(null);

  ngOnInit(): void {
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

  protected md(date: string): string {
    return `${date.slice(5, 7)}/${date.slice(8, 10)}`;
  }

  protected marginCall(value: string): boolean {
    return Number(value) > 0;
  }
}
