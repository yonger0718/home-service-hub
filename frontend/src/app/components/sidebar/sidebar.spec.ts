import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { Component, WritableSignal, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  COMPACT_QUERY,
  MATCH_MEDIA,
  PANES_QUERY,
  PHONE_QUERY,
  SIDEBAR_WIDE_QUERY,
} from '../../services/layout-mode.service';
import { DirtyAware, DirtyFormRegistry } from '../accounting/dirty-form.service';
import { NavBadgeService } from '../../services/nav-badge.service';
import { NO_TASKS, NotifSummary, notifLabel } from '../notifications/notif-task';
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
  summary: WritableSignal<NotifSummary>;
  update(): Promise<void>;
}

async function render(
  width: number,
  url = '/portfolio',
  badgeValues: Record<string, NavBadge> = {},
  height?: number,
  notif: NotifSummary = NO_TASKS,
): Promise<Rendered> {
  const summary = signal<NotifSummary>(notif);
  const badges = signal<Readonly<Record<string, NavBadge>>>(badgeValues);
  TestBed.configureTestingModule({
    imports: [SidebarComponent],
    providers: [
      provideRouter([{ path: '**', component: BlankPage }]),
      { provide: MATCH_MEDIA, useValue: fakeMatchMedia(width, height) },
      { provide: NavBadgeService, useValue: { summary, badges } },
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
    summary,
    update,
  };
}

const summaryOf = (total: number, overdue: number, tone: 'red' | 'primary'): NotifSummary => ({
  total,
  overdue,
  tone,
  label: notifLabel(total, overdue),
});
const RED_7 = summaryOf(7, 3, 'red');

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
    expect(host.querySelector('.side-logo app-logo-lockup svg')).not.toBeNull();
    expect(host.querySelector('.side-logo img')).toBeNull();
  });

  it('rail at 1100 shows a tooltip per item', async () => {
    const { host } = await render(1100, '/portfolio', {
      'accounting': { dot: true, tone: 'red', label: '有逾期的卡費' },
      'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' },
      import: { dot: true, label: '最近一次匯入失敗' },
    });

    expect(host.classList.contains('rail')).toBe(true);
    expect(host.style.getPropertyValue('--sidebar-w')).toBe('72px');
    expect(host.querySelectorAll('.group-head').length).toBe(0);
    expect(host.querySelectorAll('.side-divider').length).toBe(3);
    expect(host.querySelector('.side-logo img')!.getAttribute('src')).toBe('assets/app-icon.svg');

    const items = Array.from(host.querySelectorAll<HTMLAnchorElement>('a.side-item:not(.side-notif)'));
    expect(items.length).toBe(11);
    for (const item of items) {
      const navItem = NAV_ITEMS.find(candidate => item.getAttribute('href') === candidate.path)!;
      const tip = item.querySelector('.side-tip');
      expect(tip, navItem.sidebarLabel).not.toBeNull();
      expect(text(tip)).toContain(navItem.sidebarLabel);
      expect(item.getAttribute('aria-label')!.startsWith(navItem.sidebarLabel)).toBe(true);
    }
    const label = (path: string) => items.find(item => item.getAttribute('href') === path)!.getAttribute('aria-label');
    expect(label('/accounting')).toBe('記帳記錄，有待確認的匯入紀錄');
    expect(label('/portfolio/import')).toBe('匯入，最近一次匯入失敗');
    expect(label('/portfolio/transactions')).toBe('交易紀錄');
    expect(items.find(item => item.getAttribute('href') === '/accounting')!.querySelector('.item-dot')).not.toBeNull();
    expect(host.querySelector('.item-count')).toBeNull();
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
      'accounting': { dot: true, tone: 'red', label: '有逾期的卡費' },
      'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' },
    });
    const finance = heading(onPortfolio.host, '財務');
    expect(finance.getAttribute('aria-expanded')).toBe('true');
    expect(finance.querySelector('.group-dot')).toBeNull();

    finance.click();
    await onPortfolio.update();
    expect(finance.getAttribute('aria-expanded')).toBe('false');
    expect(finance.querySelectorAll('.group-dot').length).toBe(1);
    expect(finance.querySelector('.group-count')).toBeNull();
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
      'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' },
    });
    expect(host.querySelector('a.side-item[href="/accounting"] .item-dot')).not.toBeNull();

    prefs.setShowBadges(false);
    await update();
    const timeline = host.querySelector('a.side-item[href="/accounting"]')!;
    expect(timeline.querySelector('.item-dot')).toBeNull();
    expect(timeline.getAttribute('aria-label')).toBe('記帳記錄');
  });

  it('no nav item renders a count (expanded and rail)', async () => {
    // Fixture keys are nav item ids: `accounting` is the 帳戶 item, `accounting-timeline` is 記帳記錄 (href `/accounting`).
    const badges: Record<string, NavBadge> = {
      'accounting': { dot: true, tone: 'red', label: '有逾期的卡費' },
      'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' },
    };
    const expanded = await render(1440, '/portfolio', badges);
    expect(expanded.host.querySelector('.item-count, .group-count')).toBeNull();
    TestBed.resetTestingModule();
    localStorage.clear();
    const rail = await render(1100, '/portfolio', badges);
    expect(rail.host.querySelector('.item-count, .group-count')).toBeNull();
    expect(rail.host.querySelectorAll('.item-dot').length).toBe(2);
  });

  it('a primary dot carries item-dot--primary, a red one does not', async () => {
    const { host } = await render(1440, '/portfolio', {
      'accounting': { dot: true, tone: 'red', label: '有逾期的卡費' },
      'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' },
      import: { dot: true, label: '最近一次匯入失敗' },
    });
    const dot = (path: string) => host.querySelector(`a.side-item[href="${path}"] .item-dot`)!;
    expect(dot('/accounting').classList.contains('item-dot--primary')).toBe(true);
    expect(dot('/accounting/accounts').classList.contains('item-dot--primary')).toBe(false);
    expect(dot('/portfolio/import').classList.contains('item-dot--primary')).toBe(false);
  });

  it('a folded group shows one dot, red when any of its items is red', async () => {
    const primaryOnly = await render(1440, '/portfolio', {
      'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' },
    });
    const head = heading(primaryOnly.host, '財務');
    head.click();
    await primaryOnly.update();
    expect(head.querySelectorAll('.group-dot').length).toBe(1);
    expect(head.querySelector('.group-dot')!.classList.contains('group-dot--primary')).toBe(true);
    TestBed.resetTestingModule();
    localStorage.clear();

    const mixed = await render(1440, '/portfolio', {
      'accounting': { dot: true, tone: 'red', label: '有逾期的卡費' },
      'accounting-timeline': { dot: true, tone: 'primary', label: '有待確認的匯入紀錄' },
    });
    const head2 = heading(mixed.host, '財務');
    head2.click();
    await mixed.update();
    expect(head2.querySelectorAll('.group-dot').length).toBe(1);
    expect(head2.querySelector('.group-dot')!.classList.contains('group-dot--primary')).toBe(false);
  });

  it('styles dots with the red-ink and primary tokens and has no count rule', () => {
    const scss = readFileSync(join(process.cwd(), 'src/app/components/sidebar/sidebar.scss'), 'utf8');
    expect(scss).toContain('var(--c-red-ink)');
    expect(scss).toContain('var(--app-primary)');
    expect(scss).not.toMatch(/\.item-count|\.group-count/);
  });

  it('marks the host compact on a short screen and keeps one scroll container', async () => {
    const { host } = await render(1440, '/portfolio', {}, 800);

    expect(host.classList.contains('compact')).toBe(true);
    // overflow-y lives in sidebar.scss (jsdom does not apply it); the structure it relies on is checked here.
    const scroller = host.querySelector('.hub-sidebar')!;
    expect(Array.from(scroller.children).map(child => child.className)).toEqual(['side-head', 'side-nav']);
  });
});

