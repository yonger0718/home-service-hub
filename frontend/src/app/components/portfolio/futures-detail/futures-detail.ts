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

const MONTH_KEY = /^\d{4}-(0[1-9]|1[0-2])$/;

type LoadState = 'loading' | 'ready' | 'no-months' | 'not-found' | 'error';

interface SummaryRow {
  label: string;
  key: string;
  value: string;
  /** Trend sign for the up/down colour; 0 for rows that are not profit or loss. */
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
  protected readonly monthsFailed = signal(false);
  protected readonly months = signal<string[]>([]);
  protected readonly selected = signal<string | null>(null);
  protected readonly detail = signal<FuturesDetail | null>(null);
  private readonly expanded = signal<ReadonlySet<string>>(new Set());

  protected readonly summaryRows = computed<SummaryRow[]>(() => {
    const s = this.detail()?.summary;
    if (!s) return [];
    // 手續費＋期交稅 is a cost (unsigned) and 存提淨額 is cash moved: neither takes the gain/loss colour.
    const pnl = (label: string, key: string, value: string): SummaryRow => ({ label, key, value: this.money(value, true), sign: this.sign(value) });
    return [
      pnl('本月期貨平倉損益', 'realized', s.futures_realized),
      pnl('權利金收支', 'premium', s.premium_net),
      { label: '手續費＋期交稅', key: 'fees', value: this.money(s.fees_and_tax), sign: 0 },
      { label: '存提淨額', key: 'deposits', value: this.money(s.deposits_net, true), sign: 0 },
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
        const wanted = this.route.snapshot.queryParamMap.get('month');
        this.loadDetail(wanted !== null && MONTH_KEY.test(wanted) ? wanted : months[0]);
      },
      error: () => {
        this.monthsFailed.set(true);
        this.state.set('error');
      },
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

  /** 權利金市值 is stored unsigned; a sold (賣) option counts negative, as in the equity identity. */
  protected premiumValue(pos: { side: string; premium_value: Amount }): string {
    const raw = String(pos.premium_value);
    const negative = pos.side === '賣' && Number(raw) !== 0;
    return this.money(negative ? `-${raw.replace(/^-/, '')}` : raw, true);
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
