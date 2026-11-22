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
import { HttpErrorResponse } from '@angular/common/http';
import { RouterLink } from '@angular/router';

import { AccountingService } from '../../services/accounting.service';
import { LayoutModeService } from '../../services/layout-mode.service';
import { NotificationCenterService } from '../../services/notification-center.service';
import { injectToasts } from '../../services/toast.service';
import { guardedRetry } from '../../services/toast-retry';
import { IMPORT_RUNNING_TOAST, isImportRunning } from '../accounting/schedule-math';
import { apiErrorText, splitApiCode } from '../accounting/statements/statement-view';
import { focusPageIfLost, focusRetryIfLost } from '../accounting/retry-focus';
import { BannerComponent } from '../ui/banner/banner';
import { PageHeaderComponent } from '../shell/page-header/page-header';
import {
  COUNTED_TIERS,
  NotifChip,
  NotifCommand,
  NotifTask,
  NotifTier,
  NotifTrackingRow,
  SOURCE_NAMES,
  SOURCE_TAGS,
  TIER_LABELS,
  chipCounts,
  failureLine,
  isCommand,
} from './notif-task';
import { NOTIF_PRIMARY_CONTROL, NotifRowComponent } from './notif-row/notif-row';

/** 409 `already_posted`: the instance was posted before this request (by its own earlier send, or elsewhere). */
function isAlreadyPosted(err: unknown): boolean {
  if (!(err instanceof HttpErrorResponse) || err.status !== 409) {
    return false;
  }
  const text = apiErrorText(err);
  return text !== null && splitApiCode(text).code === 'already_posted';
}

interface ChipOption {
  readonly key: NotifChip;
  readonly label: string;
}

const CHIPS: readonly ChipOption[] = [
  { key: 'all', label: '全部' },
  { key: 'ledger', label: SOURCE_TAGS.ledger },
  { key: 'portfolio', label: SOURCE_TAGS.portfolio },
];

/** Every task source of a round. */
const ALL_SOURCES = Object.keys(SOURCE_NAMES).length;

/** What the tracking summary counts, per row. */
const TRACKING_UNIT: Record<NotifTrackingRow['id'], { name: string; unit: string }> = {
  debts: { name: '借還款', unit: '位' },
  upcoming: { name: '即將到來', unit: '件' },
};

/**
 * `/notifications`: one column (R20). Header with the count, source chips (a radiogroup, as 提醒中心's tabs), the
 * counted tasks by tier, the folded 追蹤中 (never counted) and the links out. 入帳 / 全部入帳 run in place (R4). Before the first round
 * settles a 讀取中… row ends the page (the only one, whether or not tasks have arrived); then the empty state or the
 * failure line.
 */
