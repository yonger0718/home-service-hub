import { DOCUMENT, LocationStrategy } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Params, Router, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';

import { LayoutModeService } from '../../services/layout-mode.service';
import { NavBadgeService } from '../../services/nav-badge.service';
import { PaneStateService } from '../../services/pane-state.service';
import { isHandledKey } from '../accounting/accounting-ui';
import { DirtyFormRegistry } from '../accounting/dirty-form.service';
import { calendarParams } from '../accounting/timeline/calendar-url';
import { MobileNavComponent } from '../mobile-nav/mobile-nav';
import { SidebarComponent } from '../sidebar/sidebar';
import { resolveGlobalShortcut } from './global-shortcuts';
import { HideOnScrollDirective } from './hide-on-scroll';
import { NavigationStateService } from './navigation-state.service';

const EXPANDED_WIDTH = '248px';
const RAIL_WIDTH = '72px';
/** Dialogs whose keys are their own; the accounting detail sheet opts in to app-wide keys (`data-global-shortcuts`). */
const BLOCKING_DIALOG = [
  '[role="dialog"]:not([data-global-shortcuts])',
  '[role="alertdialog"]:not([data-global-shortcuts])',
  '[aria-modal="true"]:not([data-global-shortcuts])',
].join(', ');
/**
 * An open modal anywhere in the page: a click on a PrimeNG p-dialog's plain content moves focus to <body>, outside the
 * dialog's subtree, and N must not tear the dialog down then either.
 */
const OPEN_MODAL = '[aria-modal="true"]:not([data-global-shortcuts]), [role="alertdialog"]:not([data-global-shortcuts])';
/** ARIA text widgets whose letters are input: p-select focuses a `<span role="combobox">`. */
const TYPING_ROLES = '[role="combobox"], [role="listbox"], [role="spinbutton"]';
const ENTRY_FORM: ['/accounting/entry'] = ['/accounting/entry'];

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, SidebarComponent, MobileNavComponent, HideOnScrollDirective],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(document:keydown)': 'onGlobalKey($event)',
  },
})
export class ShellComponent {
  private readonly router = inject(Router);
  private readonly registry = inject(DirtyFormRegistry);
  private readonly document = inject(DOCUMENT);
  protected readonly layout = inject(LayoutModeService);
  protected readonly nav = inject(NavigationStateService);
  protected readonly paneState = inject(PaneStateService);

  protected readonly sidebarExpanded = computed(() => this.nav.sidebarMode() === 'expanded');
  protected readonly sidebarWidth = computed(() => (this.sidebarExpanded() ? EXPANDED_WIDTH : RAIL_WIDTH));
  /** 記一筆 from 760 px, except over the entry form and the 760–1023 px sheet (a modal dialog). */
  protected readonly showFab = computed(
    () => this.layout.mode() !== 'phone' && !this.paneState.formOpen() && !this.paneState.sheetOpen(),
  );

  private readonly locationStrategy = inject(LocationStrategy);
  /** The current URL, refreshed on every NavigationEnd (calendar month / day writes included). */
  private readonly url = signal(this.router.url);

  /**
   * The 記一筆 link's href, built like RouterLink's (base href included) with the calendar month / day of the current
   * URL, so a modified click (new tab) keeps them too; plain clicks go through `openEntryForm`.
   */
  protected readonly entryFormHref = computed(() =>
    this.locationStrategy.prepareExternalUrl(
      this.router.serializeUrl(
        this.router.createUrlTree(ENTRY_FORM, { queryParams: calendarParams(this.router.parseUrl(this.url()).queryParamMap) }),
      ),
    ),
  );

  constructor() {
    inject(NavBadgeService).start();
    this.router.events
      .pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(event => this.url.set(event.urlAfterRedirects));
  }

  /** The calendar month / day of the page on screen (`{}` off the calendar), carried to the entry form. */
  private calendarQuery(): Params {
    return calendarParams(this.router.routerState.snapshot.root.queryParamMap);
  }

  /** N / ⇧N on any page opens the entry form (with the calendar month / day on the calendar); a dirty form is asked first. */
  protected onGlobalKey(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    const target = event.target instanceof Element ? event.target : null;
    const typing = target instanceof HTMLElement && (target.isContentEditable || !!target.closest(TYPING_ROLES));
    const targetTag = typing ? 'TEXTAREA' : (target?.tagName ?? null);
    const action = resolveGlobalShortcut(event, {
      url: this.router.url,
      targetTag: targetTag === 'BODY' ? null : targetTag,
      inDialog: !!target?.closest(BLOCKING_DIALOG) || !!this.document.querySelector(OPEN_MODAL),
    });
    if (!action) {
      return;
    }
    event.preventDefault();
    this.registry.requestClose(() => void this.router.navigate(action.commands, { queryParams: this.calendarQuery() }));
  }

  /**
   * 記一筆: the same dirty-form check and calendar month / day as N. A modified click (new tab / window) is left to the
   * browser.
   */
  protected openEntryForm(event: MouseEvent): void {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    this.registry.requestClose(() => void this.router.navigate(ENTRY_FORM, { queryParams: this.calendarQuery() }));
  }
}
