import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { NotificationCenterService } from '../../../../services/notification-center.service';
import { PortfolioService } from '../../../../services/portfolio.service';
import { guardedRetry } from '../../../../services/toast-retry';
import { injectToasts, toastText } from '../../../../services/toast.service';
import { knownWriteReason } from '../../../accounting/http-errors';
import { CathayDayView } from '../../../notifications/sources/cathay';
import { BannerComponent } from '../../../ui/banner/banner';
import { BentoComponent } from '../../../ui/bento/bento';
import { BtnComponent } from '../../../ui/btn/btn';

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

  protected readonly view = this.center.cathay;
  protected readonly failed = computed(() => this.center.failedSources().includes('cathay'));

  /**
   * Per trade date whose 確認 was sent: null while the ack is in flight, then the id `invalidate()` returned (the NEW
   * round, started after the answer). The row is busy until `settledRound() >= ` that id (C14): round N settling
   * releases it, and so does a later round M that superseded N (a superseded round never settles).
   */
  private readonly waiting = signal<ReadonlyMap<string, number | null>>(new Map());

  constructor() {
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
    this.center.invalidate();
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

  private setWaiting(tradeDate: string, round: number | null): void {
    const next = new Map(this.waiting());
    next.set(tradeDate, round);
    this.waiting.set(next);
  }
}
