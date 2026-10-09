import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { routes } from './app.routes';
import { AccountingShortcutsService, EntryCommand } from './components/accounting/keyboard-shortcuts';
import { DockComponent } from './components/dock/dock';
import { MobileNavComponent } from './components/mobile-nav/mobile-nav';
import { NAV_GROUPS, navItemForUrl } from './components/shell/navigation';
import { LayoutModeService, MATCH_MEDIA, PANES_QUERY, PHONE_QUERY } from './services/layout-mode.service';

const PAGES: [string, string][] = [
  ['/accounting', 'app-ledger-timeline'],
  ['/accounting/accounts', 'app-accounting-accounts'],
  ['/accounting/accounts/new', 'app-account-settings'],
  ['/accounting/accounts/5', 'app-accounting-account-entries'],
  ['/accounting/accounts/5/settings', 'app-account-settings'],
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
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/accounting');

    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'n', bubbles: true }));
    // The entry form is lazy (loadComponent + import()), which whenStable does not track: poll until the
    // navigation has settled and the form has rendered so the module is not torn down mid-navigation.
    const root = harness.fixture.nativeElement as HTMLElement;
    await vi.waitFor(() => {
      harness.fixture.detectChanges();
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
    expect(NAV_GROUPS.map(group => group.id)).toEqual(['supplies', 'portfolio', 'accounting', 'settings']);
    expect(navItemForUrl('/settings').group).toBe('settings');
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
    it(`highlights ${label} in the dock and the mobile sub-nav on ${url}`, () => {
      const item = navItemForUrl(url);
      expect(item.group).toBe('accounting');
      expect(item.label).toBe(label);

      TestBed.configureTestingModule({ imports: [DockComponent, MobileNavComponent], providers: [provideRouter([])] });
      const dock = TestBed.createComponent(DockComponent);
      dock.componentRef.setInput('activeId', item.id);
      dock.detectChanges();
      const active = (dock.nativeElement as HTMLElement).querySelector('.dock-item[aria-current="page"]');
      expect(active?.getAttribute('aria-label')).toBe(item.title);

      const mobile = TestBed.createComponent(MobileNavComponent);
      mobile.componentRef.setInput('activeId', item.id);
      mobile.componentRef.setInput('activeGroupId', 'accounting');
      mobile.detectChanges();
      expect((mobile.nativeElement as HTMLElement).querySelector('.seg-control a.active')?.textContent?.trim()).toBe(label);
    });
  }
});
