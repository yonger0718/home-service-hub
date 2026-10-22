import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { routes } from './app.routes';
import { AccountingShortcutsService, EntryCommand } from './components/accounting/keyboard-shortcuts';
import { DEFAULT_PREFERENCE } from './models/accounting.model';
import { NAV_GROUPS, navItemForUrl } from './components/shell/navigation';
import { ShellComponent } from './components/shell/shell';
import { SubNavComponent } from './components/shell/sub-nav/sub-nav';
import { SidebarComponent } from './components/sidebar/sidebar';
import { NavBadgeService } from './services/nav-badge.service';
import { LayoutModeService, MATCH_MEDIA, PANES_QUERY, PHONE_QUERY } from './services/layout-mode.service';

const PAGES: [string, string][] = [
  ['/accounting', 'app-ledger-timeline'],
  ['/accounting/accounts', 'app-accounting-accounts'],
  ['/accounting/accounts/new', 'app-account-settings'],
  ['/accounting/accounts/5', 'app-accounting-account-entries'],
  ['/accounting/accounts/5/settings', 'app-account-settings'],
  ['/accounting/accounts/5/statements', 'app-statement-list'],
  ['/accounting/accounts/5/statements/2', 'app-statement-detail'],
  ['/accounting/accounts/5/entries/9', 'app-entry-detail'],
  ['/accounting/accounts/5/entries/9/group', 'app-split-group'],
  ['/accounting/entry', 'app-entry-form'],
  ['/accounting/entries/9', 'app-entry-detail'],
  ['/accounting/entries/9/group', 'app-split-group'],
  ['/accounting/entries/9/edit', 'app-entry-form'],
  ['/accounting/settings', 'app-accounting-settings'],
  ['/accounting/reminders', 'app-accounting-reminders'],
];

const REDIRECTED = ['dashboard', 'transactions', 'cards', 'categories', 'recurring'];

function stubMatchMedia(): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

describe('accounting routes', () => {
  beforeEach(() => {
    stubMatchMedia();
    TestBed.configureTestingModule({
      providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
    });
  });

  for (const [url, selector] of PAGES) {
    it(`renders ${selector} inside the accounting layout for ${url}`, async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(url);
      // At 760 px and wider the layout loads its left-hand list asynchronously (Task 21's NgComponentOutlet).
      await harness.fixture.whenStable();
      const root = harness.fixture.nativeElement as HTMLElement;
      // The dynamic import() behind the list pane is not tracked by whenStable: poll until it has rendered.
      await vi.waitFor(() => {
        harness.fixture.detectChanges();
        expect(root.querySelector(selector)).not.toBeNull();
      });

      expect(TestBed.inject(Router).url).toBe(url);
      expect(root.querySelector('app-accounting-layout')).not.toBeNull();
      expect(root.querySelector(selector)).not.toBeNull();
    });
  }
});

/** Both route tables, chosen by the real `MATCH_MEDIA` queries (not a forced layout mode). */
describe('accounting routes per viewport', () => {
  const WIDTHS: [string, number][] = [['phone (390px)', 390], ['wide (1280px)', 1280]];
  const LISTS: Record<string, string> = { timeline: 'app-ledger-timeline', accounts: 'app-accounting-accounts' };

  for (const [label, width] of WIDTHS) {
    describe(label, () => {
      beforeEach(() => {
        const matches = (query: string) => (query === PHONE_QUERY ? width < 760 : query === PANES_QUERY ? width >= 1024 : false);
        TestBed.configureTestingModule({
          providers: [
            provideRouter(routes),
            provideHttpClient(),
            provideHttpClientTesting(),
            {
              provide: MATCH_MEDIA,
              useValue: (query: string) =>
                ({ matches: matches(query), media: query, addEventListener: () => undefined, removeEventListener: () => undefined }) as unknown as MediaQueryList,
            },
          ],
        });
      });

      for (const [url, selector] of PAGES) {
        it(`serves ${url} as ${selector}`, async () => {
          expect(TestBed.inject(LayoutModeService).mode()).toBe(width < 760 ? 'phone' : 'panes');
          const harness = await RouterTestingHarness.create();
          await harness.navigateByUrl(url);
          await harness.fixture.whenStable();
          const root = harness.fixture.nativeElement as HTMLElement;
          await vi.waitFor(() => {
            harness.fixture.detectChanges();
            expect(root.querySelector(selector)).not.toBeNull();
          });
          expect(TestBed.inject(Router).url).toBe(url);
          if (width < 760) {
            // One screen per route: no panes.
            expect(root.querySelector('.dbody')).toBeNull();
            return;
          }
          const list = url.startsWith('/accounting/accounts') ? LISTS['accounts'] : LISTS['timeline'];
          if (url === '/accounting/settings') {
            expect(root.querySelector('.dbody')).toBeNull();
          } else if (selector === list) {
            expect(root.querySelector(`.list-pane ${selector}`)).not.toBeNull();
          } else {
            // A pane page beside its list.
            await vi.waitFor(() => {
              harness.fixture.detectChanges();
              expect(root.querySelector(`.list-pane ${list}`)).not.toBeNull();
            });
            expect(root.querySelector(`.detail-pane ${selector}`)).not.toBeNull();
          }
        });
      }
    });
  }
});

