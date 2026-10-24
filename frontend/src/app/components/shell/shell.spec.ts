import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, Routes, provideRouter } from '@angular/router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { routes } from '../../app.routes';
import {
  COMPACT_QUERY,
  MATCH_MEDIA,
  MatchMediaFn,
  PANES_QUERY,
  PHONE_QUERY,
  SIDEBAR_WIDE_QUERY,
} from '../../services/layout-mode.service';
import { NavBadgeService } from '../../services/nav-badge.service';
import { PaneStateService } from '../../services/pane-state.service';
import { FAB_RIGHT_BESIDE_PANE } from '../accounting/accounting-layout/accounting-layout';
import { DirtyAware, DirtyFormRegistry } from '../accounting/dirty-form.service';
import { AccountingShortcutsService, EntryCommand } from '../accounting/keyboard-shortcuts';
import { ShellComponent } from './shell';

@Component({ template: '' })
class BlankPage {}

/** A 1280 px wide viewport whose height the test controls. */
function fakeViewport(initialHeight: number) {
  let height = initialHeight;
  const listeners = new Set<() => void>();
  const matchMedia = (query: string) =>
    ({
      media: query,
      get matches() {
        if (query === COMPACT_QUERY) {
          return height <= 820;
        }
        return query === PANES_QUERY;
      },
      addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
    }) as unknown as MediaQueryList;
  return {
    matchMedia,
    setHeight(next: number) {
      height = next;
      listeners.forEach(listener => listener());
    },
  };
}

/** A viewport `width` px wide (and 1000 px tall). */
function viewportOf(width: number): MatchMediaFn {
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
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }) as unknown as MediaQueryList;
}

interface Rendered {
  fixture: ComponentFixture<ShellComponent>;
  el: HTMLElement;
  router: Router;
  badgeStart: ReturnType<typeof vi.fn>;
  root(): HTMLElement;
  fab(): HTMLAnchorElement | null;
  /** Polls through lazy routes and NgComponentOutlet, running change detection each time. */
  find(selector: string): Promise<HTMLElement>;
  settle(): Promise<void>;
}

async function render(
  matchMedia: MatchMediaFn,
  options: { routes?: Routes; url?: string; http?: boolean } = {},
): Promise<Rendered> {
  const badgeStart = vi.fn();
  TestBed.configureTestingModule({
    imports: [ShellComponent],
    providers: [
      provideRouter(options.routes ?? [{ path: '**', component: BlankPage }]),
      ...(options.http ? [provideHttpClient(), provideHttpClientTesting()] : []),
      { provide: MATCH_MEDIA, useValue: matchMedia },
      { provide: NavBadgeService, useValue: { badges: signal({}), start: badgeStart } },
    ],
  });
  const fixture = TestBed.createComponent(ShellComponent);
  const router = TestBed.inject(Router);
  if (options.url) {
    await router.navigateByUrl(options.url);
  }
  fixture.detectChanges();
  await fixture.whenStable();
  const el = fixture.nativeElement as HTMLElement;
  const find = (selector: string) =>
    vi.waitFor(
      () => {
        fixture.detectChanges();
        const found = el.querySelector<HTMLElement>(selector);
        if (!found) {
          throw new Error(`no ${selector} yet`);
        }
        return found;
      },
      { timeout: 3000, interval: 10 },
    );
  return {
    fixture,
    el,
    router,
    badgeStart,
    root: () => el.querySelector<HTMLElement>('.hub-layout')!,
    fab: () => el.querySelector<HTMLAnchorElement>('a.hub-fab'),
    find,
    settle: async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
    },
  };
}

function press(key: string, from: Element = document.body, init: KeyboardEventInit = {}): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  from.dispatchEvent(event);
  return event;
}

