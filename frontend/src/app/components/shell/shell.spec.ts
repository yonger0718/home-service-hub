import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, Routes, provideRouter } from '@angular/router';
import { describe, expect, it, vi } from 'vitest';

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

  it('moves the floating button beside the list while a pane is open', async () => {
    const { fab, settle } = await render(viewportOf(1440), { url: '/accounting' });
    expect(fab()!.classList.contains('beside-pane')).toBe(false);

    TestBed.inject(PaneStateService).paneOpen.set(true);
    await settle();
    expect(fab()!.classList.contains('beside-pane')).toBe(true);
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
      expect(fab()!.classList.contains('beside-pane')).toBe(true);

      await router.navigateByUrl('/accounting/entry');
      await find('.detail-pane app-entry-form');
      await settle();
      expect(fab()).toBeNull();
    });
  });
});
