import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subject, catchError, of, switchMap } from 'rxjs';

import { CardModule } from 'primeng/card';
import { ChartModule } from 'primeng/chart';
import { SelectButtonModule } from 'primeng/selectbutton';
import { SkeletonModule } from 'primeng/skeleton';

import { NetworthPoint } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';

type NetworthWindow = '1M' | '3M' | '1Y' | 'All';

interface NetworthWindowOption {
  label: NetworthWindow;
  value: NetworthWindow;
}

@Component({
  selector: 'app-networth-chart',
  standalone: true,
  imports: [CommonModule, FormsModule, CardModule, ChartModule, SelectButtonModule, SkeletonModule],
  templateUrl: './networth-chart.html',
  styleUrl: './networth-chart.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NetworthChartComponent implements OnInit {
  private portfolioService = inject(PortfolioService);
  private destroyRef = inject(DestroyRef);
  private loadTrigger$ = new Subject<NetworthWindow>();

  readonly windowOptions: NetworthWindowOption[] = [
    { label: '1M', value: '1M' },
    { label: '3M', value: '3M' },
    { label: '1Y', value: '1Y' },
    { label: 'All', value: 'All' },
  ];

  readonly selectedWindow = signal<NetworthWindow>('3M');
  readonly points = signal<NetworthPoint[]>([]);
  readonly loading = signal<boolean>(false);

  readonly chartData = computed(() => {
    const points = this.points();

    return {
      labels: points.map(point => point.date),
      datasets: [
        {
          label: '總市值',
          data: points.map(point => Number(point.total_market_value)),
          borderColor: '#2563eb',
          backgroundColor: '#2563eb',
          tension: 0.25,
          fill: false,
        },
        {
          label: '總成本',
          data: points.map(point => Number(point.total_cost)),
          borderColor: '#94a3b8',
          backgroundColor: '#94a3b8',
          borderDash: [6, 4],
          tension: 0.25,
          fill: false,
        },
      ],
    };
  });

  readonly chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index',
      intersect: false,
    },
    plugins: {
      legend: {
        position: 'top',
      },
    },
    scales: {
      x: {
        grid: {
          display: false,
        },
      },
      y: {
        beginAtZero: true,
      },
    },
  };

  ngOnInit(): void {
    this.loadTrigger$
      .pipe(
        switchMap(window => {
          this.loading.set(true);
          const range = this.getDateRange(window);
          return this.portfolioService.getNetworthHistory(range.from, range.to).pipe(
            catchError(error => {
              console.error('Failed to load networth history', error);
              return of<NetworthPoint[]>([]);
            }),
          );
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(points => {
        this.points.set(points);
        this.loading.set(false);
      });
    this.loadTrigger$.next(this.selectedWindow());
  }

  onWindowChange(window: NetworthWindow | null): void {
    if (!window || window === this.selectedWindow()) {
      return;
    }

    this.selectedWindow.set(window);
    this.loadTrigger$.next(window);
  }

  private getDateRange(window: NetworthWindow): { from?: string; to?: string } {
    if (window === 'All') {
      return {};
    }

    const days = {
      '1M': 30,
      '3M': 90,
      '1Y': 365,
    }[window];

    const today = new Date();
    const from = new Date(today);
    from.setDate(from.getDate() - days);

    return {
      from: this.formatDate(from),
      to: this.formatDate(today),
    };
  }

  private formatDate(date: Date): string {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');

    return `${year}-${month}-${day}`;
  }
}