describe('SidebarComponent 記一筆', () => {
  beforeEach(() => localStorage.clear());

  const link = (host: HTMLElement) => host.querySelectorAll<HTMLAnchorElement>('a.sidebar-new-entry');

  it('expanded: one filled link with the text 記一筆 at the top of the nav, above the first group', async () => {
    const { host } = await render(1440);

    expect(link(host).length).toBe(1);
    const entry = link(host)[0];
    expect(text(entry)).toBe('記一筆');
    expect(entry.getAttribute('href')).toBe('/accounting/entry');
    expect(entry.getAttribute('aria-label')).toBe('記一筆（快捷鍵 N）');
    expect(entry.querySelector('.side-tip')).toBeNull();
    expect(entry.querySelector('.pi-plus')).not.toBeNull();
    const nav = host.querySelector('nav.side-nav')!;
    expect(nav.firstElementChild).toBe(entry);
    expect(entry.nextElementSibling!.classList.contains('side-notif')).toBe(true);
    expect(entry.nextElementSibling!.nextElementSibling!.classList.contains('side-group')).toBe(true);
  });

  it('rail: the same link as an icon with a tooltip, no visible text, the accessible name 記一筆（快捷鍵 N）', async () => {
    const { host } = await render(1100);

    expect(link(host).length).toBe(1);
    const entry = link(host)[0];
    expect(host.classList.contains('rail')).toBe(true);
    expect(entry.getAttribute('aria-label')).toBe('記一筆（快捷鍵 N）');
    expect(entry.querySelector('.side-new-entry-label')).toBeNull();
    expect(entry.querySelector('.side-tip')!.getAttribute('aria-hidden')).toBe('true');
    expect(text(entry.querySelector('.side-tip'))).toContain('記一筆');
    expect(entry.querySelector('.pi-plus')).not.toBeNull();
    expect(host.querySelector('nav.side-nav')!.firstElementChild).toBe(entry);
    // One line: the label and the key sit in one inline wrapper, not as two flex-column items.
    expect(entry.querySelectorAll('.side-tip > *').length).toBe(1);
    expect(entry.querySelector('.side-tip > .tip-line kbd')).not.toBeNull();
  });

  it('rail at compact height keeps the link and the nav items the same size and has the 4 px extra gap above', async () => {
    const { host } = await render(1100, '/portfolio', {}, 700);
    expect(host.classList.contains('compact')).toBe(true);
    expect(host.classList.contains('rail')).toBe(true);
    expect(link(host).length).toBe(1);
    const css = readFileSync(join(__dirname, 'sidebar.scss'), 'utf8');
    expect(css).toMatch(/:host\(\.compact\.rail\)[\s\S]*\.side-item,\s*\.sidebar-new-entry\s*\{\s*height:\s*38px;\s*width:\s*38px/);
  });

  it('rail adds 4 px above the link, so the gap under the logo row equals the 8 px below it', () => {
    const css = readFileSync(join(__dirname, 'sidebar.scss'), 'utf8');
    expect(css).toMatch(/:host\(\.rail\)\s*\{[\s\S]*?\.sidebar-new-entry\s*\{[^}]*margin-top:\s*4px/);
  });

  it('on the new-entry form a plain click does nothing: no dirty prompt, no navigation', async () => {
    const { host } = await render(1440, '/accounting/entry');
    const router = TestBed.inject(Router);
    const registry = TestBed.inject(DirtyFormRegistry);
    const dirty: DirtyAware = { isDirty: () => true, showDiscardPrompt: vi.fn() };
    registry.register(dirty);
    const navigate = vi.spyOn(router, 'navigate');

    const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
    link(host)[0].dispatchEvent(click);

    expect(click.defaultPrevented).toBe(true);
    expect(dirty.showDiscardPrompt).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
    expect(router.url).toBe('/accounting/entry');
  });

  it('on the new-entry form with a query a plain click also does nothing', async () => {
    const { host } = await render(1440, '/accounting/entry?month=2026-10');
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate');
    const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
    link(host)[0].dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('on the edit form of an entry a dirty form is still asked first', async () => {
    const { host } = await render(1440, '/accounting/entries/9/edit');
    const registry = TestBed.inject(DirtyFormRegistry);
    const dirty: DirtyAware = { isDirty: () => true, showDiscardPrompt: vi.fn() };
    registry.register(dirty);

    link(host)[0].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));

    expect(dirty.showDiscardPrompt).toHaveBeenCalledTimes(1);
  });

  it('a ctrl-click on the new-entry form is left to the browser', async () => {
    const { host } = await render(1440, '/accounting/entry');
    let prevented: boolean | null = null;
    const swallow = (event: Event) => {
      prevented = event.defaultPrevented;
      event.preventDefault();
    };
    document.addEventListener('click', swallow, { once: true });
    link(host)[0].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ctrlKey: true }));
    expect(prevented).toBe(false);
  });

  it('is shown on the report pages and the entry form (no hiding conditions)', async () => {
    for (const url of ['/accounting/reports?month=2026-08', '/accounting/reports/custom/7', '/accounting/entry', '/portfolio']) {
      TestBed.resetTestingModule();
      const { host } = await render(1440, url);
      expect(link(host).length, url).toBe(1);
    }
  });

  it('is not rendered in phone mode (the bottom tab bar has its own +)', async () => {
    const { host } = await render(390);
    expect(link(host).length).toBe(0);
  });

  it('its href carries the calendar month and day', async () => {
    const { host } = await render(1440, '/accounting?month=2026-10&day=2026-10-21');
    expect(link(host)[0].getAttribute('href')).toBe('/accounting/entry?month=2026-10&day=2026-10-21');
  });

  it('a plain click asks a dirty form first, then navigates to the entry form with the calendar params', async () => {
    const { host } = await render(1440, '/accounting?month=2026-10&day=2026-10-21');
    const router = TestBed.inject(Router);
    const registry = TestBed.inject(DirtyFormRegistry);
    const dirty: DirtyAware = { isDirty: () => true, showDiscardPrompt: vi.fn() };
    registry.register(dirty);

    const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
    link(host)[0].dispatchEvent(click);

    expect(click.defaultPrevented).toBe(true);
    expect(dirty.showDiscardPrompt).toHaveBeenCalledTimes(1);
    expect(router.url).toBe('/accounting?month=2026-10&day=2026-10-21');
    registry.confirmDiscard();
    await vi.waitFor(() => expect(router.url).toBe('/accounting/entry?month=2026-10&day=2026-10-21'));
  });

  it('a plain click without a dirty form navigates at once', async () => {
    const { host } = await render(1440, '/portfolio');
    const router = TestBed.inject(Router);
    link(host)[0].click();
    await vi.waitFor(() => expect(router.url).toBe('/accounting/entry'));
  });

  it('a modified click (new tab) is left to the browser', async () => {
    const { host } = await render(1440, '/portfolio');
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigate');
    let prevented: boolean | null = null;
    const swallow = (event: Event) => {
      prevented = event.defaultPrevented;
      event.preventDefault();
    };
    document.addEventListener('click', swallow, { once: true });

    link(host)[0].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, metaKey: true }));

    expect(prevented).toBe(false);
    expect(navigate).not.toHaveBeenCalled();
    expect(router.url).toBe('/portfolio');
  });
});