function stubViewport(width: number): void {
  const matches = (query: string) => (query === PHONE_QUERY ? width < 760 : query === PANES_QUERY ? width >= 1024 : false);
  TestBed.configureTestingModule({
    providers: [
      provideRouter(routes),
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: NavBadgeService, useValue: { badges: signal({}) } },
      {
        provide: MATCH_MEDIA,
        useValue: (query: string) =>
          ({ matches: matches(query), media: query, addEventListener: () => undefined, removeEventListener: () => undefined }) as unknown as MediaQueryList,
      },
    ],
  });
}

const LIST_LEVEL: [string, string][] = [
  ['/inventory', '庫存管理'],
  ['/shopping-list', '採買清單'],
  ['/portfolio', '投資概覽'],
  ['/portfolio/transactions', '股票交易紀錄'],
  ['/portfolio/dividends', '股利領取紀錄'],
  ['/portfolio/realized-pnl', '已實現損益'],
  ['/portfolio/accounts', '現金帳戶'],
  ['/portfolio/accounts/5', '現金帳戶'],
  ['/portfolio/import', '匯入 CSV'],
  ['/portfolio/import-broker', '匯入券商對帳單'],
  ['/accounting', '記帳記錄'],
  ['/accounting/accounts', '記帳帳戶'],
  ['/accounting/settings', '記帳設定'],
  ['/settings', '設定'],
];

const DRILL_DOWN: [string, string][] = [
  ['/accounting/accounts/new', '新增帳戶'],
  ['/accounting/accounts/5', '帳戶 #5'],
  ['/accounting/accounts/5/settings', '帳戶資訊'],
  ['/accounting/accounts/5/statements', '對帳帳單'],
  ['/accounting/accounts/5/statements/2', '對帳明細'],
  ['/accounting/accounts/5/entries/9', '記錄明細'],
  ['/accounting/accounts/5/entries/9/group', '多類別'],
  ['/accounting/entry', '新增記錄'],
  ['/accounting/entries/9', '記錄明細'],
  ['/accounting/entries/9/group', '多類別'],
  ['/accounting/entries/9/edit', '編輯記錄'],
  ['/accounting/reminders', '提醒中心'],
];

async function open(url: string): Promise<HTMLElement> {
  const harness = await RouterTestingHarness.create();
  await harness.navigateByUrl(url);
  expect(TestBed.inject(Router).url).toBe(url);
  await harness.fixture.whenStable();
  const root = harness.fixture.nativeElement as HTMLElement;
  await vi.waitFor(() => {
    harness.fixture.detectChanges();
    expect(root.querySelector('h1')).not.toBeNull();
  });
  if (url.endsWith('/edit')) {
    // The entry form only knows it is editing once its reference data has loaded: answer those four reads.
    const http = TestBed.inject(HttpTestingController);
    await vi.waitFor(() => {
      for (const request of http.match(r => r.method === 'GET' && /\/(accounts|projects|counterparties|preference)$/.test(r.url))) {
        request.flush(request.request.url.endsWith('/preference') ? DEFAULT_PREFERENCE : []);
      }
      harness.fixture.detectChanges();
      expect(headings(root)).toContain('編輯記錄');
    });
  }
  return root;
}

