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
import { DOCUMENT } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, ParamMap, Router, RouterLink } from '@angular/router';
import { combineLatest, map } from 'rxjs';

import { AccountingService } from '../../services/accounting.service';
import { LayoutModeService } from '../../services/layout-mode.service';
import { NotificationCenterService } from '../../services/notification-center.service';
import { PortfolioService } from '../../services/portfolio.service';
import { injectToasts, toastText } from '../../services/toast.service';
import { guardedRetry } from '../../services/toast-retry';
import { conflictReason, knownWriteReason } from '../accounting/http-errors';
import { IMPORT_RUNNING_TOAST, isImportRunning } from '../accounting/schedule-math';
import { apiErrorText, splitApiCode } from '../accounting/statements/statement-view';
import { focusPageIfLost, focusRetryIfLost } from '../accounting/retry-focus';
import { BannerComponent } from '../ui/banner/banner';
import { PAGE_SCROLLER_SELECTOR } from '../shell/fab-visibility';
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
  failureLine,
  isCommand,
} from './notif-task';
import { NOTIF_PRIMARY_CONTROL, NotifRowComponent } from './notif-row/notif-row';
import { EinvoiceFlowComponent } from './einvoice-flow/einvoice-flow';
import { EinvoiceUnmatchedComponent } from './einvoice-unmatched/einvoice-unmatched';
import { ReconFlowComponent } from './recon-flow/recon-flow';
import { EINVOICE_FLOW_SEGMENT } from './review-flow-url';
import { SnoozeOption, untilText } from './snooze';

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

/** The 已處理 of a 國泰 statement date or of a 永豐期貨 incident (records it as known; imports nothing). */
type AckCommand = Extract<NotifCommand, { kind: 'ackCathay' | 'ackFutures' }>;

/** What the tracking summary counts, per row. */
const TRACKING_UNIT: Record<NotifTrackingRow['id'], { name: string; unit: string }> = {
  debts: { name: '借還款', unit: '位' },
  upcoming: { name: '即將到來', unit: '件' },
  einvoices: { name: '發票', unit: '張' },
  dividends: { name: '股利', unit: '筆' },
};

/** The route's `statementId` as a positive integer; null when absent, zero, negative or not a number. */
function statementIdOf(raw: string | null): number | null {
  const id = Number(raw);
  return raw !== null && Number.isSafeInteger(id) && id > 0 ? id : null;
}

/** The flow open beside (or instead of) the list: a statement's review flow or the e-invoice flow. */
export type NotifFlow = { kind: 'recon'; id: number } | { kind: 'einvoice' } | { kind: 'unmatched' };

/** The flow's task id, which is also the id of its row (`recon:<id>`, `einvoice`; `einvoices` is the 追蹤中 row's). */
function flowTaskId(flow: NotifFlow | null): string | null {
  if (flow === null) return null;
  return flow.kind === 'recon' ? `recon:${flow.id}` : flow.kind === 'unmatched' ? 'einvoices' : 'einvoice';
}

function flowOf([params, query]: [ParamMap, ParamMap]): NotifFlow | null {
  if (params.get('flow') === EINVOICE_FLOW_SEGMENT) {
    return query.get('state') === 'unmatched' ? { kind: 'unmatched' } : { kind: 'einvoice' };
  }
  const id = statementIdOf(params.get('statementId'));
  return id === null ? null : { kind: 'recon', id };
}

/**
 * `/notifications`: one column (R20). `/notifications/recon/:statementId` adds the review flow (D5), `/notifications/einvoices` the e-invoice flow (D10): beside the list
 * from 1024 px (the row of that statement selected), alone below (the flow draws its own card and header). Header with the count, source chips (a radiogroup, as 提醒中心's tabs), the
 * counted tasks by tier, the folded 追蹤中 (never counted) and the links out. 入帳 / 全部入帳 / 已處理 run in place (R4). Before the first round
 * settles a 讀取中… row ends the page (the only one, whether or not tasks have arrived); then the empty state or the
 * failure line.
 */