describe('ShellComponent', () => {
  it('marks the layout compact-height on a short (800 px) screen', async () => {
    const { root } = await render(fakeViewport(800).matchMedia);

    expect(root().classList.contains('compact-height')).toBe(true);
  });

  it('leaves the layout at full density on a tall (1080 px) screen', async () => {
    const { root } = await render(fakeViewport(1080).matchMedia);

    expect(root().classList.contains('compact-height')).toBe(false);
  });

  it('toggles compact-height as the viewport height crosses 820 px', async () => {
    const viewport = fakeViewport(1080);
    const { fixture, root } = await render(viewport.matchMedia);

    viewport.setHeight(800);
    fixture.detectChanges();
    expect(root().classList.contains('compact-height')).toBe(true);

    viewport.setHeight(900);
    fixture.detectChanges();
    expect(root().classList.contains('compact-height')).toBe(false);
  });

  it('has no frosted header: the sidebar and each page carry the logo and title', async () => {
    const { el } = await render(viewportOf(1440), { url: '/portfolio' });

    expect(el.querySelector('header.hub-header')).toBeNull();
    expect(el.querySelector('.hub-logo')).toBeNull();
    expect(el.querySelector('app-sidebar')).not.toBeNull();
    expect(el.querySelector('.hub-main > main.hub-page router-outlet')).not.toBeNull();
    expect(el.querySelector('app-mobile-nav')).not.toBeNull();
  });

  it('shows the floating 記一筆 button on every route from 760 px', async () => {
    for (const [width, url] of [[1440, '/portfolio'], [1100, '/settings'], [820, '/']] as const) {
      TestBed.resetTestingModule();
      const { fab } = await render(viewportOf(width), { url });

      const button = fab()!;
      expect(button, `${width} ${url}`).not.toBeNull();
      expect(button.getAttribute('href')).toBe('/accounting/entry');
      expect(button.getAttribute('aria-label')).toBe('記一筆（快捷鍵 N）');
      const tip = button.querySelector('.fab-tip')!;
      expect(tip.getAttribute('aria-hidden')).toBe('true');
      expect(tip.textContent?.replace(/\s+/g, ' ').trim()).toBe('記一筆 N');
      expect(tip.querySelector('kbd')?.textContent).toBe('N');
    }
  });

  it('has no floating button at phone width (the tab bar has its own +)', async () => {
    const { fab } = await render(viewportOf(390), { url: '/portfolio' });

    expect(fab()).toBeNull();
  });

  it('hides the floating button while the entry form is open', async () => {
    const { fab, settle } = await render(viewportOf(1440), { url: '/portfolio' });
    const pane = TestBed.inject(PaneStateService);

    pane.formOpen.set(true);
    await settle();
    expect(fab()).toBeNull();

    pane.formOpen.set(false);
    await settle();
    expect(fab()).not.toBeNull();
  });

  it('places the floating button where the pane layout says', async () => {
    const { fab, settle } = await render(viewportOf(1440), { url: '/accounting' });
    expect(fab()!.style.getPropertyValue('--fab-right')).toBe('');

    TestBed.inject(PaneStateService).fabRight.set('calc(1px + 2px)');
    await settle();
    expect(fab()!.style.getPropertyValue('--fab-right')).toBe('calc(1px + 2px)');
  });

  it('opens the entry form with N on /portfolio', async () => {
    const { router } = await render(viewportOf(1440), { url: '/portfolio' });

    const event = press('n');

    expect(event.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(router.url).toBe('/accounting/entry'));
  });

  it('ignores an N another handler already handled, and IME commits', async () => {
    const { router } = await render(viewportOf(1440), { url: '/portfolio' });
    const navigate = vi.spyOn(router, 'navigate');

    const handled = new KeyboardEvent('keydown', { key: 'n', bubbles: true, cancelable: true });
    handled.preventDefault();
    document.body.dispatchEvent(handled);
    expect(press('n', document.body, { isComposing: true }).defaultPrevented).toBe(false);
    expect(press('n', document.body, { keyCode: 229 } as KeyboardEventInit).defaultPrevented).toBe(false);

    expect(navigate).not.toHaveBeenCalled();
    expect(router.url).toBe('/portfolio');
  });

  it('ignores N typed into a field', async () => {
    const { router } = await render(viewportOf(1440), { url: '/portfolio' });
    const input = document.createElement('input');
    document.body.appendChild(input);
    try {
      expect(press('n', input).defaultPrevented).toBe(false);
      expect(router.url).toBe('/portfolio');
    } finally {
      input.remove();
    }
  });

  it('ignores N on a combobox, listbox or spinbutton (p-select focuses a span role="combobox")', async () => {
    const { router } = await render(viewportOf(1440), { url: '/portfolio' });
    const host = document.createElement('div');
    host.innerHTML = `
      <span role="combobox" tabindex="0"></span>
      <ul role="listbox"><li role="option" tabindex="0">A</li></ul>
      <div role="spinbutton" tabindex="0"></div>`;
    document.body.appendChild(host);
    const navigate = vi.spyOn(router, 'navigate');
    try {
      for (const target of Array.from(host.querySelectorAll<HTMLElement>('[role="combobox"], [role="option"], [role="spinbutton"]'))) {
        target.focus();
        expect(press('n', target).defaultPrevented, target.getAttribute('role')!).toBe(false);
      }
      expect(navigate).not.toHaveBeenCalled();
      expect(router.url).toBe('/portfolio');
    } finally {
      host.remove();
    }
  });

  it('ignores N from <body> while a modal dialog is open elsewhere in the page', async () => {
    const { router } = await render(viewportOf(1440), { url: '/portfolio/dividends' });
    const navigate = vi.spyOn(router, 'navigate');
    for (const attrs of [{ role: 'dialog', 'aria-modal': 'true' }, { role: 'alertdialog' }]) {
      const dialog = document.createElement('div');
      for (const [name, value] of Object.entries(attrs)) dialog.setAttribute(name, value);
      document.body.appendChild(dialog);
      try {
        (document.activeElement as HTMLElement | null)?.blur();
        expect(press('n').defaultPrevented, attrs.role).toBe(false);
      } finally {
        dialog.remove();
      }
    }
    expect(navigate).not.toHaveBeenCalled();
    expect(router.url).toBe('/portfolio/dividends');
  });

  it('a 記一筆 click asks a dirty form first and navigates once 放棄 is confirmed', async () => {
    const { fab, router } = await render(viewportOf(1440), { url: '/accounting/accounts/5/settings' });
    const registry = TestBed.inject(DirtyFormRegistry);
    const dirty: DirtyAware = { isDirty: () => true, showDiscardPrompt: vi.fn() };
    registry.register(dirty);

    const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
    fab()!.dispatchEvent(click);

    expect(click.defaultPrevented).toBe(true);
    expect(dirty.showDiscardPrompt).toHaveBeenCalledTimes(1);
    expect(router.url).toBe('/accounting/accounts/5/settings');

    registry.confirmDiscard();
    await vi.waitFor(() => expect(router.url).toBe('/accounting/entry'));
  });

  it('a 記一筆 click without a dirty form opens the entry form', async () => {
    const { fab, router } = await render(viewportOf(1440), { url: '/portfolio' });

    fab()!.click();

    await vi.waitFor(() => expect(router.url).toBe('/accounting/entry'));
  });

  it('a modified 記一筆 click (new tab) is left to the browser', async () => {
    const { fab, router } = await render(viewportOf(1440), { url: '/portfolio' });
    const navigate = vi.spyOn(router, 'navigate');
    let preventedByShell: boolean | null = null;
    // Records what the shell did, then stops jsdom from following the link.
    const swallow = (event: Event) => {
      preventedByShell = event.defaultPrevented;
      event.preventDefault();
    };
    document.addEventListener('click', swallow, { once: true });

    fab()!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0, ctrlKey: true }));

    expect(preventedByShell).toBe(false);
    expect(navigate).not.toHaveBeenCalled();
    expect(router.url).toBe('/portfolio');
  });

  it('offsets the main column by the sidebar: expanded when wide, the rail otherwise', async () => {
    const wide = await render(viewportOf(1440), { url: '/portfolio' });
    expect(wide.root().classList.contains('sidebar-expanded')).toBe(true);
    expect(wide.root().classList.contains('sidebar-rail')).toBe(false);
    expect(wide.root().style.getPropertyValue('--sidebar-w')).toBe('248px');

    TestBed.resetTestingModule();
    const narrow = await render(viewportOf(1100), { url: '/portfolio' });
    expect(narrow.root().classList.contains('sidebar-rail')).toBe(true);
    expect(narrow.root().classList.contains('sidebar-expanded')).toBe(false);
    expect(narrow.root().style.getPropertyValue('--sidebar-w')).toBe('72px');
  });

  it('starts the badge refresh once', async () => {
    const { badgeStart, fixture, router } = await render(viewportOf(1440), { url: '/portfolio' });
    await router.navigateByUrl('/settings');
    fixture.detectChanges();

    expect(badgeStart).toHaveBeenCalledTimes(1);
  });

  describe('with the real routes and the accounting layout', () => {
    const real = (width: number, url: string) => render(viewportOf(width), { routes, url, http: true });

    it('820 px: N inside the detail sheet opens the form', async () => {
      const { router, find } = await real(820, '/accounting/entries/9');
      const close = await find('.detail-pane.open .sheet-close');
      close.focus();

      expect(press('n', close).defaultPrevented).toBe(true);
      await vi.waitFor(() => expect(router.url).toBe('/accounting/entry'));
      await find('.detail-pane app-entry-form');
    });

    it('N inside a nested dialog does nothing', async () => {
      const { router, find } = await real(1440, '/accounting/entries/9');
      await find('.detail-pane app-entry-detail');
      const dialog = document.createElement('div');
      dialog.setAttribute('role', 'dialog');
      const button = document.createElement('button');
      dialog.appendChild(button);
      document.body.appendChild(dialog);
      try {
        button.focus();
        expect(press('n', button).defaultPrevented).toBe(false);
        expect(router.url).toBe('/accounting/entries/9');
      } finally {
        dialog.remove();
      }
    });

    it('820 px: N inside a confirmation (alertdialog) in the sheet does nothing', async () => {
      const { router, find } = await real(820, '/accounting/entries/9');
      const sheet = await find('.detail-pane.open');
      // As the inline 刪除記錄 / 刪除整組 confirmations render: role="alertdialog" without aria-modal.
      const confirm = document.createElement('div');
      confirm.setAttribute('role', 'alertdialog');
      const button = document.createElement('button');
      confirm.appendChild(button);
      sheet.appendChild(confirm);
      try {
        button.focus();
        expect(press('n', button).defaultPrevented).toBe(false);
        expect(router.url).toBe('/accounting/entries/9');
      } finally {
        confirm.remove();
      }
    });

    it('N on a dirty entry form is ignored', async () => {
      const { router, find } = await real(1440, '/accounting/entries/9/edit');
      await find('.detail-pane app-entry-form');
      const dirty: DirtyAware = { isDirty: () => true, showDiscardPrompt: vi.fn() };
      TestBed.inject(DirtyFormRegistry).register(dirty);
      (document.activeElement as HTMLElement | null)?.blur();

      expect(press('n').defaultPrevented).toBe(false);
      expect(dirty.showDiscardPrompt).not.toHaveBeenCalled();
      expect(router.url).toBe('/accounting/entries/9/edit');
    });

    it('820 px: N over unsaved account input asks first and opens the form only after 放棄', async () => {
      const { router, find, settle } = await real(820, '/accounting/accounts/new');
      const name = (await find('.detail-pane app-account-settings .name-input')) as HTMLInputElement;
      name.value = '悠遊卡';
      name.dispatchEvent(new Event('input'));
      const credit = await find('.detail-pane .credit-toggle');
      credit.focus();

      expect(press('n', credit).defaultPrevented).toBe(true);
      const leave = await find('.detail-pane .discard-strip .discard-leave');
      await settle();
      expect(router.url).toBe('/accounting/accounts/new');
      expect(name.value).toBe('悠遊卡');

      leave.click();
      await vi.waitFor(() => expect(router.url).toBe('/accounting/entry'));
    });

    it('1440 px: a 記一筆 click over unsaved account input asks first and opens the form only after 放棄', async () => {
      const { router, find, fab, settle } = await real(1440, '/accounting/accounts/new');
      const name = (await find('.detail-pane app-account-settings .name-input')) as HTMLInputElement;
      name.value = '悠遊卡';
      name.dispatchEvent(new Event('input'));
      await settle();

      const click = new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 });
      fab()!.dispatchEvent(click);
      expect(click.defaultPrevented).toBe(true);
      const leave = await find('.detail-pane .discard-strip .discard-leave');
      await settle();
      expect(router.url).toBe('/accounting/accounts/new');

      leave.click();
      await vi.waitFor(() => expect(router.url).toBe('/accounting/entry'));
    });

    it('E still edits the selected entry (the layout\'s key)', async () => {
      const { router, find } = await real(1440, '/accounting/entries/9');
      await find('.detail-pane app-entry-detail');

      expect(press('e').defaultPrevented).toBe(true);
      await vi.waitFor(() => expect(router.url).toBe('/accounting/entries/9/edit'));
    });

    it('↓ still walks the list rows (the layout\'s key)', async () => {
      const { router, find } = await real(1440, '/accounting');
      const layout = await find('app-accounting-layout');
      const rows = document.createElement('div');
      for (const id of [7, 8]) {
        const row = document.createElement('a');
        row.dataset['entryId'] = String(id);
        rows.appendChild(row);
      }
      layout.appendChild(rows);

      expect(press('ArrowDown').defaultPrevented).toBe(true);
      await vi.waitFor(() => expect(router.url).toBe('/accounting/entries/7'));
    });

    it('⇧⏎ on the entry form still saves and continues (the layout\'s key)', async () => {
      const { find } = await real(1440, '/accounting/entry');
      await find('.detail-pane app-entry-form');
      const received: EntryCommand[] = [];
      const subscription = TestBed.inject(AccountingShortcutsService).entryCommands.subscribe(command => received.push(command));

      press('Enter', document.body, { shiftKey: true });
      subscription.unsubscribe();

      expect(received).toEqual(['save-continue']);
    });

    it('hides the floating button while the 820 px sheet is open, and brings it back when it closes', async () => {
      const { router, find, fab, settle } = await real(820, '/accounting');
      await find('.list-pane > *');
      expect(fab()).not.toBeNull();

      await router.navigateByUrl('/accounting/entries/9');
      await find('.detail-pane.open');
      await settle();
      expect(fab()).toBeNull();

      await router.navigateByUrl('/accounting');
      await vi.waitFor(async () => {
        await settle();
        expect(fab()).not.toBeNull();
      });
    });

    it('hides the floating button on the entry form and moves it beside the list for an open pane', async () => {
      const { router, find, fab, settle } = await real(1440, '/accounting/entries/9');
      await find('.detail-pane app-entry-detail');
      await settle();
      expect(fab()!.style.getPropertyValue('--fab-right')).toBe(FAB_RIGHT_BESIDE_PANE);

      await router.navigateByUrl('/accounting/entry');
      await find('.detail-pane app-entry-form');
      await settle();
      expect(fab()).toBeNull();
    });
  });
});

