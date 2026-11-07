import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subscription } from 'rxjs';

import { EntryDetail, RewardReprice, RewardRepriceItem } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { REWARD_POSTED } from '../review-reason';

const ATTENTION = new Set<RewardRepriceItem['status']>(['rule_unlinked', 'window_moved', 'zero']);

type PreviewState = { status: 'loading' } | { status: 'failed' } | { status: 'ready'; preview: RewardReprice };

/**
 * 回饋已入帳，消費金額已改 card on the entry detail (#306, spec §8): each posted reward's 已入帳 → 新算, with
 * 依新金額重算回饋 (only when a row would change) and 保持原樣. 保持原樣 clears 待確認; 重算 clears it unless a row is
 * left for the owner (rule unlinked, moved window, new 0), and the card then stays. Nothing changes before a tap.
 */
@Component({
  selector: 'app-entry-reward-reprice-card',
  standalone: true,
  templateUrl: './reward-reprice-card.html',
  styles: `
    .reprice-card { border: 1px solid var(--app-border); border-radius: 10px; margin: 12px 0 4px; padding: 10px 12px; }
    .reprice-title { font-size: 0.9rem; font-weight: 800; margin: 0 0 6px; }
    .reprice-line { display: flex; flex-direction: column; font-size: 0.88rem; gap: 2px; padding: 4px 0; }
    .reprice-sub { color: var(--app-text-muted); font-size: 0.8rem; }
    .reprice-error { color: var(--app-danger); font-size: 0.85rem; margin: 4px 0 0; }
    .reprice-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    .reprice-actions button { min-height: 44px; }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppEntryRewardRepriceCardComponent {
  private readonly service = inject(AccountingService);
  private readonly destroyRef = inject(DestroyRef);

  readonly detail = input.required<EntryDetail>();
  /** Formats a reward figure in its unit (money, or points / miles), keyed by the accrual it belongs to. */
  readonly amountText = input.required<(accrualId: number, value: string) => string>();
  readonly changed = output<void>();

  readonly visible = computed(() => this.detail().review_reason === REWARD_POSTED);
  readonly state = signal<PreviewState>({ status: 'loading' });
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);

  /** 依新金額重算回饋 shows only when it would change something. */
  readonly canReprice = computed(() => {
    const state = this.state();
    return state.status === 'ready' && state.preview.items.some(item => item.status === 'reprice');
  });

  /** A row the reprice leaves for the owner (the flag then stays): mirrors the API's `ATTENTION`. */
  readonly needsOwner = computed(() => {
    const state = this.state();
    return state.status === 'ready' && state.preview.items.some(item => ATTENTION.has(item.status));
  });

  private previewSub?: Subscription;

  constructor() {
    // Keyed on the detail object: every reload (a write here or elsewhere) re-reads the preview, so a card that stays
    // after 重算 (rows left for the owner) shows the rows as they now are.
    const key = computed(() => (this.visible() ? this.detail() : null));
    effect(() => {
      const detail = key();
      untracked(() => {
        this.busy.set(false);
        this.error.set(null);
        if (detail !== null) {
          this.load(detail.id);
        }
      });
    });
  }

  load(id = this.detail().id): void {
    this.previewSub?.unsubscribe();
    this.state.set({ status: 'loading' });
    this.previewSub = this.service.getRewardReprice(id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: preview => this.state.set({ status: 'ready', preview }),
      error: () => this.state.set({ status: 'failed' }),
    });
  }

  /** One line per accrual: what it was booked at and what happens to it. */
  lineText(item: RewardRepriceItem): string {
    const booked = this.amountText()(item.accrual_id, item.booked);
    switch (item.status) {
      case 'reprice':
        return `已入帳 ${booked} → 新算 ${this.amountText()(item.accrual_id, item.new ?? '0')}`;
      case 'same':
        return `已入帳 ${booked}，金額不變`;
      case 'zero':
        return `已入帳 ${booked}，新算 0（已超過上限），請刪除或修改回饋記錄`;
      case 'confirmed':
        return `銀行確認 ${booked}，以銀行為準`;
      case 'rule_unlinked':
        return `已入帳 ${booked}，這筆已不套用此規則，請刪除或修改回饋記錄`;
      case 'window_moved':
        return `已入帳 ${booked}，消費已改到別期，請刪除或修改回饋記錄`;
      case 'row_edited':
        return `已入帳 ${booked}，回饋記錄已手動修改，不改`;
    }
  }

  apply(keep: boolean): void {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.service.applyRewardReprice(this.detail().id, keep).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      // busy stays on until the reloaded detail (flag cleared) hides the card, so a second tap cannot write again.
      next: () => this.changed.emit(),
      error: () => {
        this.busy.set(false);
        this.error.set(keep ? '保持原樣失敗，請稍後再試。' : '重算回饋失敗，請稍後再試。');
      },
    });
  }
}
