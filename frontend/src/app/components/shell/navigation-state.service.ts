import { Injectable, Signal, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs';

import { LayoutModeService } from '../../services/layout-mode.service';
import { NavBadgeService } from '../../services/nav-badge.service';
import { NavigationPreferencesService } from '../../services/navigation-preferences.service';
import { PaneStateService } from '../../services/pane-state.service';
import {
  NAV_GROUPS,
  NavBadge,
  NavGroup,
  NavGroupId,
  NavItem,
  SidebarDefault,
  TabBarLayout,
  buildTabBar,
  navItemForUrl,
  resolveSidebarMode,
} from './navigation';

const NO_BADGES: Readonly<Record<string, NavBadge>> = Object.freeze({});

/** What the sidebar, tab bar and page header show: the active route, the visible groups, badges and sidebar mode. */
@Injectable({ providedIn: 'root' })
export class NavigationStateService {
  private readonly router = inject(Router);
  private readonly prefs = inject(NavigationPreferencesService);
  private readonly layout = inject(LayoutModeService);
  private readonly paneState = inject(PaneStateService);
  private readonly navBadges = inject(NavBadgeService);

  private readonly url = signal(this.router.url || '/');
  /** The owner expanded the sidebar while a pane auto-collapsed it; lasts until the pane closes. */
  private readonly paneOverride = signal(false);

  readonly activeItem: Signal<NavItem> = computed(() => navItemForUrl(this.url()));
  readonly activeGroup: Signal<NavGroup> = computed(
    () => NAV_GROUPS.find(group => group.id === this.activeItem().group) ?? NAV_GROUPS[0],
  );
  readonly visibleGroups: Signal<readonly NavGroup[]> = computed(() => {
    const hidden = this.prefs.hiddenGroups();
    return NAV_GROUPS.filter(group => group.id === 'system' || !hidden.includes(group.id));
  });
  readonly tabBar: Signal<TabBarLayout> = computed(() => buildTabBar(NAV_GROUPS, this.prefs.hiddenGroups()));
  readonly badges: Signal<Readonly<Record<string, NavBadge>>> = computed(() =>
    this.prefs.showBadges() ? this.navBadges.badges() : NO_BADGES,
  );
  readonly sidebarMode: Signal<SidebarDefault> = computed(() =>
    resolveSidebarMode({
      wide: this.layout.wideSidebar(),
      stored: this.prefs.sidebarDefault(),
      autoCollapse: this.prefs.autoCollapse(),
      paneOpen: this.paneState.paneOpen(),
      paneOverride: this.paneOverride(),
    }),
  );
  readonly canToggleSidebar: Signal<boolean> = this.layout.wideSidebar;

  constructor() {
    this.router.events
      .pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(event => this.url.set(event.urlAfterRedirects));

    effect(() => {
      if (!this.paneState.paneOpen()) {
        this.paneOverride.set(false);
      }
    });
  }

  toggleSidebar(): void {
    if (!this.canToggleSidebar()) {
      return;
    }
    if (this.sidebarMode() === 'rail') {
      if (this.prefs.sidebarDefault() === 'rail') {
        this.prefs.setSidebarDefault('expanded');
      }
      if (this.paneState.paneOpen() && this.prefs.autoCollapse()) {
        this.paneOverride.set(true);
      }
      return;
    }
    // A collapse is always the owner's stored choice, also while a pane is open.
    this.prefs.setSidebarDefault('rail');
    this.paneOverride.set(false);
  }

  toggleGroup(id: NavGroupId): void {
    if (id === this.activeGroup().id) {
      return;
    }
    this.prefs.setFolded(id, !this.prefs.foldedGroups().includes(id));
  }

  isFolded(id: NavGroupId): boolean {
    return id !== this.activeGroup().id && this.prefs.foldedGroups().includes(id);
  }
}
