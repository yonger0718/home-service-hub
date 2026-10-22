import { Component, WritableSignal, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import {
  COMPACT_QUERY,
  MATCH_MEDIA,
  PANES_QUERY,
  PHONE_QUERY,
  SIDEBAR_WIDE_QUERY,
} from '../../services/layout-mode.service';
import { NavBadgeService } from '../../services/nav-badge.service';
import { NavigationPreferencesService } from '../../services/navigation-preferences.service';
import { NAV_ITEMS, NavBadge } from '../shell/navigation';
import { SidebarComponent } from './sidebar';

@Component({ template: '' })
class BlankPage {}

function fakeMatchMedia(width: number, height = 1000) {
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
              : query === COMPACT_QUERY
                ? height <= 820
                : false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as MediaQueryList;
}

interface Rendered {
  fixture: ComponentFixture<SidebarComponent>;
  host: HTMLElement;
  prefs: NavigationPreferencesService;
  badges: WritableSignal<Readonly<Record<string, NavBadge>>>;
  update(): Promise<void>;
}

async function render(
  width: number,
  url = '/portfolio',
  badgeValues: Record<string, NavBadge> = {},
  height?: number,
): Promise<Rendered> {
  const badges = signal<Readonly<Record<string, NavBadge>>>(badgeValues);
  TestBed.configureTestingModule({
    imports: [SidebarComponent],
    providers: [
      provideRouter([{ path: '**', component: BlankPage }]),
      { provide: MATCH_MEDIA, useValue: fakeMatchMedia(width, height) },
      { provide: NavBadgeService, useValue: { badges } },
    ],
  });
  await TestBed.inject(Router).navigateByUrl(url);
  const fixture = TestBed.createComponent(SidebarComponent);
  const update = async () => {
    fixture.detectChanges();
    await fixture.whenStable();
  };
  await update();
  return {
    fixture,
    host: fixture.nativeElement as HTMLElement,
    prefs: TestBed.inject(NavigationPreferencesService),
    badges,
    update,
  };
}

const text = (el: Element | null | undefined) => el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

function heading(host: HTMLElement, label: string): HTMLButtonElement {
  const button = Array.from(host.querySelectorAll<HTMLButtonElement>('button.group-head')).find(
    head => text(head.querySelector('.group-label')) === label,
  );
  if (!button) throw new Error(`no heading ${label}`);
  return button;
}

describe('SidebarComponent', () => {
  beforeEach(() => localStorage.clear());

  it('expanded at 1440 with headings and labels', async () => {
    const { host } = await render(1440);

    expect(host.classList.contains('expanded')).toBe(true);
    expect(host.classList.contains('rail')).toBe(false);
    expect(host.style.getPropertyValue('--sidebar-w')).toBe('248px');
    expect(host.querySelector('nav')!.getAttribute('aria-label')).toBe('主要導覽');
    expect(Array.from(host.querySelectorAll('.group-head .group-label')).map(text)).toEqual(['投資', '財務', '系統']);
    const labels = Array.from(host.querySelectorAll('a.side-item .item-label')).map(text);
    expect(labels).toContain('投資概覽');
    expect(labels).toContain('記帳記錄');
    expect(labels).not.toContain('庫存');
    expect(host.querySelector('.side-tip')).toBeNull();
    expect(host.querySelector('.side-logo img')!.getAttribute('src')).toBe('assets/logo-lockup.svg');
  });

  it('rail at 1100 shows a tooltip per item', async () => {
    const { host } = await render(1100, '/portfolio', {
      'accounting-timeline': { count: 2, label: '提醒中心 2 項' },
      import: { dot: true, label: '最近一次匯入失敗' },
    });

    expect(host.classList.contains('rail')).toBe(true);
    expect(host.style.getPropertyValue('--sidebar-w')).toBe('72px');
    expect(host.querySelectorAll('.group-head').length).toBe(0);
    expect(host.querySelectorAll('.side-divider').length).toBe(3);
    expect(host.querySelector('.side-logo img')!.getAttribute('src')).toBe('assets/app-icon.svg');

    const items = Array.from(host.querySelectorAll<HTMLAnchorElement>('a.side-item'));
    expect(items.length).toBe(10);
    for (const item of items) {
      const navItem = NAV_ITEMS.find(candidate => item.getAttribute('href') === candidate.path)!;
      const tip = item.querySelector('.side-tip');
      expect(tip, navItem.sidebarLabel).not.toBeNull();
      expect(text(tip)).toContain(navItem.sidebarLabel);
      expect(item.getAttribute('aria-label')!.startsWith(navItem.sidebarLabel)).toBe(true);
    }
    const label = (path: string) => items.find(item => item.getAttribute('href') === path)!.getAttribute('aria-label');
    expect(label('/accounting')).toBe('記帳記錄，提醒中心 2 項');
    expect(label('/portfolio/import')).toBe('匯入，最近一次匯入失敗');
    expect(label('/portfolio/transactions')).toBe('交易紀錄');
    expect(items.find(item => item.getAttribute('href') === '/accounting')!.querySelector('.item-count')!.textContent!.trim()).toBe('2');
    expect(items.find(item => item.getAttribute('href') === '/portfolio/import')!.querySelector('.item-dot')).not.toBeNull();

    // The fixed tooltip is placed at the item's centre when the pointer or focus reaches it.
    const icon = items[1].querySelector('.item-icon i')!;
    icon.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    expect(items[1].style.getPropertyValue('--tip-y')).toMatch(/^-?[\d.]+px$/);
    items[2].dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
    expect(items[2].style.getPropertyValue('--tip-y')).toMatch(/^-?[\d.]+px$/);
  });

  it('active item marked', async () => {
    const { host } = await render(1440, '/portfolio/transactions');

    const current = host.querySelectorAll('[aria-current="page"]');
    expect(current.length).toBe(1);
    expect(text(current[0])).toBe('交易紀錄');
  });

  it('folded group shows sum and dot', async () => {
    const onPortfolio = await render(1440, '/portfolio', {
      'accounting-timeline': { count: 2, label: '提醒中心 2 項' },
      accounting: { count: 1, label: '1 筆待確認' },
    });
    const finance = heading(onPortfolio.host, '財務');
    expect(finance.getAttribute('aria-expanded')).toBe('true');
    expect(finance.querySelector('.group-count')).toBeNull();

    finance.click();
    await onPortfolio.update();
    expect(finance.getAttribute('aria-expanded')).toBe('false');
    expect(text(finance.querySelector('.group-count'))).toBe('3');
    expect(onPortfolio.host.querySelector('a.side-item[href="/accounting"]')).toBeNull();
    expect(onPortfolio.prefs.foldedGroups()).toEqual(['accounting']);

    TestBed.resetTestingModule();
    localStorage.clear();
    const onAccounting = await render(1440, '/accounting', { import: { dot: true, label: '最近一次匯入失敗' } });
    const invest = heading(onAccounting.host, '投資');
    invest.click();
    await onAccounting.update();
    expect(invest.getAttribute('aria-expanded')).toBe('false');
    expect(invest.querySelector('.group-dot')).not.toBeNull();
    expect(invest.querySelector('.group-count')).toBeNull();
  });

  it('active group shows 目前 and stays open on click', async () => {
    const { host, prefs, update } = await render(1440, '/accounting');
    const finance = heading(host, '財務');

    expect(text(finance.querySelector('.group-hint'))).toBe('目前');
    expect(finance.querySelector('.group-chevron')).toBeNull();
    expect(heading(host, '投資').querySelector('.group-hint')).toBeNull();
    expect(heading(host, '投資').querySelector('.group-chevron')).not.toBeNull();

    finance.click();
    await update();
    expect(finance.getAttribute('aria-expanded')).toBe('true');
    expect(host.querySelector('a.side-item[href="/accounting"]')).not.toBeNull();
    expect(prefs.foldedGroups()).toEqual([]);
  });

  it('collapse button stores rail', async () => {
    const wide = await render(1440);
    const collapse = wide.host.querySelector<HTMLButtonElement>('button.side-toggle')!;
    expect(collapse.getAttribute('aria-label')).toBe('收合側欄');

    collapse.click();
    await wide.update();
    expect(wide.prefs.sidebarDefault()).toBe('rail');
    expect(wide.host.classList.contains('rail')).toBe(true);
    expect(wide.host.querySelector('button.side-toggle')!.getAttribute('aria-label')).toBe('展開側欄');

    TestBed.resetTestingModule();
    localStorage.clear();
    const narrow = await render(1100);
    expect(narrow.host.querySelector('button.side-toggle')).toBeNull();
  });

  it('logo links to the first visible group', async () => {
    const first = await render(1440);
    expect(first.host.querySelector('a.side-logo')!.getAttribute('href')).toBe('/portfolio');

    first.prefs.setHidden('portfolio', true);
    first.prefs.setHidden('accounting', true);
    await first.update();
    expect(first.host.querySelector('a.side-logo')!.getAttribute('href')).toBe('/settings');
  });

  it('badges off hides the item badges', async () => {
    const { host, prefs, update } = await render(1440, '/portfolio', {
      'accounting-timeline': { count: 2, label: '提醒中心 2 項' },
    });
    expect(host.querySelector('a.side-item[href="/accounting"] .item-count')).not.toBeNull();

    prefs.setShowBadges(false);
    await update();
    const timeline = host.querySelector('a.side-item[href="/accounting"]')!;
    expect(timeline.querySelector('.item-count')).toBeNull();
    expect(timeline.getAttribute('aria-label')).toBe('記帳記錄');
  });

  it('marks the host compact on a short screen and keeps one scroll container', async () => {
    const { host } = await render(1440, '/portfolio', {}, 800);

    expect(host.classList.contains('compact')).toBe(true);
    // overflow-y lives in sidebar.scss (jsdom does not apply it); the structure it relies on is checked here.
    const scroller = host.querySelector('.hub-sidebar')!;
    expect(Array.from(scroller.children).map(child => child.className)).toEqual(['side-head', 'side-nav']);
  });
});