function fakeScroller(el: Element, top: number, scrollHeight = 2000, clientHeight = 800) {
  for (const [k, v] of Object.entries({ scrollTop: top, scrollHeight, clientHeight })) {
    Object.defineProperty(el, k, { value: v, configurable: true });
  }
}

describe('記一筆 hides while scrolling (D5)', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  async function start() {
    const r = await render(viewportOf(1440), { url: '/somewhere' });
    await r.settle();
    const scrollDocument = async (top: number, scrollHeight = 2000, clientHeight = 800) => {
      fakeScroller(document.documentElement, top, scrollHeight, clientHeight);
      document.dispatchEvent(new Event('scroll'));
      await r.settle();
    };
    const hidden = () => r.fab()!.classList.contains('is-hidden');
    return { ...r, scrollDocument, hidden };
  }

  it('hides after a downward document scroll and returns on an upward one', async () => {
    const { scrollDocument, hidden } = await start();
    await scrollDocument(0);
    await scrollDocument(120);
    expect(hidden()).toBe(true);
    await scrollDocument(100);
    expect(hidden()).toBe(false);
  });

  it('returns at the end of the page', async () => {
    const { scrollDocument, hidden } = await start();
    await scrollDocument(0);
    await scrollDocument(120);
    expect(hidden()).toBe(true);
    await scrollDocument(1200, 2000, 800);
    expect(hidden()).toBe(false);
  });

  it('returns after 1.2 s without scrolling', async () => {
    const { scrollDocument, hidden, settle } = await start();
    await scrollDocument(0);
    await scrollDocument(120);
    expect(hidden()).toBe(true);
    vi.advanceTimersByTime(1199);
    await settle();
    expect(hidden()).toBe(true);
    vi.advanceTimersByTime(1);
    await settle();
    expect(hidden()).toBe(false);
  });

  it('never hides while it has focus', async () => {
    const { scrollDocument, hidden, fab } = await start();
    fab()!.focus();
    await scrollDocument(0);
    await scrollDocument(120);
    expect(hidden()).toBe(false);
  });

  it('ignores scrolls that are not the page scroller', async () => {
    const { el, settle, hidden } = await start();
    const page = el.querySelector('.hub-page')!;
    const plain = document.createElement('div');
    page.appendChild(plain);
    fakeScroller(plain, 0);
    plain.dispatchEvent(new Event('scroll'));
    fakeScroller(plain, 300);
    plain.dispatchEvent(new Event('scroll'));
    await settle();
    expect(hidden()).toBe(false);

    const marked = document.createElement('div');
    marked.setAttribute('data-page-scroller', '');
    page.appendChild(marked);
    fakeScroller(marked, 0);
    marked.dispatchEvent(new Event('scroll'));
    fakeScroller(marked, 300);
    marked.dispatchEvent(new Event('scroll'));
    await settle();
    expect(hidden()).toBe(true);
  });

  it('reacts to .hub-main when it is the scroller', async () => {
    const { el, settle, hidden } = await start();
    const main = el.querySelector('.hub-main')!;
    fakeScroller(main, 0);
    main.dispatchEvent(new Event('scroll'));
    fakeScroller(main, 200);
    main.dispatchEvent(new Event('scroll'));
    await settle();
    expect(hidden()).toBe(true);
  });

  it('comes back on a route change', async () => {
    const { scrollDocument, hidden, router, settle } = await start();
    await scrollDocument(0);
    await scrollDocument(120);
    expect(hidden()).toBe(true);
    await router.navigateByUrl('/portfolio/dividends');
    await settle();
    expect(hidden()).toBe(false);
  });

  it('the hidden state is a class the stylesheet turns into visibility: hidden', () => {
    const css = readFileSync(join(process.cwd(), 'src/app/components/shell/shell.scss'), 'utf8');
    expect(css).toMatch(/&\.is-hidden\s*{[^}]*visibility:\s*hidden/);
    expect(css).toMatch(/transition:[^;]*160ms/);
    expect(css).toMatch(/transition:[^;]*visibility 0s linear 0s/);
    expect(css).toMatch(/&\.is-hidden\s*{[^}]*transition-delay:\s*0s,\s*0s,\s*160ms/);
    expect(css).toMatch(/prefers-reduced-motion:\s*reduce\)\s*{[\s\S]*\.hub-fab\s*{\s*transition:\s*none/);
  });
});