const headings = (root: HTMLElement) => Array.from(root.querySelectorAll('h1')).map(h => h.textContent?.trim());

describe('phone route matrix (390 px)', () => {
  beforeEach(() => stubViewport(390));

  for (const [url, title] of LIST_LEVEL) {
    // 設定 is a pushed screen: a ‹ back button and no gear (it would link to itself).
    const gear = url === '/settings' ? 'a ‹ back button, no gear' : 'the gear';
    it(`${url} has one h1 "${title}", ${gear} and the sub-nav`, async () => {
      const root = await open(url);
      expect(headings(root)).toEqual([title]);
      if (url === '/settings') {
        expect(root.querySelector('a[aria-label="設定"]')).toBeNull();
        expect(root.querySelector('button.page-back[aria-label="返回"]')).not.toBeNull();
        expect(root.querySelector('app-sub-nav')).not.toBeNull();
        expect(root.querySelector('app-sub-nav')?.querySelector('a.active') ?? null).toBeNull();
      } else {
        expect(root.querySelector('a[aria-label="設定"][href="/settings"]')).not.toBeNull();
        expect(root.querySelector('button.page-back')).toBeNull();
        expect(root.querySelector('app-sub-nav a.active')).not.toBeNull();
      }
    });
  }

  for (const [url, title] of DRILL_DOWN) {
    it(`${url} has one h1 "${title}" and no gear or sub-nav`, async () => {
      const root = await open(url);
      expect(headings(root)).toEqual([title]);
      expect(root.querySelector('a[aria-label="設定"]')).toBeNull();
      expect(root.querySelector('app-sub-nav')).toBeNull();
    });
  }
});

describe('wide route matrix (1440 px)', () => {
  beforeEach(() => stubViewport(1440));

  for (const [url, title] of LIST_LEVEL) {
    it(`${url} shows the h1 "${title}" without gear or sub-nav`, async () => {
      const root = await open(url);
      expect(headings(root)).toContain(title);
      expect(root.querySelector('a[aria-label="設定"]')).toBeNull();
      expect(root.querySelector('button.page-back')).toBeNull();
      expect(root.querySelector('app-sub-nav')).toBeNull();
    });
  }

  for (const [url, title] of DRILL_DOWN) {
    it(`${url} renders an h1 "${title}" without gear or sub-nav`, async () => {
      const root = await open(url);
      expect(headings(root)).toContain(title);
      expect(root.querySelector('a[aria-label="設定"]')).toBeNull();
      expect(root.querySelector('app-sub-nav')).toBeNull();
    });
  }
});

describe('landing route', () => {
  afterEach(() => localStorage.clear());

  // The manifest's start_url is "/": it opens the first visible group, as the sidebar logo does.
  const CASES: [string, string[] | null, string][] = [
    ['default preferences (物資 hidden)', null, '/portfolio'],
    ['物資 visible', [], '/inventory'],
    ['物資, 投資 and 財務 hidden', ['supplies', 'portfolio', 'accounting'], '/settings'],
  ];

  for (const [label, hidden, target] of CASES) {
    describe(label, () => {
      beforeEach(() => {
        localStorage.clear();
        if (hidden) localStorage.setItem('hh-nav-hidden', JSON.stringify(hidden));
        stubViewport(1440);
      });

      it(`'' lands on ${target}`, async () => {
        const harness = await RouterTestingHarness.create();
        await harness.navigateByUrl('/');
        expect(TestBed.inject(Router).url).toBe(target);
      });

      it(`an unknown URL ends on ${target}`, async () => {
        const harness = await RouterTestingHarness.create();
        await harness.navigateByUrl('/no-such-page');
        expect(TestBed.inject(Router).url).toBe(target);
      });
    });
  }
});

