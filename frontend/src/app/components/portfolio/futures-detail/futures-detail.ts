import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';

import { FuturesDay, FuturesDeposit, FuturesDetail, FuturesFill } from '../../../models/portfolio.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PortfolioService } from '../../../services/portfolio.service';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { BentoComponent } from '../../ui/bento/bento';
import { Amount, displayedSign, formatMoney } from '../../ui/format';

type LoadState = 'loading' | 'ready' | 'no-months' | 'not-found' | 'error';

interface SummaryRow {
  label: string;
  value: string;
  sign: -1 | 0 | 1;
}

/**
 * 期貨明細: one month of the 永豐期貨 statements (summary, per-day fills with their closing links, the open
 * positions). The month is the `month` query param, else the newest one; the picker keeps the URL in step.
 */
@Component({
  selector: 'app-portfolio-futures-detail',
  imports: [PageHeaderComponent, BentoComponent],
  templateUrl: './futures-detail.html',
  styleUrl: './futures-detail.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioFuturesDetailComponent implements OnInit, OnDestroy {
  private readonly portfolio = inject(PortfolioService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly layoutMode = inject(LayoutModeService);
  private monthsSub?: Subscription;
  private detailSub?: Subscription;

  protected readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  protected readonly state = signal<LoadState>('loading');
  protected readonly months = signal<string[]>([]);
  protected readonly selected = signal<string | null>(null);
  protected readonly detail = signal<FuturesDetail | null>(null);
  private readonly expanded = signal<ReadonlySet<string>>(new Set());

  protected readonly summaryRows = computed<SummaryRow[]>(() => {
    const s = this.detail()?.summary;
    if (!s) return [];
    const row = (label: string, value: string): SummaryRow => ({ label, value: this.money(value, true), sign: this.sign(value) });
    return [
      row('本月期貨平倉損益', s.futures_realized),
      row('權利金收支', s.premium_net),
      row('手續費＋期交稅', s.fees_and_tax),
      row('存提淨額', s.deposits_net),
    ];
  });

  ngOnInit(): void {
    this.monthsSub = this.portfolio.getFuturesMonths().subscribe({
      next: ({ months }) => {
        this.months.set(months);
        if (months.length === 0) {
          this.state.set('no-months');
          return;
        }
        this.loadDetail(this.route.snapshot.queryParamMap.get('month') ?? months[0]);
      },
      error: () => this.state.set('error'),
    });
  }

  ngOnDestroy(): void {
    this.monthsSub?.unsubscribe();
    this.detailSub?.unsubscribe();
  }

  protected pick(month: string): void {
    if (month === this.selected()) return;
    void this.router.navigate([], { relativeTo: this.route, queryParams: { month }, replaceUrl: true });
    this.loadDetail(month);
  }

  private loadDetail(month: string): void {
    this.detailSub?.unsubscribe();
    this.selected.set(month);
    this.state.set('loading');
    this.expanded.set(new Set());
    this.detailSub = this.portfolio.getFuturesDetail(month).subscribe({
      next: detail => {
        this.detail.set(detail);
        this.state.set('ready');
      },
      error: (err: unknown) => {
        this.detail.set(null);
        this.state.set(err instanceof HttpErrorResponse && err.status === 404 ? 'not-found' : 'error');
      },
    });
  }

  protected monthLabel(key: string): string {
    return `${key.slice(0, 4)} 年 ${Number(key.slice(5, 7))} 月`;
  }

  protected md(date: string): string {
    return `${date.slice(5, 7)}/${date.slice(8, 10)}`;
  }

  protected money(value: Amount, change = false): string {
    return formatMoney(value, { sign: change ? 'change' : undefined });
  }

  protected sign(value: Amount): -1 | 0 | 1 {
    return displayedSign(value, formatMoney(value));
  }

  protected pnlLabel(fill: FuturesFill): string {
    return fill.kind === 'option' ? '權利金' : '平倉損益';
  }

  protected strikeText(fill: { strike: string | null }): string {
    return fill.strike ?? '—';
  }

  protected rightText(fill: { right: 'Call' | 'Put' | null }): string {
    return fill.right ?? '—';
  }

  protected depositLabel(d: FuturesDeposit): string {
    return d.direction === 'in' ? '入金' : '出金';
  }

  protected fillKey(day: FuturesDay, index: number): string {
    return `${day.date}#${index}`;
  }

  protected isOpen(key: string): boolean {
    return this.expanded().has(key);
  }

  protected toggle(key: string): void {
    const next = new Set(this.expanded());
    if (!next.delete(key)) next.add(key);
    this.expanded.set(next);
  }
}
