import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  Signal,
  afterNextRender,
  computed,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { RouterLink } from '@angular/router';

import { AccountingService } from '../../services/accounting.service';
import { LayoutModeService } from '../../services/layout-mode.service';
import { NotificationCenterService } from '../../services/notification-center.service';
import { injectToasts, toastText } from '../../services/toast.service';
import { guardedRetry } from '../../services/toast-retry';
import { writeErrorMessage } from '../accounting/http-errors';
import { IMPORT_RUNNING_TOAST, isImportRunning } from '../accounting/schedule-math';
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
import { NOTIF_PRIMARY_CONTROL, NotifRowComponent } from './notif-row/notif-row';

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
 * counted tasks by tier, the folded 追蹤中 (never counted) and the links out. 入帳 / 全部入帳 run in place (R4); Task 10
 * owns the loading / failure / empty states.
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
  private readonly accounting = inject(AccountingService);
  private readonly toasts = injectToasts();
  private readonly host: HTMLElement = inject(ElementRef).nativeElement;
  private readonly injector = inject(Injector);

  /**
   * Rows whose command runs or has answered: task id → `null` while the write runs, then the id of the newest round
   * started when it answered. The row stays busy until a round with a greater id has settled (R26, P6): only such a
   * round was started after the answer, so only its reads can see the write. A superseded round never settles.
   */
  private readonly busy = signal<ReadonlyMap<string, number | null>>(new Map());
  /** Tasks whose command answered (ok or failed) and whose row may yet leave the list: focus follows them (P6). */
  private readonly answered = new Set<string>();
  /** The task ids in DOM order at the last render of the sections. */
  private visibleIds: readonly string[] = [];

  protected readonly chips = CHIPS;
  protected readonly chip = signal<NotifChip>('all');
  protected readonly phone = computed(() => this.layout.mode() === 'phone');
  protected readonly counts = computed(() => chipCounts(this.center.tasks()));

  /** The counted tiers that have a task under the chosen chip, rows in the service's order. */
  protected readonly sections: Signal<{ tier: NotifTier; label: string; tasks: NotifTask[] }[]> =
    computed(() => {
      if (!this.center.loaded()) {
        return [];
      }
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
    () => this.center.loaded() && this.chip() !== 'all' && this.counts()[this.chip()] === 0 && this.counts().all > 0,
  );

  protected readonly subtitle = computed<string | null>(() => {
    // `summary()` is NO_TASKS until the first round settles: no subtitle rather than a false all-clear.
    if (!this.center.loaded()) {
      return null;
    }
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
  protected readonly trackingRows = computed(() =>
    this.center.loaded() && this.chip() !== 'portfolio' ? this.center.trackingRows() : [],
  );
  protected readonly trackingSum = computed(() =>
    this.trackingRows()
      .map((row) => `${TRACKING_UNIT[row.id].name} ${row.count} ${TRACKING_UNIT[row.id].unit}`)
      .join(' · '),
  );

  constructor() {
    this.center.refresh({ reason: 'open' });

    // Release every row whose waiting round has been passed by a settled one.
    effect(() => {
      const settled = this.center.settledRound();
      const busy = this.busy();
      const released = [...busy].filter(([, round]) => round !== null && settled > round).map(([id]) => id);
      if (released.length === 0) {
        return;
      }
      const next = new Map(busy);
      const listed = new Set(untracked(this.center.tasks).map(task => task.id));
      for (const id of released) {
        next.delete(id);
        // Still listed once its re-read settled: the row stays, and so does focus.
        if (listed.has(id)) {
          this.answered.delete(id);
        }
      }
      this.busy.set(next);
    });

    // A row whose command answered leaves the list: focus that fell to <body> goes to the row now at its place.
    effect(() => {
      const ids = this.sections().flatMap(section => section.tasks.map(task => task.id));
      const previous = this.visibleIds;
      this.visibleIds = ids;
      const shown = new Set(ids);
      for (const id of [...this.answered]) {
        if (shown.has(id)) {
          continue;
        }
        this.answered.delete(id);
        const index = previous.indexOf(id);
        if (index >= 0) {
          this.restoreFocus(index);
        }
      }
    });
  }

  protected pick(chip: NotifChip): void {
    this.chip.set(chip);
  }

  protected chipName(option: ChipOption): string {
    return this.center.loaded() ? `${option.label}，${this.counts()[option.key]} 件` : option.label;
  }

  protected isBusy(id: string): boolean {
    return this.busy().has(id);
  }

  /** A row's 入帳 / 全部入帳; ignored while that row is busy (one write per press). */
  protected onRun(task: NotifTask, command: NotifCommand): void {
    if (this.isBusy(task.id)) {
      return;
    }
    this.send(task.id, command);
  }

  /**
   * Sends the write and owns the row's busy state. Not tied to the page's life: the write must finish, and the toast
   * scope shows a late answer without its action. The task id and the command are captured for 重試.
   */
  private send(taskId: string, command: NotifCommand): void {
    this.setBusy(taskId, null);
    const failed = (err: unknown) => this.failed(taskId, command, err);
    if (command.kind === 'postInstance') {
      this.accounting.postScheduleInstance(command.instanceId).subscribe({
        next: () => this.succeeded(taskId, () => this.toasts.done(`已入帳：${command.name}`)),
        error: failed,
      });
    } else {
      this.accounting.catchUpSchedule(command.definitionId).subscribe({
        next: ({ posted, failed: stop }) =>
          this.succeeded(taskId, () =>
            stop === null
              ? this.toasts.done(`已入帳 ${posted.length} 期：${command.name}`)
              : this.toasts.error(`${command.name}：已入帳 ${posted.length} 期，之後的入帳失敗（${stop.error}）`),
          ),
        error: failed,
      });
    }
  }

  /**
   * The write bumped `entriesChanged`; the service's effect starts the round that re-reads after it, later. So
   * `round()` here is still the pre-write round, and the row waits for a greater one. No refresh / invalidate here.
   */
  private succeeded(taskId: string, toast: () => void): void {
    toast();
    this.markAnswered(taskId);
    this.setBusy(taskId, this.center.round());
  }

  /**
   * An error bumps nothing, and the write may still have committed (a 5xx after the commit; the interceptor's
   * resend): re-read now. The reads raise no toast, so the 失敗 toast stays the visible one.
   */
  private failed(taskId: string, command: NotifCommand, err: unknown): void {
    if (isImportRunning(err)) {
      this.toasts.error(IMPORT_RUNNING_TOAST);
    } else {
      const text = toastText(`${command.name} 入帳失敗`, writeErrorMessage(err));
      this.toasts.error(text, {
        retry: guardedRetry(err, this.toasts, text, () => this.isBusy(taskId), () => this.send(taskId, command)),
      });
    }
    this.markAnswered(taskId);
    const after = this.center.round();
    this.center.invalidate();
    this.setBusy(taskId, after);
  }

  private setBusy(taskId: string, round: number | null): void {
    const next = new Map(this.busy());
    next.set(taskId, round);
    this.busy.set(next);
  }

  /** Focus follows the row if it leaves; only a row on screen now can hold focus. */
  private markAnswered(taskId: string): void {
    if (this.visibleIds.includes(taskId)) {
      this.answered.add(taskId);
    }
  }

  /**
   * After the render: when focus fell to `<body>`, the native control of the row now at `index` (R28), else the
   * 追蹤中 summary, else the first chip — the first of those that is enabled. Focus the owner moved is left alone.
   */
  private restoreFocus(index: number): void {
    afterNextRender(
      () => {
        const active = document.activeElement;
        if (active && active !== document.body) {
          return;
        }
        const rows = this.host.querySelectorAll('app-notif-row');
        const candidates = [
          rows[index]?.querySelector<HTMLElement>(NOTIF_PRIMARY_CONTROL) ?? null,
          this.host.querySelector<HTMLElement>('details.notif-tracking > summary'),
          this.host.querySelector<HTMLElement>('.notif-chips button'),
        ];
        const target = candidates.find(el => el !== null && !(el as HTMLButtonElement).disabled);
        target?.focus({ preventScroll: true });
      },
      { injector: this.injector },
    );
  }
}