describe('accounting route redirects and shortcuts', () => {
  beforeEach(() => {
    stubMatchMedia();
    TestBed.configureTestingModule({
      providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
    });
  });

  for (const removed of REDIRECTED) {
    it(`lands a bookmarked /accounting/${removed} on the timeline`, async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(`/accounting/${removed}`);
      expect(TestBed.inject(Router).url).toBe('/accounting');
    });
  }

  it('opens the entry form with N from the timeline', async () => {
    // N is the shell's app-wide shortcut: render the shell around the routes.
    const shell = TestBed.createComponent(ShellComponent);
    await TestBed.inject(Router).navigateByUrl('/accounting');
    shell.detectChanges();

    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true, cancelable: true }));
    // The entry form is lazy (loadComponent + import()), which whenStable does not track: poll until the
    // navigation has settled and the form has rendered so the module is not torn down mid-navigation.
    const root = shell.nativeElement as HTMLElement;
    await vi.waitFor(() => {
      shell.detectChanges();
      expect(TestBed.inject(Router).url).toBe('/accounting/entry');
      expect(root.querySelector('app-entry-form')).not.toBeNull();
    });

    expect(TestBed.inject(Router).url).toBe('/accounting/entry');
  });

  it('turns ⇧⏎ on the entry form into a save-and-continue command', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/accounting/entry');
    const received: EntryCommand[] = [];
    const subscription = TestBed.inject(AccountingShortcutsService).entryCommands.subscribe(command => received.push(command));

    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true }));
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    subscription.unsubscribe();

    expect(received).toEqual(['save-continue', 'cancel']);
  });
});

describe('accounting navigation', () => {
  it('points the accounting sub-nav at 記錄, 帳戶 and 記帳設定', () => {
    const accounting = NAV_GROUPS.find(group => group.id === 'accounting')!;
    expect(accounting.defaultPath).toBe('/accounting');
    expect(accounting.items.map(item => [item.label, item.path])).toEqual([
      ['記錄', '/accounting'],
      ['帳戶', '/accounting/accounts'],
      ['記帳設定', '/accounting/settings'],
    ]);
    expect(accounting.items[0].title).toBe('記帳記錄');
  });

  it('keeps the global settings page reachable as its own group', () => {
    expect(NAV_GROUPS.map(group => group.id)).toEqual(['supplies', 'portfolio', 'accounting', 'system']);
    expect(navItemForUrl('/settings').group).toBe('system');
  });

  const HIGHLIGHT: [string, string][] = [
    ['/accounting', '記錄'],
    ['/accounting/entry', '記錄'],
    ['/accounting/entries/9', '記錄'],
    ['/accounting/entries/9/group', '記錄'],
    ['/accounting/accounts', '帳戶'],
    ['/accounting/accounts/5', '帳戶'],
    ['/accounting/accounts/5/settings', '帳戶'],
    ['/accounting/accounts/5/entries/9', '帳戶'],
    ['/accounting/accounts/5/entries/9/group', '帳戶'],
    ['/accounting/settings', '記帳設定'],
  ];

  for (const [url, label] of HIGHLIGHT) {
    it(`highlights ${label} in the sidebar and the mobile sub-nav on ${url}`, async () => {
      const item = navItemForUrl(url);
      expect(item.group).toBe('accounting');
      expect(item.label).toBe(label);

      TestBed.configureTestingModule({ imports: [SidebarComponent, SubNavComponent],
        providers: [provideRouter([{ path: '**', children: [] }]), { provide: NavBadgeService, useValue: { badges: signal({}) } }],
      });
      await TestBed.inject(Router).navigateByUrl(url);
      const sidebar = TestBed.createComponent(SidebarComponent);
      sidebar.detectChanges();
      const active = (sidebar.nativeElement as HTMLElement).querySelectorAll('a.side-item[aria-current="page"]');
      expect(active.length).toBe(1);
      expect(active[0].getAttribute('aria-label')).toBe(item.sidebarLabel);
      expect(active[0].getAttribute('href')).toBe(item.path);

      const sub = TestBed.createComponent(SubNavComponent);
      sub.detectChanges();
      expect((sub.nativeElement as HTMLElement).querySelector('a.active')?.textContent?.trim()).toBe(label);
    });
  }
});
