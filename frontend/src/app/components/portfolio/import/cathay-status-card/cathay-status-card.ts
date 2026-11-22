import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, ElementRef, Injector, computed, effect, inject, signal, untracked } from '@angular/core';

import { NotificationCenterService } from '../../../../services/notification-center.service';
import { PortfolioService } from '../../../../services/portfolio.service';
import { guardedRetry } from '../../../../services/toast-retry';
import { injectToasts, toastText } from '../../../../services/toast.service';
import { knownWriteReason } from '../../../accounting/http-errors';
import { focusPageIfLost, focusRetryIfLost } from '../../../accounting/retry-focus';
import { CathayDayView } from '../../../notifications/sources/cathay';
import { BannerComponent } from '../../../ui/banner/banner';
import { BentoComponent } from '../../../ui/bento/bento';
import { BtnComponent } from '../../../ui/btn/btn';

/** A date's 確認 button. */
const ackSelector = (tradeDate: string) => `li.cathay-day[data-trade-date="${tradeDate}"] button`;
const NONE_SELECTOR = '.cathay-none';
const RETRY_SELECTOR = '.banner-action';

/**
 * 國泰每日對帳單 on the import page: the notification centre's `cathay()` view (the same answer as the 通知 tasks and
 * the 匯入 dot), with 確認 per pending date.
 */
@Component({
  selector: 'app-cathay-status-card',
  imports: [BentoComponent, BannerComponent, BtnComponent],
  templateUrl: './cathay-status-card.html',
  styleUrl: './cathay-status-card.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CathayStatusCardComponent {
  protected readonly center = inject(NotificationCenterService);
  private readonly portfolio = inject(PortfolioService);
  private readonly toasts = injectToasts();
  private readonly host: HTMLElement = inject(ElementRef).nativeElement;
  private readonly injector = inject(Injector);

  protected readonly view = this.center.cathay;
  protected readonly failed = computed(() => this.center.failedSources().includes('cathay'));

  /**
   * Per trade date whose 確認 was sent: null while the ack is in flight, then the id `invalidate()` returned (the NEW
   * round, started after the answer). The row is busy until `settledRound() >= ` that id (C14): round N settling
   * releases it, and so does a later round M that superseded N (a superseded round never settles).
   */
  private readonly waiting = signal<ReadonlyMap<string, number | null>>(new Map());
  /** The dates whose 確認 had focus when it was sent: their release may hand focus on. */
  private readonly focusedAtSend = new Set<string>();
  /** The round the unread banner's 重試 started, while its 重試 had focus; null otherwise. */
  private retryRound: number | null = null;

  constructor() {
    // A released 確認 (C14) whose button had focus: the disabled button may have dropped focus to <body>, and the row
    // may be gone. Focus goes to the same row, else the next, else the previous, else the none text — only if it is
    // still lost after the render (focus the owner moved elsewhere stays).
    effect(() => {
      const settled = this.center.settledRound();
      for (const [tradeDate, round] of this.waiting()) {
        if (round !== null && settled >= round && this.focusedAtSend.delete(tradeDate)) {
          focusPageIfLost(this.host, this.injector, untracked(() => this.focusTarget(tradeDate)));
        }
      }
    });
    // The unread banner's 重試 settled: the banner is gone (first 確認, else the none text) or back (its 重試).
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
          const first = this.center.cathay()?.days[0];
          focusPageIfLost(this.host, this.injector, first ? ackSelector(first.tradeDate) : NONE_SELECTOR);
        }
      });
    });
    this.center.refresh({ reason: 'open' });
  }

  protected isBusy(tradeDate: string): boolean {
    const round = this.waiting().get(tradeDate);
    if (round === undefined) {
      return false;
    }
    return round === null || this.center.settledRound() < round;
  }

  /** 無法讀取 → 重試: a new round (the banner's button is disabled while one runs). */
  protected retry(): void {
    if (this.center.loading()) {
      return;
    }
    const focused = this.host.querySelector(RETRY_SELECTOR) === document.activeElement;
    const round = this.center.invalidate();
    this.retryRound = focused ? round : null;
  }

  /** 確認; ignored while that date is busy (one request per press). */
  protected ack(day: CathayDayView): void {
    if (this.isBusy(day.tradeDate)) {
      return;
    }
    this.send(day.tradeDate, `國泰對帳單 ${day.label}`);
  }

  /** Sends the ack; the date and its name are captured for 重試 (the row may have left by then). */
  private send(tradeDate: string, name: string): void {
    if (this.host.querySelector(ackSelector(tradeDate)) === document.activeElement) {
      this.focusedAtSend.add(tradeDate);
    } else {
      this.focusedAtSend.delete(tradeDate);
    }
    this.setWaiting(tradeDate, null);
    this.portfolio.ackCathayDay(tradeDate).subscribe({
      next: () => {
        this.toasts.done(`已確認：${name}`);
        this.reread(tradeDate);
      },
      error: (err: unknown) => {
        if (err instanceof HttpErrorResponse && err.status === 404) {
          // The date no longer needs the owner (acked elsewhere, or by an earlier send that had committed).
          this.toasts.done(`${name}：已經不需要確認`);
        } else {
          // Only a reason the app words itself reaches the owner; the raw error is for the console.
          console.error(`確認失敗 cathay:${tradeDate}（${name}）`, err);
          const reason = knownWriteReason(err);
          const text = reason === null ? `${name} 確認失敗` : toastText(`${name} 確認失敗`, reason);
          this.toasts.error(text, {
            retry: guardedRetry(err, this.toasts, text, () => this.isBusy(tradeDate), () => this.send(tradeDate, name)),
          });
        }
        // The ack may have committed.
        this.reread(tradeDate);
      },
    });
  }

  /** An ack bumps no change signal: start the re-read and wait for that round (or a later one) to settle. */
  private reread(tradeDate: string): void {
    const round = this.center.invalidate();
    this.setWaiting(tradeDate, round);
  }

  /**
   * Where a released date's focus goes, from the view about to render (dates sorted by trade date): its own 確認 if
   * still listed, else the next date's, else the previous date's, else the none text; the 重試 when the read failed.
   */
  private focusTarget(tradeDate: string): string {
    if (this.failed()) {
      return RETRY_SELECTOR;
    }
    const days = this.center.cathay()?.days ?? [];
    const target =
      days.find(d => d.tradeDate === tradeDate) ??
      days.find(d => d.tradeDate > tradeDate) ??
      [...days].reverse().find(d => d.tradeDate < tradeDate);
    return target ? ackSelector(target.tradeDate) : NONE_SELECTOR;
  }

  private setWaiting(tradeDate: string, round: number | null): void {
    const next = new Map(this.waiting());
    next.set(tradeDate, round);
    this.waiting.set(next);
  }
}
