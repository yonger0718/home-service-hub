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
import { ActivatedRouteSnapshot, NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';

import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { ACCOUNTING_PAGES, AccountingListKey } from '../accounting-pages';
import { AccountingToastComponent } from '../accounting-toast';
import { isHandledKey, trapFocus, OVERLAY_ATTR } from '../accounting-ui';
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

/** `/accounting/entries/:id…` or, opened from a passbook, `/accounting/accounts/:id/entries/:eid`. */
const ENTRY_URL = /^\/accounting(?:\/accounts\/\d+)?\/entries\/(\d+)(?:\/|$)/;
/** A passbook (`/accounting/accounts/:id`) or an entry opened from it: ↓ / ↑ stay inside that passbook. */
const REMINDERS_URL = /^\/accounting\/reminders(?:\/|$)/;
const PASSBOOK_URL = /^\/accounting\/accounts\/(\d+)(?:\/entries\/\d+)?$/;
const FORM_URL = /^\/accounting\/(?:entry|entries\/\d+\/edit)(?:[/?#]|$)/;
const SWIPE_CLOSE_PX = 80;
/** A swipe that drifts more than this vertically is a scroll, not a close. */
const SWIPE_MAX_DY = 40;
/** Pointers starting on a control never begin a swipe (✕ itself may). */
const SWIPE_IGNORE = 'input, textarea, select, button:not(.sheet-close), [contenteditable], .keypad';


/**
 * Host of every `/accounting` route (design D22): one screen per route on phones; on wider screens the list
 * on the left (rendered here, so it survives row selection) and the `pane` outlet on the right — a sheet
 * sliding in from the right at 760–1023 px, a persistent pane at ≥ 1024 px. Shows the floating "+" from 760 px.
 */
@Component({
  selector: 'app-accounting-layout',
  standalone: true,
  imports: [RouterOutlet, RouterLink, NgComponentOutlet, AccountingToastComponent],
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
  private readonly router = inject(Router);
  private readonly layoutMode = inject(LayoutModeService);
  private readonly document = inject(DOCUMENT);
  private readonly injector = inject(Injector);
  private readonly shortcuts = inject(AccountingShortcutsService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly listPane = viewChild<ElementRef<HTMLElement>>('listPane');
  private readonly detailPane = viewChild<ElementRef<HTMLElement>>('detailPane');

  private readonly closeButton = viewChild<ElementRef<HTMLButtonElement>>('closeButton');
  private readonly accounting = inject(AccountingService);

  /** `Preference.expense_income_colors`, read once and again after every preference save. */
  readonly colors = signal<'red_green' | 'green_red'>('red_green');

  readonly mode = this.layoutMode.mode;
  private readonly url = signal(this.router.url);
  private readonly routeState = signal<LayoutRouteState>(readLayoutState(this.router.routerState.snapshot.root));

  readonly listKey = computed(() => this.routeState().list);
  readonly paneOpen = computed(() => this.routeState().pane);
  readonly listComponent = signal<Type<unknown> | null>(null);

  /** Entry shown in the pane (or full screen): lists highlight it. */
  readonly selectedEntryId = computed(() => {
    const match = ENTRY_URL.exec(this.url().split(/[?#]/)[0]);
    return match ? Number(match[1]) : null;
  });

  /** 760–1023 px with a page in the pane: the right-hand sheet is a modal dialog. */
  readonly sheetOpen = computed(() => this.mode() === 'sheet' && this.paneOpen());

  readonly showFab = computed(() => this.mode() !== 'phone' && !FORM_URL.test(this.url()));

  private wasPhone: boolean | null = null;
  private swipe: { x: number; y: number } | null = null;

  /** Element focused before the sheet opened; focus returns there when it closes. */
  private focusBeforeSheet: HTMLElement | null = null;

  constructor() {
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
      const open = this.sheetOpen();
      const list = this.listPane()?.nativeElement;
      untracked(() => {
        // Remove inert before returning focus to a list/dock opener.
        this.setOutsideInert(open, list);
        open ? this.focusSheet() : this.restoreFocus();
      });
    });
    inject(DestroyRef).onDestroy(() => this.setOutsideInert(false));
  }

  private inertList: HTMLElement | null = null;
  private inertDock: HTMLElement | null = null;

  /** Remember the actual nodes: view queries may be gone by destruction or a layout mode switch. */
  private setOutsideInert(inert: boolean, list?: HTMLElement): void {
    const dock = inert ? this.document.querySelector<HTMLElement>('app-dock') : null;
    if (!inert || this.inertList !== list) this.inertList?.removeAttribute('inert');
    if (!inert || this.inertDock !== dock) this.inertDock?.removeAttribute('inert');
    if (inert) {
      list?.setAttribute('inert', '');
      dock?.setAttribute('inert', '');
      this.inertList = list ?? null;
      this.inertDock = dock;
    } else {
      list?.removeAttribute('inert');
      this.inertList = null;
      this.inertDock = null;
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


  /** Close the pane / sheet: back to the list's own URL. */
  close(): void {
    const passbook = PASSBOOK_URL.exec(this.url().split(/[?#]/)[0]);
    if (passbook && this.selectedEntryId() !== null) {
      void this.router.navigateByUrl(`/accounting/accounts/${passbook[1]}`);
      return;
    }
    void this.router.navigateByUrl(this.listKey() === 'accounts' ? '/accounting/accounts' : '/accounting');
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
    // ↓ / ↑ with no rows on screen stay the page's own keys (scrolling).
    if (!action || (action.type === 'move' && this.rows().length === 0)) {
      return;
    }
    event.preventDefault();
    if (action.type === 'navigate') {
      void this.router.navigate(action.commands);
    } else if (action.type === 'entry') {
      this.shortcuts.entryCommands.next(action.command);
    } else {
      this.moveSelection(action.delta);
    }
  }

  /**
   * Entry rows ↑ / ↓ walk. On the reminder centre only its own rows count (in the wide layout it sits in the pane,
   * beside the timeline's rows on the left).
   */
  private rows(): HTMLElement[] {
    const host = this.host.nativeElement;
    const scope = this.onReminders() ? (host.querySelector<HTMLElement>('.detail-pane') ?? host) : host;
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
    const index = rows.findIndex(row => row.dataset['entryId'] === current);
    const next = rows[index === -1 ? 0 : Math.min(rows.length - 1, Math.max(0, index + delta))];
    next.scrollIntoView?.({ block: 'nearest' });
    const id = Number(next.dataset['entryId']);
    const passbook = PASSBOOK_URL.exec(path);
    if (REMINDERS_URL.test(path)) {
      // Opened from the reminder centre: ✕ on the detail returns there.
      void this.router.navigate(['/accounting/entries', id], { state: { closeTo: 'reminders' } });
      return;
    }
    void this.router.navigate(passbook ? ['/accounting/accounts', Number(passbook[1]), 'entries', id] : ['/accounting/entries', id]);
  }

  private focusSheet(): void {
    const active = this.document.activeElement;
    if (!this.focusBeforeSheet && active instanceof HTMLElement && active !== this.document.body) {
      this.focusBeforeSheet = active;
    }
    afterNextRender({ write: () => this.closeButton()?.nativeElement.focus() }, { injector: this.injector });
  }

  private restoreFocus(): void {
    const previous = this.focusBeforeSheet;
    this.focusBeforeSheet = null;
    if (previous?.isConnected) {
      previous.focus();
    }
  }

  /** Swipe-to-close starts only on the grip, only in the 760–1023 px sheet (spec §4.1). */
  onGripPointerDown(event: PointerEvent): void {
    const target = event.target as Element | null;
    if (!this.sheetOpen() || target?.closest(SWIPE_IGNORE)) {
      this.swipe = null;
      return;
    }
    this.swipe = { x: event.clientX, y: event.clientY };
    const grip = event.currentTarget as HTMLElement | null;
    if (typeof event.pointerId === 'number') {
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
    const dx = event.clientX - swipe.x;
    const dy = event.clientY - swipe.y;
    if (dx > SWIPE_CLOSE_PX && Math.abs(dy) < SWIPE_MAX_DY) {
      this.close();
    }
  }

  onGripPointerCancel(): void {
    this.swipe = null;
  }

}
