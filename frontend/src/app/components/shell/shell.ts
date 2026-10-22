import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { Router, RouterLink, RouterOutlet } from '@angular/router';

import { LayoutModeService } from '../../services/layout-mode.service';
import { NavBadgeService } from '../../services/nav-badge.service';
import { PaneStateService } from '../../services/pane-state.service';
import { isHandledKey } from '../accounting/accounting-ui';
import { DirtyFormRegistry } from '../accounting/dirty-form.service';
import { MobileNavComponent } from '../mobile-nav/mobile-nav';
import { SidebarComponent } from '../sidebar/sidebar';
import { resolveGlobalShortcut } from './global-shortcuts';
import { NavigationStateService } from './navigation-state.service';

const EXPANDED_WIDTH = '248px';
const RAIL_WIDTH = '72px';
/** Dialogs whose keys are their own; the accounting detail sheet opts in to app-wide keys (`data-global-shortcuts`). */
const BLOCKING_DIALOG = '[role="dialog"]:not([data-global-shortcuts]), [aria-modal="true"]:not([data-global-shortcuts])';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, SidebarComponent, MobileNavComponent],
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
  protected readonly layout = inject(LayoutModeService);
  protected readonly nav = inject(NavigationStateService);
  protected readonly paneState = inject(PaneStateService);

  protected readonly sidebarExpanded = computed(() => this.nav.sidebarMode() === 'expanded');
  protected readonly sidebarWidth = computed(() => (this.sidebarExpanded() ? EXPANDED_WIDTH : RAIL_WIDTH));
  /** 記一筆 from 760 px, except over the entry form and the 760–1023 px sheet (a modal dialog). */
  protected readonly showFab = computed(
    () => this.layout.mode() !== 'phone' && !this.paneState.formOpen() && !this.paneState.sheetOpen(),
  );

  constructor() {
    inject(NavBadgeService).start();
  }

  /** N / ⇧N on any page opens the entry form; a dirty form is asked first. */
  protected onGlobalKey(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    const target = event.target instanceof Element ? event.target : null;
    const targetTag = target instanceof HTMLElement && target.isContentEditable ? 'TEXTAREA' : (target?.tagName ?? null);
    const action = resolveGlobalShortcut(event, {
      url: this.router.url,
      targetTag: targetTag === 'BODY' ? null : targetTag,
      inDialog: !!target?.closest(BLOCKING_DIALOG),
    });
    if (!action) {
      return;
    }
    event.preventDefault();
    this.registry.requestClose(() => void this.router.navigate(action.commands));
  }
}