@Component({
  selector: 'app-notifications',
  imports: [BannerComponent, NotifRowComponent, PageHeaderComponent, RouterLink],
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
  /** Tasks whose row's primary control had focus when their command was sent (a disabled button loses it in a browser). */
  private readonly focusedAtSend = new Set<string>();
  /** The task ids in DOM order at the last render of the sections. */
  private visibleIds: readonly string[] = [];

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
    () => this.center.loaded() && this.chip() !== 'all' && this.counts()[this.chip()] === 0 && this.counts().all > 0,
  );

  /**
   * Nothing to show yet: no round has settled, or a later round is in flight and there is no task to show. The
   * all-clear is a claim about the CURRENT round, so "no task" from the last settled round is not enough while a
   * newer round may still find one (e.g. a card added elsewhere whose statement read is pending): treated like the
   * first load. With tasks listed, a later round changes nothing: rows, section numbers, chip counts and subtitle stay
   * as the last settled round left them (the last known truth; hiding the numbers on every refresh, e.g. after each
   * 入帳, would flicker). A failed or truncated source keeps its own treatment and is never replaced by this.
   */
  private readonly pending = computed(
    () =>
      !this.center.loaded() ||
      (this.center.loading() &&
        this.center.tasks().length === 0 &&
        this.center.failedSources().length === 0 &&
        !this.center.truncated()),
  );

  /** Every source of the round failed: nothing was read, so there is no number to show (a partial failure still has them). */
  private readonly allFailed = computed(() => this.center.failedSources().length >= ALL_SOURCES);

  /**
   * The counts are shown: a round settled and at least one source was read. With one source failed they are lower
   * bounds, but they are the same numbers the sidebar shows (the banner says the list is partial); with every source
   * failed there is nothing to count.
   */
  protected readonly countsFinal = computed(() => !this.pending() && !this.allFailed());

  /**
   * Never absent (the chips would shift down when it appeared). Before the first round settles `summary()` is
   * NO_TASKS, and with a source failed there is no all-clear: a partial read shows the count with the warning (or the
   * warning alone at zero), every source failed shows the warning alone.
   */
  protected readonly subtitle = computed<string>(() => {
    if (this.pending()) {
      return '讀取中…';
    }
    if (this.allFailed()) {
      return '部分資料無法讀取';
    }
    const { total, overdue } = this.center.summary();
    if (this.center.failedSources().length > 0) {
      // Partial read: the count the sidebar shows, then the warning; with nothing counted the warning alone.
      return total > 0 ? `${total} 件要處理 · 部分資料無法讀取` : '部分資料無法讀取';
    }
    if (total === 0) {
      // Nothing counted, but the inbox has items this page could not list: not an all-clear.
      return this.center.overflow() ? '還有對帳項目未列出' : '沒有要處理的事';
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
    !this.pending() && this.chip() !== 'portfolio' ? this.center.trackingRows() : [],
  );
  protected readonly trackingSum = computed(() =>
    this.trackingRows()
      .map((row) => `${TRACKING_UNIT[row.id].name} ${row.count} ${TRACKING_UNIT[row.id].unit}`)
      .join(' · '),
  );

  /**
   * loading: no round has settled and no task has arrived yet (the 讀取中… row itself shows until a round settles,
   * with or without tasks). empty: nothing to do and nothing unread (a failed or
   * truncated source is not "nothing"). list: everything else, including a failed source with no task (chips, 追蹤中,
   * links and the failure line only).
   */
  protected readonly state: Signal<'loading' | 'empty' | 'list'> = computed(() => {
    // With tasks: always the list, whatever round is in flight (see `pending`).
    if (this.center.tasks().length > 0) {
      return 'list';
    }
    if (this.pending()) {
      return 'loading';
    }
    return this.center.failedSources().length === 0 && !this.center.truncated() ? 'empty' : 'list';
  });

  /** 下一件: the nearest tracked bill, under the all-clear (ledger only, as 追蹤中). */
  protected readonly nextLine = computed<string | null>(() => {
    const next = this.chip() === 'portfolio' ? null : this.center.nextUpcoming();
    return next ? `下一件：${[next.title, next.when, next.amount].filter(Boolean).join(' · ')}` : null;
  });
  protected readonly trackingCount = computed(() => this.trackingRows().reduce((n, row) => n + row.count, 0));

  /**
   * The inbox's unlisted rest (R25): a plain note under the sections, never a row, so no section number, chip, subtitle
   * count or sidebar count includes it. A ledger thing, hidden under 投資. Null while no round has settled.
   */
  protected readonly overflow = computed(() => {
    const rest = !this.pending() && this.chip() !== 'portfolio' ? this.center.overflow() : null;
    return rest && !isCommand(rest.primary) ? { text: `${rest.title} · ${rest.when}（不計入數字）`, link: rest.primary } : null;
  });

  protected readonly failure = computed(() => failureLine(this.center.failedSources()));
  /** A 重試 of the reads is running: the line stays (same element) and the button waits. */
  protected readonly retrying = signal(false);

  constructor() {
    this.center.refresh({ reason: 'open' });

    // The all-clear leaves (a later round is in flight, or a task arrived): focus on its 看追蹤中 button would fall to
    // <body>, so it goes to the checked chip, as after a 重試.
    effect(() => {
      if (this.state() === 'empty') {
        return;
      }
      const active = document.activeElement;
      if (active && this.host.querySelector('.notif-empty')?.contains(active)) {
        this.host.querySelector<HTMLElement>('.notif-chips button[aria-checked="true"]')?.focus({ preventScroll: true });
      }
    });

    // A 重試 ends when its round has settled: success hands focus on, a repeat failure gives it back to 重試.
    effect(() => {
      if (!this.retrying() || this.center.loading()) {
        return;
      }
      this.retrying.set(false);
      const host = this.host;
      if (untracked(this.center.failedSources).length === 0) {
        focusPageIfLost(host, this.injector, '.notif-chips button[aria-checked="true"]');
      } else {
        focusRetryIfLost(host, this.injector, '.notif-failed .banner-action');
      }
    });

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
        // Still listed once its re-read settled: the row stays. Disabling the button made a browser drop focus to
        // <body>, so it goes back to the row's control if it had it when the command was sent.
        if (listed.has(id)) {
          this.answered.delete(id);
          if (this.focusedAtSend.delete(id)) {
            this.refocusRow(id);
          }
        } else {
          this.focusedAtSend.delete(id);
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

  protected retry(): void {
    if (this.retrying() || this.center.loading()) {
      return;
    }
    this.retrying.set(true);
    this.center.invalidate();
  }

  /** 看追蹤中: opens the folded block, scrolls it into view and focuses its summary. */
  protected openTracking(): void {
    const details = this.host.querySelector<HTMLDetailsElement>('details.notif-tracking');
    if (!details) {
      return;
    }
    details.open = true;
    const summary = details.querySelector<HTMLElement>('summary');
    summary?.scrollIntoView?.({ block: 'nearest' });
    summary?.focus({ preventScroll: true });
  }

  protected pick(chip: NotifChip): void {
    this.chip.set(chip);
  }

  protected chipName(option: ChipOption): string {
    return this.countsFinal() ? `${option.label}，${this.counts()[option.key]} 件` : option.label;
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
    if (this.rowControl(taskId) === document.activeElement) {
      this.focusedAtSend.add(taskId);
    } else {
      this.focusedAtSend.delete(taskId);
    }
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
            stop !== null
              ? this.toastStopped(taskId, command.name, posted.length, stop)
              : posted.length === 0
                ? // Nothing was pending (e.g. the interceptor's resend after a catch-up that had gone through).
                  this.toasts.done(`${command.name}：沒有待入帳的期數`)
                : this.toasts.done(`已入帳 ${posted.length} 期：${command.name}`),
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
   * resend): re-read now. The reads raise no toast, so the 失敗 toast stays the visible one. `already_posted` takes
   * this path too (re-read, release, focus) but reads as done.
   */
  private failed(taskId: string, command: NotifCommand, err: unknown): void {
    if (isAlreadyPosted(err)) {
      // The entry exists (a resend after a post that had gone through, #411): done, with the failure path's re-read.
      this.toasts.done(`${command.name}：此期已入帳`);
    } else if (isImportRunning(err)) {
      this.toasts.error(IMPORT_RUNNING_TOAST);
    } else {
      // The server's text is for the console only; the owner reads a fixed line (a 500's body is not for them).
      console.error(`入帳失敗 ${taskId}（${command.name}）`, err);
      const text = `${command.name} 入帳失敗，請再試一次`;
      this.toasts.error(text, {
        retry: guardedRetry(err, this.toasts, text, () => this.isBusy(taskId), () => this.send(taskId, command)),
      });
    }
    this.markAnswered(taskId);
    const after = this.center.round();
    this.center.invalidate();
    this.setBusy(taskId, after);
  }

  /** A 全部入帳 that posted some periods and then stopped: the count, never the server's reason (that goes to the console). */
  private toastStopped(taskId: string, name: string, posted: number, stop: { error: string }): void {
    console.error(`全部入帳中斷 ${taskId}（${name}）`, stop.error);
    this.toasts.error(`${name}：已入帳 ${posted} 期，之後的入帳失敗，請再試一次`);
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

  /** The native primary control of the row showing task `id`, if it is on screen. */
  private rowControl(id: string): HTMLElement | null {
    const index = this.visibleIds.indexOf(id);
    return index < 0 ? null : (this.host.querySelectorAll('app-notif-row')[index]?.querySelector<HTMLElement>(NOTIF_PRIMARY_CONTROL) ?? null);
  }

  /** After the render: a row that stayed takes back focus that fell to <body> (or nowhere); focus moved elsewhere stays. */
  private refocusRow(id: string): void {
    afterNextRender(
      () => {
        const active = document.activeElement;
        if (active && active !== document.body) {
          return;
        }
        const control = this.rowControl(id);
        if (control && !(control as HTMLButtonElement).disabled) {
          control.focus({ preventScroll: true });
        }
      },
      { injector: this.injector },
    );
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
