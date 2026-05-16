import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { catchError, of } from 'rxjs';

import { CardModule } from 'primeng/card';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { SkeletonModule } from 'primeng/skeleton';

import { UpcomingEvent } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';

@Component({
  selector: 'app-corporate-actions-panel',
  standalone: true,
  imports: [CommonModule, CardModule, TableModule, TagModule, SkeletonModule],
  templateUrl: './corporate-actions-panel.html',
  styleUrl: './corporate-actions-panel.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CorporateActionsPanelComponent implements OnInit {
  private portfolioService = inject(PortfolioService);
  private destroyRef = inject(DestroyRef);

  readonly events = signal<UpcomingEvent[]>([]);
  readonly loading = signal<boolean>(false);

  ngOnInit(): void {
    this.loading.set(true);
    this.portfolioService
      .getUpcomingEvents()
      .pipe(
        catchError(error => {
          console.error('Failed to load upcoming events', error);
          return of<UpcomingEvent[]>([]);
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(rows => {
        this.events.set(rows);
        this.loading.set(false);
      });
  }

  typeLabel(type: UpcomingEvent['type']): string {
    switch (type) {
      case 'CASH_DIV':
        return '除息';
      case 'STOCK_DIV':
        return '除權';
      case 'BOTH':
        return '除息+除權';
      case 'FACE_VALUE':
        return '面額變更';
    }
  }

  typeSeverity(type: UpcomingEvent['type']): 'info' | 'success' | 'warn' | 'danger' {
    switch (type) {
      case 'CASH_DIV':
        return 'success';
      case 'STOCK_DIV':
        return 'info';
      case 'BOTH':
        return 'warn';
      case 'FACE_VALUE':
        return 'danger';
    }
  }

  formatValue(row: UpcomingEvent): string {
    const parts: string[] = [];
    if (row.cash_dividend) {
      parts.push(`現金 ${row.cash_dividend}`);
    }
    if (row.stock_dividend_shares) {
      parts.push(`配股 ${row.stock_dividend_shares}/股`);
    }
    if (row.ratio) {
      parts.push(`比例 ${row.ratio}`);
    }
    return parts.join(' / ') || '-';
  }
}
