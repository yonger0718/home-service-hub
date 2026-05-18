import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  ViewChild,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { finalize, interval, switchMap, takeUntil, takeWhile, timer } from 'rxjs';
import { PortfolioService } from '../../../services/portfolio.service';
import { PortfolioSummary, ExDividendRecord } from '../../../models/portfolio.model';
import { CardModule } from 'primeng/card';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { ButtonModule } from 'primeng/button';
import { TooltipModule } from 'primeng/tooltip';
import { AccordionModule } from 'primeng/accordion';
import { SkeletonModule } from 'primeng/skeleton';
import { MessageService } from 'primeng/api';
import { NetworthChartComponent } from '../networth-chart/networth-chart';
import { CorporateActionsPanelComponent } from '../corporate-actions-panel/corporate-actions-panel';

@Component({
  selector: 'app-portfolio-dashboard',
  imports: [CommonModule, CardModule, TableModule, TagModule, ButtonModule, TooltipModule, AccordionModule, SkeletonModule, NetworthChartComponent, CorporateActionsPanelComponent],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class PortfolioDashboardComponent implements OnInit {
  private portfolioService = inject(PortfolioService);
  private messageService = inject(MessageService, { optional: true });
  private destroyRef = inject(DestroyRef);
  protected readonly Number = Number;

  @ViewChild(NetworthChartComponent) chart?: NetworthChartComponent;

  summary = signal<PortfolioSummary | null>(null);
  upcomingExDividends = signal<ExDividendRecord[]>([]);
  loading = signal<boolean>(false);
  showWithDividend = signal<boolean>(false);
  expandedSymbols = signal<Set<string>>(new Set());

  ngOnInit() {
    this.loadSummary();
    this.loadExDividends();
  }

  toggleDividend() {
    this.showWithDividend.set(!this.showWithDividend());
  }

  toggleHoldingExpand(symbol: string) {
    const next = new Set(this.expandedSymbols());
    if (next.has(symbol)) {
      next.delete(symbol);
    } else {
      next.add(symbol);
    }
    this.expandedSymbols.set(next);
  }

  isHoldingExpanded(symbol: string): boolean {
    return this.expandedSymbols().has(symbol);
  }

  loadSummary() {
    this.loading.set(true);
    this.portfolioService.refreshQuotes().subscribe({
      next: (response) => {
        if (response?.refresh_scheduled) {
          this.pollRecalcStatus();
          return;
        }

        this.reloadSummaryAndChart();
      },
      error: (err) => {
        if (err.status === 409) {
          this.notify('另一筆重算進行中, 稍候再試');
        } else if (err.status !== 204) {
          console.error('refreshQuotes failed', err);
        }
        this.reloadSummaryAndChart();
      },
    });
  }

  private pollRecalcStatus(): void {
    interval(1000)
      .pipe(
        switchMap(() => this.portfolioService.getRecalcStatus()),
        takeWhile(
          status => !['completed', 'partial', 'failed'].includes(status.state),
          true,
        ),
        takeUntil(timer(30000)),
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.reloadSummaryAndChart()),
      )
      .subscribe({
        error: (err) => console.error('getRecalcStatus failed', err),
      });
  }

  private reloadSummaryAndChart(): void {
    this.portfolioService.getSummary().subscribe({
      next: (data) => {
        this.summary.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        console.error('Failed to load portfolio summary', err);
        this.loading.set(false);
      }
    });
    this.chart?.reload();
  }

  private notify(msg: string): void {
    if (this.messageService) {
      this.messageService.add({
        severity: 'warn',
        summary: '提醒',
        detail: msg,
      });
      return;
    }

    console.warn(msg);
  }

  getPnlColor(value: number | string): 'success' | 'secondary' | 'info' | 'warn' | 'danger' | 'contrast' {
    const num = Number(value);
    if (num > 0) return 'danger';
    if (num < 0) return 'success';
    return 'info';
  }

  formatCurrency(value: number | string): string {
    return new Intl.NumberFormat('zh-TW', { style: 'currency', currency: 'TWD', minimumFractionDigits: 0 }).format(Number(value));
  }

  formatXirr(value: number | null | undefined): string {
    if (value == null) return 'N/A';
    return (value * 100).toFixed(2) + '%';
  }

  loadExDividends() {
    this.portfolioService.getUpcomingExDividends().subscribe({
      next: (data) => this.upcomingExDividends.set(data),
      error: () => this.upcomingExDividends.set([])
    });
  }
}
