import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { MATCH_MEDIA, PANES_QUERY, PHONE_QUERY, SIDEBAR_WIDE_QUERY } from '../../services/layout-mode.service';
import { NavBadgeService } from '../../services/nav-badge.service';
import { NavigationPreferencesService } from '../../services/navigation-preferences.service';
import { PaneStateService } from '../../services/pane-state.service';
import { NavBadge } from './navigation';
import { NavigationStateService } from './navigation-state.service';

@Component({ template: '' })
class BlankPage {}

const BADGES: Record<string, NavBadge> = {
  'accounting-timeline': { count: 2, label: '提醒中心 2 項' },
  import: { dot: true, label: '最近一次匯入失敗' },
};

function fakeMatchMedia(width: number) {
  return (query: string) =>
    ({
      media: query,
      matches:
        query === PHONE_QUERY
          ? width < 760
          : query === PANES_QUERY
            ? width >= 1024
            : query === SIDEBAR_WIDE_QUERY
              ? width >= 1280
              : false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList;
}

function configure(width = 1440) {
  TestBed.configureTestingModule({
    providers: [
      provideRouter([{ path: '**', component: BlankPage }]),
      { provide: MATCH_MEDIA, useValue: fakeMatchMedia(width) },
      { provide: NavBadgeService, useValue: { badges: signal(BADGES) } },
    ],
  });
  return {
    prefs: TestBed.inject(NavigationPreferencesService),
    pane: TestBed.inject(PaneStateService),
    router: TestBed.inject(Router),
  };
}

async function create(width = 1440, url = '/portfolio') {
  const deps = configure(width);
  const nav = TestBed.inject(NavigationStateService);
  await deps.router.navigateByUrl(url);
  return { nav, ...deps };
}

describe('NavigationStateService', () => {
  beforeEach(() => localStorage.clear());

  it('tracks the active item and group from the router', async () => {
    const { nav, router } = await create(1440, '/portfolio/transactions');

    expect(nav.activeItem().id).toBe('transactions');
    expect(nav.activeGroup().id).toBe('portfolio');

    await router.navigateByUrl('/accounting/accounts');
    expect(nav.activeItem().id).toBe('accounting');
    expect(nav.activeGroup().id).toBe('accounting');
  });

  it('starts from the current router url', async () => {
    const { router } = configure();
    await router.navigateByUrl('/accounting/settings');

    expect(TestBed.inject(NavigationStateService).activeItem().id).toBe('accounting-settings');
  });

  it('drops hidden groups from the visible list but always keeps 系統', async () => {
    const { nav, prefs } = await create();
    const tabs = () => nav.tabBar().slots.map(slot => (slot.kind === 'plus' ? '+' : slot.group.id));

    expect(nav.visibleGroups().map(group => group.id)).toEqual(['portfolio', 'accounting', 'system']);
    expect(tabs()).toEqual(['portfolio', '+', 'accounting']);
    expect(nav.tabBar().settingsInHeader).toBe(true);

    prefs.setHidden('portfolio', true);
    prefs.setHidden('accounting', true);
    expect(nav.visibleGroups().map(group => group.id)).toEqual(['system']);
    prefs.setHidden('supplies', false);
    expect(tabs()).toEqual(['supplies', '+', 'system']);
    expect(nav.tabBar().settingsInHeader).toBe(false);
  });

  it('opening a pane collapses to the rail and closing restores expanded', async () => {
    const { nav, pane } = await create();

    expect(nav.sidebarMode()).toBe('expanded');
    pane.paneOpen.set(true);
    expect(nav.sidebarMode()).toBe('rail');
    pane.paneOpen.set(false);
    TestBed.tick();
    expect(nav.sidebarMode()).toBe('expanded');
  });

  it('manual expand wins while the pane is open, and resets when it closes', async () => {
    const { nav, pane, prefs } = await create();
    pane.paneOpen.set(true);

    nav.toggleSidebar();
    expect(nav.sidebarMode()).toBe('expanded');
    expect(prefs.sidebarDefault()).toBe('expanded');

    pane.paneOpen.set(false);
    TestBed.tick();
    pane.paneOpen.set(true);
    TestBed.tick();
    expect(nav.sidebarMode()).toBe('rail');
  });

  it('expand while auto-collapsed and stored rail stays expanded', async () => {
    const { nav, pane, prefs } = await create();
    prefs.setSidebarDefault('rail');
    pane.paneOpen.set(true);
    expect(nav.sidebarMode()).toBe('rail');

    nav.toggleSidebar();
    expect(nav.sidebarMode()).toBe('expanded');
    expect(prefs.sidebarDefault()).toBe('expanded');
  });

  it('auto-collapse off keeps expanded with a pane open', async () => {
    const { nav, pane, prefs } = await create();
    prefs.setAutoCollapse(false);

    pane.paneOpen.set(true);
    expect(nav.sidebarMode()).toBe('expanded');
  });

  it('narrow viewport is always the rail and cannot toggle', async () => {
    const { nav, prefs } = await create(1100);

    expect(nav.sidebarMode()).toBe('rail');
    expect(nav.canToggleSidebar()).toBe(false);
    nav.toggleSidebar();
    expect(nav.sidebarMode()).toBe('rail');
    expect(prefs.sidebarDefault()).toBe('expanded');
  });

  it('open pane → expand → collapse → close keeps the rail', async () => {
    const { nav, pane, prefs } = await create();
    expect(nav.canToggleSidebar()).toBe(true);

    pane.paneOpen.set(true);
    expect(nav.sidebarMode()).toBe('rail');
    nav.toggleSidebar();
    expect(nav.sidebarMode()).toBe('expanded');
    nav.toggleSidebar();
    expect(nav.sidebarMode()).toBe('rail');
    expect(prefs.sidebarDefault()).toBe('rail');
    pane.paneOpen.set(false);
    TestBed.tick();
    expect(nav.sidebarMode()).toBe('rail');

    TestBed.resetTestingModule();
    const reloaded = await create();
    expect(reloaded.nav.sidebarMode()).toBe('rail');
  });

  it('override resets through the real lifecycle', async () => {
    const { nav, pane } = await create();
    pane.paneOpen.set(true);
    nav.toggleSidebar();
    expect(nav.sidebarMode()).toBe('expanded');

    pane.paneOpen.set(false);
    TestBed.tick();
    pane.paneOpen.set(true);
    TestBed.tick();
    expect(nav.sidebarMode()).toBe('rail');
  });

  it('direct load of a pane route starts as the rail', async () => {
    const { pane, router } = configure();
    pane.paneOpen.set(true);
    await router.navigateByUrl('/accounting/entries/9');

    expect(TestBed.inject(NavigationStateService).sidebarMode()).toBe('rail');
  });

  it('badges off hides every badge', async () => {
    const { nav, prefs } = await create();
    expect(nav.badges()).toEqual(BADGES);

    prefs.setShowBadges(false);
    expect(nav.badges()).toEqual({});
  });

  it('active group never folds', async () => {
    const { nav, prefs } = await create(1440, '/accounting');

    nav.toggleGroup('accounting');
    expect(nav.isFolded('accounting')).toBe(false);
    expect(prefs.foldedGroups()).toEqual([]);

    nav.toggleGroup('portfolio');
    expect(nav.isFolded('portfolio')).toBe(true);
    expect(prefs.foldedGroups()).toEqual(['portfolio']);
    nav.toggleGroup('portfolio');
    expect(nav.isFolded('portfolio')).toBe(false);
  });

  it('a folded group opens while it is the active group and folds again on leaving', async () => {
    const { nav, prefs, router } = await create(1440, '/accounting');
    prefs.setFolded('portfolio', true);

    await router.navigateByUrl('/portfolio/dividends');
    expect(nav.isFolded('portfolio')).toBe(false);
    await router.navigateByUrl('/accounting');
    expect(nav.isFolded('portfolio')).toBe(true);
  });
});