@Component({
  selector: 'app-notifications',
  imports: [BannerComponent, EinvoiceFlowComponent, EinvoiceUnmatchedComponent, NotifRowComponent, PageHeaderComponent, ReconFlowComponent, RouterLink],
  templateUrl: './notifications.html',
  styleUrl: './notifications.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NotificationsComponent {
  protected readonly center = inject(NotificationCenterService);
  private readonly layout = inject(LayoutModeService);
  private readonly accounting = inject(AccountingService);
  private readonly portfolio = inject(PortfolioService);
  private readonly toasts = injectToasts();
  private readonly host: HTMLElement = inject(ElementRef).nativeElement;
  private readonly injector = inject(Injector);
  private readonly router = inject(Router);
  private readonly document = inject(DOCUMENT);

  private readonly route = inject(ActivatedRoute);
  private readonly routeFlow = toSignal(combineLatest([this.route.paramMap, this.route.queryParamMap]).pipe(map(flowOf)), {
    initialValue: null,
  });
  /** The open flow (the route's `statementId` or `flow`), or null on `/notifications`; equal while its task id is. */
  protected readonly flow = computed(() => this.routeFlow(), { equal: (a, b) => flowTaskId(a) === flowTaskId(b) });
  protected readonly panes = computed(() => this.layout.mode() === 'panes');
  /** Below 1024 px with a flow: it renders alone, full screen (no header, chips, list or links). */
  protected readonly flowAlone = computed(() => (this.panes() ? null : this.flow()));
  /** The task id of the row the open flow belongs to (`recon:<id>`, `einvoice`), or null. */
  protected readonly selectedTaskId = computed(() => flowTaskId(this.flow()));

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
      (this.center.loading() && this.center.tasks().length === 0 && this.center.failedSources().length === 0 && !this.unlisted()),
  );

  /** Some source left work out of its answer: the inbox's ask items, or 國泰 dates beyond those listed (C5). */
  private readonly unlisted = computed(
    () => this.center.truncated() || this.center.cathayMore() > 0 || this.center.futuresMore() > 0,
  );

  /**
   * A source failed and nothing is counted: there is no number to stand behind (a zero would read as "nothing to do"
   * while part of the data is unread), so chips, section headings and subtitle show labels only. With tasks counted the
   * numbers are the facade's (lower bounds the banner qualifies), the same the sidebar and the bell show.
   */
  private readonly unreadAndEmpty = computed(
    () => this.center.failedSources().length > 0 && this.center.summary().total === 0,
  );

  /** The counts are shown: a round settled and not (a source failed with nothing counted). */
  protected readonly countsFinal = computed(() => !this.pending() && !this.unreadAndEmpty());

  /**
   * Never absent (the chips would shift down when it appeared). Before the first round settles `summary()` is
   * NO_TASKS, and with a source failed there is no all-clear: a partial read shows the count with the warning (or the
   * warning alone when nothing is counted).
   */
  protected readonly subtitle = computed<string>(() => {
    if (this.pending()) {
      return '讀取中…';
    }
    const { total, overdue } = this.center.summary();
    if (this.center.failedSources().length > 0) {
      // Partial read: the warning first (a phone header keeps the subtitle on one line and cuts its end), then the
      // count the sidebar shows and the overdue part; with nothing counted the warning alone.
      const parts = ['部分資料無法讀取'];
      if (total > 0) {
        parts.push(`${total} 件要處理`);
        if (overdue > 0) {
          parts.push(`${overdue} 件已逾期`);
        }
      }
      return parts.join(' · ');
    }
    if (total === 0) {
      // Nothing counted, but the inbox has items this page could not list: not an all-clear.
      if (this.center.overflow()) {
        return '還有對帳項目未列出';
      }
      const snoozed = this.center.snoozedCount();
      return snoozed > 0 ? `沒有要處理的事 · ${snoozed} 件稍後提醒` : '沒有要處理的事';
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

  /** 追蹤中: every row under 全部, the rows of that source under 財務 / 投資 (P5); none while pending. */
  protected readonly trackingRows = computed(() => {
    if (this.pending()) {
      return [];
    }
    const chip = this.chip();
    return this.center.trackingRows().filter(row => chip === 'all' || row.source === chip);
  });
  /** The 追蹤中 summary, one part per row (「發票 7 張」); each part wraps as a whole on phones. */
  protected readonly trackingSum = computed(() =>
    this.trackingRows().map((row) => `${TRACKING_UNIT[row.id].name} ${row.count} ${TRACKING_UNIT[row.id].unit}`),
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
    // 都處理好了 never stands beside a note saying something was not listed.
    return this.center.failedSources().length === 0 && !this.unlisted() ? 'empty' : 'list';
  });

  /** 下一件: the nearest tracked bill, under the all-clear; ledger only, so absent under 投資 (P5). */
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
    return rest && !isCommand(rest.primary) ? { text: rest.title, link: rest.primary } : null;
  });

  /**
   * 國泰 pending dates the API did not list (C5): a second plain note after the ledger one, never counted. A 投資
   * thing, hidden under 財務; 0 (no note) while no round has settled.
   */
  protected readonly cathayMore = computed(() =>
    !this.pending() && this.chip() !== 'ledger' ? Math.max(0, this.center.cathayMore()) : 0,
  );

  /** 永豐期貨 items the API did not list: a third plain note, as the 國泰 one. */
  protected readonly futuresMore = computed(() =>
    !this.pending() && this.chip() !== 'ledger' ? Math.max(0, this.center.futuresMore()) : 0,
  );

  protected readonly failure = computed(() => failureLine(this.center.failedSources()));
  /** A 重試 of the reads is running: the line stays (same element) and the button waits. */
  protected readonly retrying = signal(false);

  constructor() {
    this.center.refresh({ reason: 'open' });

    // The flow opens (or changes statement): focus goes to its host once it is rendered. It closes: focus goes to the
    // row of that statement (the list is back below 1024 px), else the page heading.
    let shown: string | null = null;
    effect(() => {
      const id = this.selectedTaskId();
      const previous = shown;
      shown = id;
      if (id === previous) {
        return;
      }
      afterNextRender(
        () => (id !== null ? this.focusFlow() : this.focusAfterClose(previous!)),
        { injector: this.injector },
      );
    });

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

  /**
   * The flow's ✕, ‹ or Escape: back to the list (the flow itself does no navigation). The flow's history entry is
   * replaced, so browser Back after closing does not reopen it.
   */
  protected closeFlow(): void {
    void this.router.navigate(['/notifications'], { replaceUrl: true });
  }

  /**
   * The flow opened (or changed statement). Alone (below 1024 px) it is a new screen: the page scrolls to its top, as a
   * route change would (the router restores no scroll here, and the list may have been scrolled down). The scroller is
   * the document, or `.hub-main` when the shell scrolls it (compact-height panes); both are reset. Then its host
   * takes focus.
   */
  private focusFlow(): void {
    if (this.flowAlone() !== null) {
      const scrollers = [this.document.scrollingElement ?? this.document.documentElement, this.host.closest(PAGE_SCROLLER_SELECTOR)];
      for (const scroller of scrollers) {
        scroller?.scrollTo?.({ top: 0 });
      }
    }
    this.host.querySelector<HTMLElement>('app-recon-flow, app-einvoice-flow')?.focus({ preventScroll: true });
  }

  /**
   * The flow closed: the row of that statement takes focus, else the page heading, scrolled into view (below 1024 px
   * the list was just rendered again at the top of a page that may be scrolled).
   */
  private focusAfterClose(taskId: string): void {
    const row = this.host.querySelector(`app-notif-row[data-task-id="${taskId}"]`);
    const target = row?.querySelector<HTMLElement>(NOTIF_PRIMARY_CONTROL) ?? this.focusableHeading();
    if (!target) {
      return;
    }
    target.scrollIntoView?.({ block: 'nearest' });
    target.focus({ preventScroll: true });
  }

  /** The page h1, focusable for this one hand-off: its tabindex leaves with the focus. */
  private focusableHeading(): HTMLElement | null {
    const heading = this.host.querySelector<HTMLElement>('h1');
    if (heading && !heading.hasAttribute('tabindex')) {
      heading.setAttribute('tabindex', '-1');
      heading.addEventListener('blur', () => heading.removeAttribute('tabindex'), { once: true });
    }
    return heading;
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

  /** A row's 入帳 / 全部入帳 / 已處理; ignored while that row is busy (one write per press). */
  /**
   * 稍後提醒 (D4, PR E2): the task leaves the list on this device; the toast names the end time and 復原 brings it back.
   * Focus follows the row as after a command (the next row's primary, else the tracking summary, else the chips).
   */
  protected onSnooze(task: NotifTask, option: SnoozeOption): void {
    this.answered.add(task.id);
    const until = this.center.snooze(task, option);
    this.toasts.done(`已延後到 ${untilText(until)}：${task.title}`, { undo: () => this.center.unsnooze(task.id) });
  }

  /** The empty card's 「N 件稍後提醒，最早 MM/DD HH:mm 回來」 time, or null with nothing snoozed. */
  protected readonly snoozedEarliestText = computed(() => {
    const earliest = this.center.snoozedEarliest();
    return earliest ? untilText(earliest) : null;
  });

  /**
   * 現在顯示 cancels every snooze (owner design review 2026-11-26): the tasks come back, the toast 「已取消 N 件延後」 has
   * 復原 (the cancelled snoozes return); focus goes to the first row.
   */
  protected showSnoozed(): void {
    const n = this.center.snoozedCount();
    const saved = this.center.showSnoozed();
    this.toasts.done(`已取消 ${n} 件延後`, { undo: () => this.center.restoreSnoozes(saved) });
    this.restoreFocus(0);
  }

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
    } else if (command.kind === 'catchUp') {
      this.accounting.catchUpSchedule(command.definitionId).subscribe({
        next: ({ posted, failed: stop }) =>
          this.succeeded(taskId, () =>
            stop !== null
              ? this.toastStopped(taskId, command.name, posted.length, stop)
              : posted.length === 0
                ? // Nothing was pending (e.g. 重試 after a catch-up that had gone through).
                  this.toasts.done(`${command.name}：沒有待入帳的期數`)
                : this.toasts.done(`已入帳 ${posted.length} 期：${command.name}`),
          ),
        error: failed,
      });
    } else if (command.kind === 'ackCathay') {
      this.portfolio.ackCathayDay(command.tradeDate).subscribe({
        next: () => {
          this.toasts.done(`已處理：${command.name}`);
          // An ack bumps no change signal: the page re-reads itself, or the date would stay listed (spec 3.4).
          this.reread(taskId);
        },
        error: (err: unknown) => this.ackFailed(taskId, command, err),
      });
    } else if (command.kind === 'ackFutures') {
      // 204 with no body; as 國泰, an ack bumps no change signal: the page re-reads itself.
      this.portfolio.ackFuturesItem(command.key, command.status).subscribe({
        next: () => {
          this.toasts.done(`已處理：${command.name}`);
          this.reread(taskId);
        },
        error: (err: unknown) => this.ackFailed(taskId, command, err),
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
   * An error bumps nothing, and the write may still have committed (a 5xx after the commit): re-read
   * now. The reads raise no toast, so the 失敗 toast stays the visible one. `already_posted` takes
   * this path too (re-read, release, focus) but reads as done.
   */
  private failed(taskId: string, command: NotifCommand, err: unknown): void {
    if (isAlreadyPosted(err)) {
      // The entry exists (重試 after a post that had gone through, or posted elsewhere): done, with the failure path's re-read.
      this.toasts.done(`${command.name}：此期已入帳`);
    } else if (isImportRunning(err)) {
      this.toasts.error(IMPORT_RUNNING_TOAST);
    } else {
      // Only a reason the app words itself reaches the owner; any other server text is for the console (a 500's body).
      console.error(`入帳失敗 ${taskId}（${command.name}）`, err);
      const reason = knownWriteReason(err);
      const text = reason === null ? `${command.name} 入帳失敗` : toastText(`${command.name} 入帳失敗`, reason);
      this.toasts.error(text, {
        retry: guardedRetry(err, this.toasts, text, () => this.isBusy(taskId), () => this.send(taskId, command)),
      });
    }
    this.reread(taskId);
  }

  /**
   * A failed 已處理 (C6). 404: the date no longer needs the owner (acked elsewhere, or by an earlier send that had
   * committed): done. Else 失敗, a reason only when the app words it, 重試 only for a network failure; the raw error
   * goes to the console. No 復原 either way (there is no un-ack). The ack may have committed: re-read.
   */
  private ackFailed(taskId: string, command: AckCommand, err: unknown): void {
    if (err instanceof HttpErrorResponse && err.status === 404) {
      this.toasts.done(`${command.name}：已經不需要處理`);
    } else {
      console.error(`標記失敗 ${taskId}（${command.name}）`, err);
      const reason = knownWriteReason(err);
      const text = reason === null ? `${command.name} 標記失敗` : toastText(`${command.name} 標記失敗`, reason);
      this.toasts.error(text, {
        retry: guardedRetry(err, this.toasts, text, () => this.isBusy(taskId), () => this.send(taskId, command)),
      });
    }
    this.reread(taskId);
  }

  /**
   * The answer bumped no change signal: start the re-read now. The row waits for a round above the one current
   * before this `invalidate()` (the release rule), i.e. for the re-read itself to settle.
   */
  private reread(taskId: string): void {
    this.markAnswered(taskId);
    const after = this.center.round();
    this.center.invalidate();
    this.setBusy(taskId, after);
  }

  /** A 全部入帳 that posted some periods and then stopped: the count, and the reason only when it is a conflict code the app words. */
  private toastStopped(taskId: string, name: string, posted: number, stop: { error: string }): void {
    console.error(`全部入帳中斷 ${taskId}（${name}）`, stop.error);
    const reason = conflictReason(stop.error);
    this.toasts.error(
      reason === null
        ? `${name}：已入帳 ${posted} 期，之後的入帳失敗`
        : `${name}：已入帳 ${posted} 期，之後的入帳失敗（${reason}）`,
    );
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
