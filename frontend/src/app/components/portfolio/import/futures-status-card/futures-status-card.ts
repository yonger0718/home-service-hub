import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, ElementRef, Injector, computed, effect, inject, signal, untracked } from '@angular/core';

import { FuturesSummary } from '../../../../models/portfolio.model';
import { NotificationCenterService } from '../../../../services/notification-center.service';
import { PortfolioService } from '../../../../services/portfolio.service';
import { guardedRetry } from '../../../../services/toast-retry';
import { injectToasts, toastText } from '../../../../services/toast.service';
import { shortDate } from '../../../accounting/dates';
import { knownWriteReason } from '../../../accounting/http-errors';
import { focusPageIfLost, focusRetryIfLost } from '../../../accounting/retry-focus';
import { FuturesItemView } from '../../../notifications/sources/sinopac-futures';
import { BannerComponent } from '../../../ui/banner/banner';
import { BentoComponent } from '../../../ui/bento/bento';
import { BtnComponent } from '../../../ui/btn/btn';
import { formatMoney } from '../../../ui/format';

/** An incident's 知道了 button (`id` = `<key>:<status>`). */
const ackSelector = (id: string) => `li.futures-item[data-item="${id}"] button`;
const NONE_SELECTOR = '.futures-none';
const RETRY_SELECTOR = '.banner-action';

type EquityState = { readonly state: 'loading' } | { readonly state: 'error' } | { readonly state: 'ready'; readonly summary: FuturesSummary };

/**
 * 永豐期貨對帳單 on the import page (spec §9): 權益總值 with its source date and the frozen note (§6.3), then the
 * notification centre's `futures()` view — the latest run and the pending incidents with 知道了 each (records the
 * incident as known; changes no data). Built as the 國泰 card.
 */
@Component({
  selector: 'app-futures-status-card',
  imports: [BentoComponent, BannerComponent, BtnComponent],
  templateUrl: './futures-status-card.html',
  styleUrl: './futures-status-card.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FuturesStatusCardComponent {
  protected readonly center = inject(NotificationCenterService);
  private readonly portfolio = inject(PortfolioService);
  private readonly toasts = injectToasts();
  private readonly host: HTMLElement = inject(ElementRef).nativeElement;
  private readonly injector = inject(Injector);

  protected readonly view = this.center.futures;
  protected readonly failed = computed(() => this.center.failedSources().includes('sinopac_futures'));
  protected readonly equity = signal<EquityState>({ state: 'loading' });

  /** Per incident id whose 知道了 was sent: null while in flight, then the re-read round it waits for (as 國泰). */
  private readonly waiting = signal<ReadonlyMap<string, number | null>>(new Map());
  private readonly focusedAtSend = new Set<string>();
  private retryRound: number | null = null;

  constructor() {
    // A released 知道了 whose button had focus: focus goes to the same row, else the next, else the previous, else the
    // none text — only if it is still lost after the render.
    effect(() => {
      const settled = this.center.settledRound();
      for (const [id, round] of this.waiting()) {
        if (round !== null && settled >= round && this.focusedAtSend.delete(id)) {
          focusPageIfLost(this.host, this.injector, untracked(() => this.focusTarget(id)));
        }
      }
    });
    // The unread banner's 重試 settled.
    effect(() => {
      const settled = this.center.settledRound();
      if (this.retryRound === null || settled < this.retryRound) {
        return;
      }
      this.retryRound = null;
      untracked(() => {
        if (this.failed()) {
          focusRetryIfLost(this.host, this.injector, RETRY_SELECTOR);
        } else {
          const first = this.center.futures()?.items[0];
          focusPageIfLost(this.host, this.injector, first ? ackSelector(first.id) : NONE_SELECTOR);
        }
      });
    });
    this.center.refresh({ reason: 'open' });
    this.portfolio.getFuturesSummary().subscribe({
      next: summary => this.equity.set({ state: 'ready', summary }),
      error: () => this.equity.set({ state: 'error' }),
    });
  }

  protected money(value: string): string {
    return formatMoney(value);
  }

  protected md(date: string): string {
    return shortDate(date);
  }

  protected isBusy(id: string): boolean {
    const round = this.waiting().get(id);
    if (round === undefined) {
      return false;
    }
    return round === null || this.center.settledRound() < round;
  }

  protected retry(): void {
    if (this.center.loading()) {
      return;
    }
    const focused = this.host.querySelector(RETRY_SELECTOR) === document.activeElement;
    const round = this.center.invalidate();
    this.retryRound = focused ? round : null;
  }

  /** 知道了; ignored while that incident is busy (one request per press). */
  protected ack(item: FuturesItemView): void {
    if (this.isBusy(item.id)) {
      return;
    }
    this.send(item);
  }

  /** Sends the ack (204, no body); the item is captured for 重試 (the row may have left by then). */
  private send(item: FuturesItemView): void {
    const { id, key, status, name } = item;
    if (this.host.querySelector(ackSelector(id)) === document.activeElement) {
      this.focusedAtSend.add(id);
    } else {
      this.focusedAtSend.delete(id);
    }
    this.setWaiting(id, null);
    this.portfolio.ackFuturesItem(key, status).subscribe({
      next: () => {
        this.toasts.done(`已處理：${name}`);
        this.reread(id);
      },
      error: (err: unknown) => {
        if (err instanceof HttpErrorResponse && err.status === 404) {
          // Not pending any more (acknowledged elsewhere, or by an earlier send that had committed).
          this.toasts.done(`${name}：已經不需要處理`);
        } else {
          console.error(`標記失敗 sinopac_futures:${id}（${name}）`, err);
          const reason = knownWriteReason(err);
          const text = reason === null ? `${name} 標記失敗` : toastText(`${name} 標記失敗`, reason);
          this.toasts.error(text, {
            retry: guardedRetry(err, this.toasts, text, () => this.isBusy(id), () => this.send(item)),
          });
        }
        // The ack may have committed.
        this.reread(id);
      },
    });
  }

  private reread(id: string): void {
    const round = this.center.invalidate();
    this.setWaiting(id, round);
  }

  /** Where a released incident's focus goes, from the view about to render (items sorted by key, then status). */
  private focusTarget(id: string): string {
    if (this.failed()) {
      return RETRY_SELECTOR;
    }
    const items = this.center.futures()?.items ?? [];
    // The view's order: key, then status (`id` is `<key>:<status>`; a key holds no colon).
    const split = id.lastIndexOf(':');
    const key = id.slice(0, split);
    const status = id.slice(split + 1);
    const order = (i: FuturesItemView) => i.key.localeCompare(key) || i.status.localeCompare(status);
    const target = items.find(i => order(i) === 0) ?? items.find(i => order(i) > 0) ?? [...items].reverse().find(i => order(i) < 0);
    return target ? ackSelector(target.id) : NONE_SELECTOR;
  }

  private setWaiting(id: string, round: number | null): void {
    const next = new Map(this.waiting());
    next.set(id, round);
    this.waiting.set(next);
  }
}
