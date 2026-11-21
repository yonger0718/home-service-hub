import { ChangeDetectionStrategy, Component, Signal, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { LayoutModeService } from '../../services/layout-mode.service';
import { NotificationCenterService } from '../../services/notification-center.service';
import { PageHeaderComponent } from '../shell/page-header/page-header';
import {
  COUNTED_TIERS,
  NotifChip,
  NotifCommand,
  NotifTask,
  NotifTier,
  NotifTrackingRow,
  SOURCE_TAGS,
  TIER_LABELS,
  chipCounts,
} from './notif-task';
import { NotifRowComponent } from './notif-row/notif-row';

interface ChipOption {
  readonly key: NotifChip;
  readonly label: string;
}

const CHIPS: readonly ChipOption[] = [
  { key: 'all', label: '全部' },
  { key: 'ledger', label: SOURCE_TAGS.ledger },
  { key: 'portfolio', label: SOURCE_TAGS.portfolio },
];

/** What the tracking summary counts, per row. */
const TRACKING_UNIT: Record<NotifTrackingRow['id'], { name: string; unit: string }> = {
  debts: { name: '借還款', unit: '位' },
  upcoming: { name: '即將到來', unit: '件' },
};

/**
 * `/notifications`: one column (R20). Header with the count, source chips (a radiogroup, as 提醒中心's tabs), the
 * counted tasks by tier, the folded 追蹤中 (never counted) and the links out. Task 9 wires `run`; Task 10 owns the
 * loading / failure / empty states.
 */
@Component({
  selector: 'app-notifications',
  imports: [NotifRowComponent, PageHeaderComponent, RouterLink],
  templateUrl: './notifications.html',
  styleUrl: './notifications.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NotificationsComponent {
  protected readonly center = inject(NotificationCenterService);
  private readonly layout = inject(LayoutModeService);

  protected readonly chips = CHIPS;
  protected readonly chip = signal<NotifChip>('all');
  protected readonly phone = computed(() => this.layout.mode() === 'phone');
  protected readonly counts = computed(() => chipCounts(this.center.tasks()));

  /** The counted tiers that have a task under the chosen chip, rows in the service's order. */
  protected readonly sections: Signal<{ tier: NotifTier; label: string; tasks: NotifTask[] }[]> =
    computed(() => {
      const chip = this.chip();
      const tasks = this.center.tasks().filter((t) => chip === 'all' || t.source === chip);
      return COUNTED_TIERS.map((tier) => ({
        tier,
        label: TIER_LABELS[tier],
        tasks: tasks.filter((t) => t.tier === tier),
      })).filter((s) => s.tasks.length > 0);
    });

  /** 全部 has work but the chosen source has none. */
  protected readonly chipEmpty = computed(
    () => this.chip() !== 'all' && this.counts()[this.chip()] === 0 && this.counts().all > 0,
  );

  protected readonly subtitle = computed(() => {
    const { total, overdue } = this.center.summary();
    if (total === 0) {
      return '沒有要處理的事';
    }
    const parts = [`${total} 件要處理`];
    if (overdue > 0) {
      parts.push(`${overdue} 件已逾期`);
    }
    if (!this.phone()) {
      parts.push('數字只算要你動手的事');
    }
    return parts.join(' · ');
  });

  /** 追蹤中 is a ledger thing: hidden under 投資, and without rows. */
  protected readonly trackingRows = computed(() => (this.chip() === 'portfolio' ? [] : this.center.trackingRows()));
  protected readonly trackingSum = computed(() =>
    this.trackingRows()
      .map((row) => `${TRACKING_UNIT[row.id].name} ${row.count} ${TRACKING_UNIT[row.id].unit}`)
      .join(' · '),
  );

  constructor() {
    this.center.refresh({ reason: 'open' });
  }

  protected pick(chip: NotifChip): void {
    this.chip.set(chip);
  }

  protected chipName(option: ChipOption): string {
    return `${option.label}，${this.counts()[option.key]} 件`;
  }

  protected isBusy(_id: string): boolean {
    return false;
  }

  /** Task 9 implements the in-place commands. */
  protected onRun(_task: NotifTask, _command: NotifCommand): void {}
}