describe('SidebarComponent 通知', () => {
  beforeEach(() => localStorage.clear());

  const link = (host: HTMLElement) => host.querySelector<HTMLAnchorElement>('a.side-notif')!;

  it.each([
    ['expanded 1440', 1440],
    ['rail 1100', 1100],
  ])('%s: 通知 sits above the first group, after 記一筆, and links to /notifications', async (_name, width) => {
    const { host } = await render(width);
    const nav = host.querySelector('nav.side-nav')!;
    const kids = Array.from(nav.children);
    const notif = link(host);
    expect(notif.getAttribute('href')).toBe('/notifications');
    expect(notif.querySelector('i.pi-bell')).not.toBeNull();
    const entry = nav.querySelector('a.sidebar-new-entry')!;
    const firstGroup = nav.querySelector('.side-group')!;
    expect(kids.indexOf(notif)).toBe(kids.indexOf(entry) + 1);
    expect(kids.indexOf(notif)).toBeLessThan(kids.indexOf(firstGroup));
  });

  it('shows 7 in the red pill with the label 通知，7 件要處理，3 件已逾期, announced once', async () => {
    const { host } = await render(1440, '/portfolio', {}, undefined, RED_7);
    const notif = link(host);
    expect(notif.getAttribute('aria-label')).toBe('通知，7 件要處理，3 件已逾期');
    const pill = notif.querySelector('.notif-count')!;
    expect(text(pill)).toBe('7');
    expect(pill.classList.contains('notif-count--red')).toBe(true);
    expect(pill.getAttribute('aria-hidden')).toBe('true');
    expect(notif.querySelectorAll('.notif-count').length).toBe(1);
    expect(text(notif.querySelector('.item-label'))).toBe('通知');
    expect(notif.querySelector('.sr-only')).toBeNull();
  });

  it('a primary pill when nothing is overdue', async () => {
    const { host } = await render(1440, '/portfolio', {}, undefined, summaryOf(2, 0, 'primary'));
    const pill = link(host).querySelector('.notif-count')!;
    expect(text(pill)).toBe('2');
    expect(pill.classList.contains('notif-count--red')).toBe(false);
    expect(link(host).getAttribute('aria-label')).toBe('通知，2 件要處理');
  });

  it('no pill at 0, and the label is 通知 (also while the first round has not settled)', async () => {
    const { host } = await render(1440);
    expect(link(host).querySelector('.notif-count')).toBeNull();
    expect(link(host).getAttribute('aria-label')).toBe('通知');
  });

  it('badges off: no pill, label 通知', async () => {
    const { host, prefs, update } = await render(1440, '/portfolio', {}, undefined, RED_7);
    expect(link(host).querySelector('.notif-count')).not.toBeNull();
    prefs.setShowBadges(false);
    await update();
    expect(link(host).querySelector('.notif-count')).toBeNull();
    expect(link(host).getAttribute('aria-label')).toBe('通知');
  });

  it('hiding 財務 and 投資 keeps 通知', async () => {
    localStorage.setItem('hh-nav-hidden', JSON.stringify(['accounting', 'portfolio']));
    const { host } = await render(1440, '/settings');
    expect(link(host)).not.toBeNull();
    TestBed.resetTestingModule();
    const rail = await render(1100, '/settings');
    expect(link(rail.host)).not.toBeNull();
  });

  it('aria-current="page" on /notifications and no group head is current', async () => {
    const { host } = await render(1440, '/notifications');
    expect(link(host).getAttribute('aria-current')).toBe('page');
    expect(host.querySelectorAll('[aria-current="page"]').length).toBe(1);
    expect(host.querySelector('.group-head.current')).toBeNull();
    expect(host.querySelector('a.side-item.active:not(.side-notif)')).toBeNull();
  });

  it('is not current on another page', async () => {
    const { host } = await render(1440, '/portfolio');
    expect(link(host).getAttribute('aria-current')).toBeNull();
  });

  it('rail: the pill sits on the icon and the tip reads 通知 and 7 件要處理，3 件已逾期', async () => {
    const { host } = await render(1100, '/portfolio', {}, undefined, RED_7);
    const notif = link(host);
    expect(text(notif.querySelector('.item-icon .notif-count'))).toBe('7');
    const tip = notif.querySelector('.side-tip')!;
    expect(text(tip)).toBe('通知 7 件要處理，3 件已逾期');
    expect(text(tip.querySelector('small'))).toBe('7 件要處理，3 件已逾期');
    expect(notif.getAttribute('aria-label')).toBe('通知，7 件要處理，3 件已逾期');
  });

  it('rail: at 0 the tip reads 通知 alone and there is no pill', async () => {
    const { host } = await render(1100);
    const tip = link(host).querySelector('.side-tip')!;
    expect(text(tip)).toBe('通知');
    expect(tip.querySelector('small')).toBeNull();
    expect(link(host).querySelector('.notif-count')).toBeNull();
  });

  it('follows the summary as it changes', async () => {
    const { host, summary, update } = await render(1440);
    summary.set(RED_7);
    await update();
    expect(text(link(host).querySelector('.notif-count'))).toBe('7');
  });
});
