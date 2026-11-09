import { DOCUMENT, NgComponentOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  Type,
  afterNextRender,
  computed,
  effect,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { calendarParams, writeCalendarQuery } from '../timeline/calendar-url';
import { ActivatedRouteSnapshot, NavigationEnd, Params, Router, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';

import { BtnComponent } from '../../ui/btn/btn';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PaneStateService } from '../../../services/pane-state.service';
import { ACCOUNTING_PAGES, AccountingListKey } from '../accounting-pages';
import { AccountingToastComponent } from '../accounting-toast';
import { DirtyFormRegistry } from '../dirty-form.service';
import { MonthOverviewComponent } from '../month-overview/month-overview';
import { CalendarDayPanelComponent } from '../calendar-day-panel/calendar-day-panel';
import { CalendarPaneService } from '../calendar-month/calendar-pane.service';
import { addDays } from '../dates';
import type { InboxEinvoice } from '../inbox/einvoice.model';
import { groupCommands, listOpenState } from '../split-group/group-nav';
import type { TimelineRow } from '../timeline/timeline';
import { AccountingOverviewService } from '../month-overview/overview.service';
import { QUERY_DEBOUNCE_MS, focusables, isHandledKey, trapFocus, OVERLAY_ATTR } from '../accounting-ui';
import { AccountingShortcutsService, resolveShortcut } from '../keyboard-shortcuts';

export interface LayoutRouteState {
  /** `data.list` of the matched wide route: the page shown in the left pane. */
  list: AccountingListKey | null;
  /** True when a route is active in the `pane` outlet. */
  pane: boolean;
}

export function readLayoutState(root: ActivatedRouteSnapshot): LayoutRouteState {
  const state: LayoutRouteState = { list: null, pane: false };
  const visit = (node: ActivatedRouteSnapshot) => {
    const list = node.routeConfig?.data?.['list'] as AccountingListKey | undefined;
    if (list) {
      state.list = list;
    }
    if (node.outlet === 'pane') {
      state.pane = true;
    }
    node.children.forEach(visit);
  };
  visit(root);
  return state;
}

/** `/accounting/entries/:id…` or, opened from a passbook, `/accounting/accounts/:id/entries/:eid` (and `…/group`). */
const ENTRY_URL = /^\/accounting(?:\/accounts\/\d+)?\/entries\/(\d+)(?:\/|$)/;
/** The 多類別 group view of the split the routed member belongs to. */
const GROUP_URL = /^\/accounting(?:\/accounts\/\d+)?\/entries\/\d+\/group$/;
/** A passbook (`/accounting/accounts/:id`) or an entry opened from it: ↓ / ↑ stay inside that passbook. */
const REMINDERS_URL = /^\/accounting\/reminders(?:\/|$)/;
const PASSBOOK_URL = /^\/accounting\/accounts\/(\d+)(?:\/entries\/\d+(?:\/group)?)?$/;
const FORM_URL = /^\/accounting\/(?:entry|entries\/\d+\/edit)(?:[/?#]|$)/;
/** FAB `right` with a pane open: the list column's bottom-right corner (.hub-page side padding 1.75rem, .dbody gap
 * 16px, columns 5fr / 6fr, see accounting-layout.scss), so the button never covers the pane. */
export const FAB_RIGHT_BESIDE_PANE = 'calc(1.75rem + (100vw - var(--sidebar-w) - 3.5rem - 16px) * 6 / 11 + 32px)';
/** The same corner in calendar mode: the list column is 2fr of 3fr (`.dbody--calendar`). */
export const FAB_RIGHT_BESIDE_CALENDAR_PANE = 'calc(1.75rem + (100vw - var(--sidebar-w) - 3.5rem - 16px) / 3 + 32px)';
const SWIPE_CLOSE_PX = 80;
/** A swipe that drifts more than this vertically is a scroll, not a close. */
const SWIPE_MAX_DY = 40;
/** Pointers starting on a control never begin a swipe (✕ itself may). */
const SWIPE_IGNORE = 'input, textarea, select, button:not(.sheet-close), [contenteditable], .keypad';
/** A routed page's own ✕: the detail / group view 關閉, the entry form's data-pane-close. */
const PAGE_CLOSE = '.close[aria-label="關閉"], [data-pane-close]';


/**
 * Host of every `/accounting` route (design D22): one screen per route on phones; on wider screens the list
 * on the left (rendered here, so it survives row selection) and the `pane` outlet on the right — a sheet
 * sliding in from the right at 760–1023 px, a persistent pane at ≥ 1024 px. Publishes its pane state to
 * `PaneStateService` for the shell (sidebar auto-collapse, the 記一筆 button).
 */
@Component({
  selector: 'app-accounting-layout',
  standalone: true,
  imports: [
    RouterOutlet,
    NgComponentOutlet,
    AccountingToastComponent,
    MonthOverviewComponent,
    CalendarDayPanelComponent,
    PageHeaderComponent,
    BtnComponent,
  ],
  templateUrl: './accounting-layout.html',
  styleUrl: './accounting-layout.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(document:keydown.escape)': 'onEscape($event)',
    '(document:keydown)': 'onShortcutKey($event)',
    // 支出收入顏色: one class for every accounting page; pages colour `.neg` / `.pos` with `--tone-neg` / `--tone-pos`.
    '[class.green-red]': "colors() === 'green_red'",
  },
})
export class AccountingLayoutComponent {
  private readonly registry = inject(DirtyFormRegistry);
  private readonly router = inject(Router);
  private readonly layoutMode = inject(LayoutModeService);
  private readonly document = inject(DOCUMENT);
  private readonly injector = inject(Injector);
  private readonly shortcuts = inject(AccountingShortcutsService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly listPane = viewChild<ElementRef<HTMLElement>>('listPane');
  private readonly pageHeader = viewChild('pageHeader', { read: ElementRef<HTMLElement> });
  private readonly detailPane = viewChild<ElementRef<HTMLElement>>('detailPane');

  private readonly closeButton = viewChild<ElementRef<HTMLButtonElement>>('closeButton');
  private readonly accounting = inject(AccountingService);
  private readonly paneState = inject(PaneStateService);

  /** The list renders without its own page header: the layout hoists it above the card. */
  protected readonly outletInputs = { embedded: true };

  addAccount(): void {
    void this.router.navigateByUrl('/accounting/accounts/new');
  }

  /** `Preference.expense_income_colors`, read once and again after every preference save. */
  readonly colors = signal<'red_green' | 'green_red'>('red_green');

  readonly mode = this.layoutMode.mode;
  private readonly url = signal(this.router.url);
  private readonly routeState = signal<LayoutRouteState>(readLayoutState(this.router.routerState.snapshot.root));

  readonly listKey = computed(() => this.routeState().list);
  readonly paneOpen = computed(() => this.routeState().pane);
  protected readonly overview = inject(AccountingOverviewService);
  /** D7: nothing selected on the timeline at ≥ 1280 px → the month overview instead of the one-line empty state. */
  readonly showOverview = computed(
    () =>
      this.mode() === 'panes' &&
      this.layoutMode.wideSidebar() &&
      this.listKey() === 'timeline' &&
      !this.paneOpen() &&
      !this.overview.calendar(),
  );
  /** 日曆 on the timeline at ≥ 1024 px: the list pane takes 2fr, the day panel and drawer live in the right pane. */
  readonly calendarMode = computed(() => this.mode() === 'panes' && this.listKey() === 'timeline' && this.overview.calendar());
  /** What the timeline publishes for the calendar's right pane. */
  protected readonly pane = inject(CalendarPaneService);
  /** Calendar mode's right-pane base: 'summary' | 'day' | 'results' (day wins over results; results need a search text). */
  readonly calendarBase = computed<'summary' | 'day' | 'results' | null>(() =>
    !this.calendarMode() ? null : this.pane.selectedDay() ? 'day' : this.pane.search() ? 'results' : 'summary',
  );
  private readonly calSearch = viewChild<ElementRef<HTMLInputElement>>('calSearch');
  /** The right pane's base (本月摘要 / day panel / results): inert while the drawer covers it. */
  private readonly calBase = viewChild<ElementRef<HTMLElement>>('calBase');
  /** Host of the `pane` outlet: the drawer in calendar mode. */
  private readonly drawer = viewChild<ElementRef<HTMLElement>>('drawer');
  private searchTimer: ReturnType<typeof setTimeout> | null = null;
  readonly emptyCopy = computed(() =>
    this.listKey() === 'accounts' ? '選擇帳戶查看明細，或按 ＋ 新增' : '選擇一筆記錄查看明細，或按 ＋ 新增',
  );
  readonly listComponent = signal<Type<unknown> | null>(null);

  /** Entry shown in the pane (or full screen): lists highlight it. */
  readonly selectedEntryId = computed(() => {
    const match = ENTRY_URL.exec(this.url().split(/[?#]/)[0]);
    return match ? Number(match[1]) : null;
  });

  /** The routed path without query or fragment (lists read it to know whether they are the page on screen). */
  readonly currentPath = computed(() => this.url().split(/[?#]/)[0]);

  /** The routed page is a group view: a list marks every row of the routed member's split. */
  readonly groupViewOpen = computed(() => GROUP_URL.test(this.url().split(/[?#]/)[0]));

  /** 760–1023 px with a page in the pane: the right-hand sheet is a modal dialog. */
  readonly sheetOpen = computed(() => this.mode() === 'sheet' && this.paneOpen());
  /** The 760–1023 px sheet slid off-screen: not an empty 明細 region for assistive tech, and not focusable. */
  readonly sheetClosed = computed(() => this.mode() === 'sheet' && !this.paneOpen());

  /** The entry form (new / edit) is on screen; it carries its own ✕. */
  readonly formOpen = computed(() => FORM_URL.test(this.url()));

  /** 日曆 at ≥ 1024 px with a page in the pane: a modal drawer over the right pane only (the grid stays live). */
  readonly drawerOpen = computed(() => this.calendarMode() && this.paneOpen());
  /** The sheet or the drawer: either owes focus to its opener when it closes. */
  private readonly overlayOpen = computed(() => this.sheetOpen() || this.drawerOpen());

  private wasPhone: boolean | null = null;
  /** `selection`: the document's selected text at pointerdown, so only a selection made by this gesture blocks it. */
  private swipe: { x: number; y: number; selection: string } | null = null;

  /** Element focused before the sheet / drawer opened; focus returns there when it closes. */
  private focusBeforeOverlay: HTMLElement | null = null;
  /** The sheet / drawer was open (focus moved into it), so its closing owes focus somewhere other than `<body>`. */
  private overlayWasOpen = false;

  constructor() {
    effect(() => this.overview.visible.set(this.showOverview()));
    // The lifted search box follows the shared query, but never while it has focus (an IME composition or a trailing
    // space the trimmed query would drop survives); no [value] binding for the same reason.
    effect(() => {
      const text = this.pane.search();
      const input = this.calSearch()?.nativeElement;
      if (input && input !== this.document.activeElement && input.value !== text) {
        input.value = text;
      }
    });

    this.router.events
      .pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(event => {
        this.url.set(event.urlAfterRedirects);
        this.routeState.set(readLayoutState(this.router.routerState.snapshot.root));
      });

    effect(onCleanup => {
      const key = this.listKey();
      if (!key) {
        this.listComponent.set(null);
        return;
      }
      let active = true;
      onCleanup(() => (active = false));
      void ACCOUNTING_PAGES[key]().then(type => {
        if (active) {
          this.listComponent.set(type);
        }
      });
    });

    effect(() => {
      const phone = this.mode() === 'phone';
      untracked(() => {
        if (this.wasPhone !== null && this.wasPhone !== phone) {
          void this.router.navigateByUrl(this.router.url, { onSameUrlNavigation: 'reload' });
        }
        this.wasPhone = phone;
      });
    });

    // Initial read (no change seen yet) and a re-read after each preference save.
    effect(() => {
      this.accounting.preferenceChanged();
      untracked(() =>
        this.accounting
          .getPreference()
          .subscribe({ next: preference => this.colors.set(preference.expense_income_colors), error: () => undefined }),
      );
    });

    effect(() => {
      const open = this.overlayOpen();
      const sheet = this.sheetOpen();
      const drawer = this.drawerOpen();
      const list = this.listPane()?.nativeElement;
      const header = this.pageHeader()?.nativeElement as HTMLElement | undefined;
      const base = this.calBase()?.nativeElement;
      untracked(() => {
        // Remember the opener before anything turns inert (an inert focused element drops focus to <body>).
        if (open) {
          this.rememberOpener();
        }
        // The sheet inerts everything outside it; the drawer only the right pane's base, never the grid. Remove inert
        // before returning focus to an opener.
        this.setOutsideInert(sheet, list, header);
        this.setBaseInert(drawer, base);
        open ? this.focusOverlay() : this.restoreFocus();
      });
    });
    // Detail → form inside an open sheet / drawer removes the focused ✕: move focus to the form's own ✕ so it stays in
    // the dialog (the opener saved by rememberOpener is kept).
    effect(() => {
      if (this.overlayOpen() && this.formOpen()) {
        afterNextRender(
          {
            write: () => {
              const pane = this.detailPane()?.nativeElement;
              if (pane && !pane.contains(this.document.activeElement)) {
                pane.querySelector<HTMLElement>('[data-pane-close]')?.focus();
              }
            },
          },
          { injector: this.injector },
        );
      }
    });
    effect(() => {
      const beside = this.mode() === 'panes' && this.paneOpen();
      this.paneState.paneOpen.set(beside);
      this.paneState.fabRight.set(beside ? (this.calendarMode() ? FAB_RIGHT_BESIDE_CALENDAR_PANE : FAB_RIGHT_BESIDE_PANE) : null);
      this.paneState.formOpen.set(this.formOpen());
      this.paneState.sheetOpen.set(this.sheetOpen());
    });
    inject(DestroyRef).onDestroy(() => {
      this.clearSearchTimer();
      this.setOutsideInert(false);
      this.setBaseInert(false);
      this.overview.visible.set(false);
      this.paneState.paneOpen.set(false);
      this.paneState.formOpen.set(false);
      this.paneState.sheetOpen.set(false);
      this.paneState.fabRight.set(null);
    });
  }

  private inertList: HTMLElement | null = null;
  private inertHeader: HTMLElement | null = null;
  private inertSidebar: HTMLElement | null = null;

  /** Remember the actual nodes: view queries may be gone by destruction or a layout mode switch. */
  private setOutsideInert(inert: boolean, list?: HTMLElement, header?: HTMLElement): void {
    const sidebar = inert ? this.document.querySelector<HTMLElement>('app-sidebar') : null;
    if (!inert || this.inertList !== list) this.inertList?.removeAttribute('inert');
    if (!inert || this.inertHeader !== header) this.inertHeader?.removeAttribute('inert');
    if (!inert || this.inertSidebar !== sidebar) this.inertSidebar?.removeAttribute('inert');
    if (inert) {
      list?.setAttribute('inert', '');
      header?.setAttribute('inert', '');
      this.inertHeader = header ?? null;
      sidebar?.setAttribute('inert', '');
      this.inertList = list ?? null;
      this.inertSidebar = sidebar;
    } else {
      list?.removeAttribute('inert');
      header?.removeAttribute('inert');
      this.inertHeader = null;
      this.inertList = null;
      this.inertSidebar = null;
    }
  }

  private inertBase: HTMLElement | null = null;

  /** `inert` on the right pane's base while the drawer covers it (the node is remembered, as in setOutsideInert). */
  private setBaseInert(inert: boolean, base?: HTMLElement): void {
    if (this.inertBase && (!inert || this.inertBase !== base)) {
      this.inertBase.removeAttribute('inert');
    }
    this.inertBase = inert ? (base ?? null) : null;
    this.inertBase?.toggleAttribute('inert', true);
  }

  /**
   * Keys inside the drawer: Esc presses the page's own ✕ (`closeDrawer`; marked handled; an Esc already handled — the
   * entry form cancels itself — or an IME commit is left alone), Tab / Shift+Tab wrap inside it, ↑ ↓ step the entry
   * (`stepEntry`) unless a field has focus. The entry form keeps ↑ ↓ (stepping would drop its unsaved edits), and the
   * reminder centre leaves them to the document's walk over its own rows.
   */
  onDrawerKeydown(event: KeyboardEvent): void {
    if (!this.drawerOpen() || isHandledKey(event)) {
      return;
    }
    const drawer = this.drawer()?.nativeElement;
    if (event.key === 'Escape') {
      event.preventDefault();
      this.closeDrawer();
    } else if (event.key === 'Tab') {
      if (drawer && !(event.target as Element | null)?.closest?.(`[${OVERLAY_ATTR}]`)) {
        trapFocus(drawer, event);
      }
    } else if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && !this.onReminders() && !this.formOpen()) {
      const target = event.target as HTMLElement | null;
      if (event.ctrlKey || event.metaKey || event.altKey || target?.isContentEditable || target?.closest?.('input, textarea, select')) {
        return;
      }
      event.preventDefault();
      this.pane.commands()?.stepEntry(event.key === 'ArrowDown' ? 1 : -1);
    }
  }

  /** Tab / Shift+Tab wraps inside the open sheet; an overlay inside it (data-overlay) traps its own Tab. */
  onPaneKeydown(event: KeyboardEvent): void {
    if (!this.sheetOpen() || event.key !== 'Tab' || isHandledKey(event)) {
      return;
    }
    if ((event.target as Element | null)?.closest?.(`[${OVERLAY_ATTR}]`)) {
      return;
    }
    const pane = this.detailPane()?.nativeElement;
    if (pane) {
      trapFocus(pane, event);
    }
  }


  /** Close the pane / sheet (✕, backdrop, swipe, Esc): a dirty entry form first asks 放棄未儲存的內容？. */
  close(): void {
    this.registry.requestClose(() => this.navigateClose());
  }

  /**
   * Esc in the drawer: the page's own ✕, so Esc and ✕ make the same history move (`closeKeepingDay`; the form's ✕ asks
   * about unsaved edits itself). A page with no ✕ yet (still loading) closes through `close`.
   */
  private closeDrawer(): void {
    const pageClose = this.drawer()?.nativeElement.querySelector<HTMLElement>(PAGE_CLOSE);
    if (pageClose) {
      pageClose.click();
    } else {
      this.close();
    }
  }

  private calendarQuery(): Params {
    return calendarParams(this.router.routerState.snapshot.root.queryParamMap);
  }

  // 日曆 right pane

  /** 今天 or a 這個月要處理 due line: that day (a next-month due moves the month too). */
  onPickDay(date: string): void {
    void writeCalendarQuery(this.router, date.slice(0, 7), date);
  }

  /** ‹ › / `[` `]` in the day panel: the previous / next day, crossing month edges. */
  onStepDay(delta: 1 | -1): void {
    const day = this.pane.selectedDay();
    if (day === null) {
      return;
    }
    const next = addDays(day, delta);
    void writeCalendarQuery(this.router, next.slice(0, 7), next);
  }

  /** 本月摘要: back to the month's summary. */
  onClearDay(): void {
    void writeCalendarQuery(this.router, this.pane.month(), null);
  }

  /** A day-panel / results row: a split opens its group view (as `timeline.open`), any other row its detail. */
  onOpenRow(row: TimelineRow): void {
    const queryParams = this.calendarQuery();
    if (row.folder) {
      const state = listOpenState(this.router.url, '/accounting');
      void this.router.navigate(groupCommands(row.entryId, null), { ...(state ? { state } : {}), queryParams });
      return;
    }
    void this.router.navigate(['/accounting/entries', row.entryId], { queryParams });
  }

  /** ＋ 在這天記一筆: the entry form dated that day, keeping the calendar. */
  onAddEntry(date: string): void {
    void this.router.navigate(['/accounting/entry'], { queryParams: { date, ...this.calendarQuery() } });
  }

  /** One search shared with the filter bar: the timeline owns the query. */
  onSearch(text: string): void {
    this.pane.commands()?.setQuery(text);
  }

  /** The lifted box: 300 ms debounce (none during an IME composition), Enter commits at once, Esc empties it. */
  onSearchInput(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (event.type === 'keydown') {
      const key = (event as KeyboardEvent).key;
      if (isHandledKey(event as KeyboardEvent) || (key !== 'Enter' && key !== 'Escape')) {
        return;
      }
      if (key === 'Escape' && input.value === '') {
        return; // an empty field lets Esc through, like the filter bar's
      }
      event.preventDefault();
      this.clearSearchTimer();
      if (key === 'Escape') {
        input.value = '';
      }
      this.onSearch(input.value);
      return;
    }
    this.clearSearchTimer();
    if ((event as InputEvent).isComposing) {
      return;
    }
    const value = input.value;
    this.searchTimer = setTimeout(() => {
      this.searchTimer = null;
      this.onSearch(value);
    }, QUERY_DEBOUNCE_MS);
  }

  private clearSearchTimer(): void {
    if (this.searchTimer !== null) {
      clearTimeout(this.searchTimer);
      this.searchTimer = null;
    }
  }

  /** 重試 in the day panel: the timeline reads the day again. */
  onRetryDay(): void {
    this.pane.commands()?.retryDay();
  }

  /** 重試 in the month search results: the timeline reads them again. */
  onRetrySearch(): void {
    this.pane.commands()?.retrySearch();
  }

  /** 載入更多 in the month search results: the timeline reads the next page. */
  onLoadMore(): void {
    this.pane.commands()?.loadMore();
  }

  onOpenInvoice(invoice: InboxEinvoice): void {
    this.pane.commands()?.openInvoice(invoice);
  }

  /** A bill line: that card's passbook (the timeline's `openCard` target). */
  openCard(accountId: number): void {
    void this.router.navigate(['/accounting/accounts', accountId]);
  }

  /** Back to the list's own URL. */
  private navigateClose(): void {
    const passbook = PASSBOOK_URL.exec(this.url().split(/[?#]/)[0]);
    if (passbook && this.selectedEntryId() !== null) {
      void this.router.navigateByUrl(`/accounting/accounts/${passbook[1]}`);
      return;
    }
    if (this.listKey() === 'accounts') {
      void this.router.navigateByUrl('/accounting/accounts');
      return;
    }
    void this.router.navigate(['/accounting'], { queryParams: this.calendarQuery() });
  }


  /**
   * Escape closes the sheet; marked handled so global shortcuts skip it. An Escape already handled, or one that is part
   * of an IME composition (which child forms ignore), is left alone so an unsaved form is not discarded.
   */
  onEscape(event: Event): void {
    if (isHandledKey(event as KeyboardEvent)) return;
    if (!this.sheetOpen()) {
      return;
    }
    event.preventDefault();
    this.close();
  }

  /** Global accounting shortcuts. Acts (and marks the event handled) only when no other handler has. */
  onShortcutKey(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    const target = event.target as HTMLElement | null;
    const targetTag = target?.isContentEditable ? 'TEXTAREA' : (target?.tagName ?? null);
    const action = resolveShortcut(event, { url: this.router.url, targetTag: targetTag === 'BODY' ? null : targetTag });
    // ↓ / ↑ with no rows on screen stay the page's own keys (scrolling), as they do outside an open drawer: its own
    // ↑ ↓ step its entry (onDrawerKeydown). The reminder centre in the drawer keeps walking its own rows.
    if (!action || (action.type === 'move' && (this.rows().length === 0 || (this.drawerOpen() && !this.onReminders())))) {
      return;
    }
    event.preventDefault();
    if (action.type === 'navigate') {
      void this.router.navigate(action.commands, action.queryParams ? { queryParams: action.queryParams } : undefined);
    } else if (action.type === 'entry') {
      this.shortcuts.entryCommands.next(action.command);
    } else {
      this.moveSelection(action.delta);
    }
  }

  /**
   * Entry rows ↑ / ↓ walk. On the reminder centre only its own rows count (in the wide layout it sits in the pane host,
   * beside the timeline's rows on the left and, in 日曆, over the inert day panel of the right pane).
   */
  private rows(): HTMLElement[] {
    const host = this.host.nativeElement;
    const scope = this.onReminders() ? (host.querySelector<HTMLElement>('.pane-host') ?? host) : host;
    return Array.from(scope.querySelectorAll<HTMLElement>('[data-entry-id]'));
  }

  private onReminders(): boolean {
    return REMINDERS_URL.test(this.router.url.split(/[?#]/)[0]);
  }

  private moveSelection(delta: 1 | -1): void {
    const rows = this.rows();
    if (rows.length === 0) {
      return;
    }
    const path = this.router.url.split(/[?#]/)[0];
    const current = ENTRY_URL.exec(path)?.[1];
    // A group row stands for every member on the page (`data-member-ids`), as its highlight does.
    const index = rows.findIndex(
      row => row.dataset['entryId'] === current || (current !== undefined && (row.dataset['memberIds'] ?? '').split(' ').includes(current)),
    );
    const next = rows[index === -1 ? 0 : Math.min(rows.length - 1, Math.max(0, index + delta))];
    next.scrollIntoView?.({ block: 'nearest' });
    const id = Number(next.dataset['entryId']);
    const passbook = PASSBOOK_URL.exec(path);
    // A split row (`data-split`) opens its group view, as a click on it does.
    const tail = next.dataset['split'] !== undefined ? ['group'] : [];
    if (REMINDERS_URL.test(path)) {
      // Opened from the reminder centre: ✕ on the detail returns there.
      void this.router.navigate(['/accounting/entries', id, ...tail], { state: { closeTo: 'reminders' } });
      return;
    }
    void this.router.navigate(passbook ? ['/accounting/accounts', Number(passbook[1]), 'entries', id, ...tail] : ['/accounting/entries', id, ...tail], {
      queryParams: this.calendarQuery(),
    });
  }

  private rememberOpener(): void {
    const active = this.document.activeElement;
    if (!this.focusBeforeOverlay && active instanceof HTMLElement && active !== this.document.body) {
      this.focusBeforeOverlay = active;
    }
  }

  private focusOverlay(): void {
    this.overlayWasOpen = true;
    afterNextRender(
      {
        write: () => {
          const drawer = this.drawerOpen() ? this.drawer()?.nativeElement : undefined;
          if (drawer) {
            // The page's own ✕ (detail / group 關閉, the form's data-pane-close), else its first control, else the
            // dialog itself (a page still loading).
            (drawer.querySelector<HTMLElement>(PAGE_CLOSE) ?? focusables(drawer)[0] ?? drawer).focus();
            return;
          }
          // The grip's ✕, else the page's own ✕ (`data-pane-close`, the entry form's header).
          (
            this.closeButton()?.nativeElement ??
            this.detailPane()?.nativeElement.querySelector<HTMLElement>('[data-pane-close]')
          )?.focus();
        },
      },
      { injector: this.injector },
    );
  }

  private restoreFocus(): void {
    const previous = this.focusBeforeOverlay;
    const wasOpen = this.overlayWasOpen;
    this.focusBeforeOverlay = null;
    this.overlayWasOpen = false;
    if (previous?.isConnected) {
      previous.focus();
      return;
    }
    // No opener (a deep link): the now-inert sheet would leave focus on <body>. Fall back to the list's current row,
    // else the list pane itself, unless focus already moved somewhere live.
    const active = this.document.activeElement;
    if (!wasOpen || (active && active !== this.document.body && !this.detailPane()?.nativeElement.contains(active))) {
      return;
    }
    const list = this.listPane()?.nativeElement;
    // 日曆: the grid's selected day (a grid click under the drawer may have removed the day-panel opener).
    const cell = this.calendarMode() ? list?.querySelector<HTMLElement>('.cell.sel') : null;
    (cell ?? list?.querySelector<HTMLElement>('.sel, [aria-current]:not([aria-current="false"])') ?? list)?.focus();
  }

  /** Swipe-to-close starts only on the grip, only in the 760–1023 px sheet (spec §4.1). */
  onGripPointerDown(event: PointerEvent): void {
    const target = event.target as Element | null;
    if (!this.sheetOpen() || target?.closest(SWIPE_IGNORE)) {
      this.swipe = null;
      return;
    }
    this.swipe = { x: event.clientX, y: event.clientY, selection: this.selectedText() };
    const grip = event.currentTarget as HTMLElement | null;
    // Capture would retarget the button's following click to the grip, swallowing a plain tap on ✕.
    if (typeof event.pointerId === 'number' && !target?.closest('.sheet-close')) {
      grip?.setPointerCapture?.(event.pointerId);
    }
  }

  onGripPointerMove(event: PointerEvent): void {
    if (this.swipe && Math.abs(event.clientY - this.swipe.y) >= SWIPE_MAX_DY) {
      // Clearly vertical: a scroll, not a close.
      this.swipe = null;
    }
  }

  onGripPointerUp(event: PointerEvent): void {
    const swipe = this.swipe;
    this.swipe = null;
    if (!swipe || !this.sheetOpen()) {
      return;
    }
    // A mouse drag from the grip across the pinned header selects text: that is a selection, not a close. Only a
    // selection this gesture made inside the pane counts; an older one elsewhere never blocks a swipe.
    const text = this.selectedText();
    const anchor = this.document.getSelection()?.anchorNode ?? null;
    if (text && text !== swipe.selection && anchor && this.detailPane()?.nativeElement.contains(anchor)) {
      return;
    }
    const dx = event.clientX - swipe.x;
    const dy = event.clientY - swipe.y;
    if (dx > SWIPE_CLOSE_PX && Math.abs(dy) < SWIPE_MAX_DY) {
      this.close();
    }
  }

  private selectedText(): string {
    const selection = this.document.getSelection();
    return selection && !selection.isCollapsed ? selection.toString().trim() : '';
  }

  onGripPointerCancel(): void {
    this.swipe = null;
  }

}
