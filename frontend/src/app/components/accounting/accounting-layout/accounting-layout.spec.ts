import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Location } from '@angular/common';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { describe, expect, it, vi } from 'vitest';

import { routes } from '../../../app.routes';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { makeEntryDetail, makePreference } from '../testing/fixtures';
import { AccountingLayoutComponent } from './accounting-layout';

async function start(mode: LayoutMode, url: string): Promise<RouterTestingHarness> {
  TestBed.configureTestingModule({
    providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
  });
  TestBed.inject(LayoutModeService).set(mode);
  const harness = await RouterTestingHarness.create();
  await harness.navigateByUrl(url);
  return harness;
}

/** Polls through lazy loading and NgComponentOutlet, running change detection each time. */
function find(harness: RouterTestingHarness, selector: string): Promise<Element> {
  return vi.waitFor(
    () => {
      harness.detectChanges();
      const found = harness.routeNativeElement?.querySelector(selector);
      if (!found) {
        throw new Error(`no ${selector} yet`);
      }
      return found;
    },
    { timeout: 3000, interval: 10 },
  );
}

const LIST = '.list-pane > *';
const PANE_PAGE = '.detail-pane router-outlet + :not(.pane-empty)';
/**
 * A page in the layout's primary outlet, never the FAB that follows it before the page loads
 * (`:scope >`: jsdom 27 mis-caches a bare `router-outlet + *`).
 */
const SCREEN = ':scope > router-outlet + :not(.fab)';

describe('AccountingLayoutComponent', () => {
  it('sets a scoped .72rem minimum for captions inherited by calendar and keypad', async () => {
    const harness = await start('phone', '/accounting');
    harness.detectChanges();
    const host = harness.routeNativeElement!;
    const hostAttribute = Array.from(host.attributes).find(attribute => attribute.name.startsWith('_nghost-'))!.name;
    const installedStyle = Array.from(document.querySelectorAll('style')).find(style =>
      style.textContent?.includes(`[${hostAttribute}]`),
    )!;
    // Sass emits @charset for this stylesheet's Chinese comments. jsdom ignores the first rule after it;
    // browsers ignore this encoding directive in style elements. Normalize only this installed test style.
    installedStyle.textContent = installedStyle.textContent!.replace(/^@charset [^;]+;\s*/, '');
    expect(getComputedStyle(host).getPropertyValue('--fs-micro').trim()).toBe('0.72rem');
  });

  it('keeps the list and updates the URL when a row is selected at 1280px', async () => {
    const harness = await start('panes', '/accounting');
    const list = await find(harness, LIST);
    expect(harness.routeNativeElement!.querySelector('.pane-empty')).not.toBeNull();

    await TestBed.inject(Router).navigateByUrl('/accounting/entries/5');
    await find(harness, PANE_PAGE);

    expect(TestBed.inject(Router).url).toBe('/accounting/entries/5');
    expect(harness.routeNativeElement!.querySelector(LIST)).toBe(list);
    const layout = harness.routeDebugElement!.componentInstance as AccountingLayoutComponent;
    expect(layout.selectedEntryId()).toBe(5);
    expect(layout.paneOpen()).toBe(true);
  });

  it('shows list and detail on a direct load of /accounting/entries/5 at 1280px', async () => {
    const harness = await start('panes', '/accounting/entries/5');

    await find(harness, LIST);
    await find(harness, PANE_PAGE);
    expect(harness.routeNativeElement!.querySelector('.dbody')).not.toBeNull();
  });

  it('renders the detail as its own screen on a phone, without panes or FAB', async () => {
    const harness = await start('phone', '/accounting/entries/5');
    const el = harness.routeNativeElement!;

    await find(harness, SCREEN);
    expect(el.querySelector('.dbody')).toBeNull();
    expect(el.querySelector('.fab')).toBeNull();
  });

  it('opens the detail as a sheet at 820px and closes it with ✕', async () => {
    const harness = await start('sheet', '/accounting');
    await find(harness, LIST);
    expect(harness.routeNativeElement!.querySelector('.detail-pane.open')).toBeNull();

    const router = TestBed.inject(Router);
    await router.navigateByUrl('/accounting/entries/5');
    await find(harness, '.detail-pane.open');
    (await find(harness, '.sheet-close') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(router.url).toBe('/accounting'));
  });

  it('closes the sheet on Escape and restores focus', async () => {
    const harness = await start('sheet', '/accounting');
    await find(harness, LIST);
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();

    const router = TestBed.inject(Router);
    await router.navigateByUrl('/accounting/entries/5');
    const sheet = await find(harness, '.detail-pane.open');
    expect(sheet.getAttribute('role')).toBe('dialog');
    expect(sheet.getAttribute('aria-modal')).toBe('true');
    expect(sheet.getAttribute('aria-label')).toBe('明細');
    const closeButton = await find(harness, '.sheet-close');
    await vi.waitFor(() => expect(document.activeElement).toBe(closeButton));

    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    document.dispatchEvent(escape);

    expect(escape.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(router.url).toBe('/accounting'));
    const layout = harness.routeDebugElement!.componentInstance as AccountingLayoutComponent;
    await vi.waitFor(() => {
      harness.detectChanges();
      expect(layout.paneOpen()).toBe(false);
      expect(document.activeElement).toBe(opener);
    });
    opener.remove();
  });

  it('leaves an Escape already handled inside the sheet (the entry form cancels itself)', async () => {
    const harness = await start('sheet', '/accounting');
    await find(harness, LIST);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/accounting/entries/5');
    await find(harness, '.detail-pane.open');
    const navigate = vi.spyOn(router, 'navigateByUrl');

    const inner = document.createElement('button');
    document.body.appendChild(inner);
    inner.addEventListener('keydown', event => event.preventDefault());
    inner.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    inner.remove();

    expect(navigate).not.toHaveBeenCalled();
    expect(router.url).toBe('/accounting/entries/5');
  });

  it('leaves an Escape that is part of an IME composition, so an unsaved form is not discarded', async () => {
    const harness = await start('sheet', '/accounting');
    await find(harness, LIST);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/accounting/entries/5');
    await find(harness, '.detail-pane.open');
    const navigate = vi.spyOn(router, 'navigateByUrl');

    const composing = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true, isComposing: true });
    document.dispatchEvent(composing);
    const ime = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true, keyCode: 229 } as KeyboardEventInit);
    document.dispatchEvent(ime);

    expect(composing.defaultPrevented).toBe(false);
    expect(ime.defaultPrevented).toBe(false);
    expect(navigate).not.toHaveBeenCalled();
    expect(router.url).toBe('/accounting/entries/5');

    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    document.dispatchEvent(escape);
    expect(escape.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(router.url).toBe('/accounting'));
  });

  it('leaves Escape alone when no sheet is open, and has no dialog role in panes mode', async () => {
    const harness = await start('panes', '/accounting/entries/5');
    const pane = await find(harness, '.detail-pane');
    await find(harness, PANE_PAGE);
    expect(pane.getAttribute('role')).toBeNull();
    expect(pane.getAttribute('aria-modal')).toBeNull();

    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    document.dispatchEvent(escape);

    expect(escape.defaultPrevented).toBe(false);
    expect(TestBed.inject(Router).url).toBe('/accounting/entries/5');
  });

  it('shows the floating plus from 760px, pointing at the entry form', async () => {
    const harness = await start('panes', '/accounting/accounts');

    const fab = await find(harness, 'a.fab');
    expect(fab.getAttribute('href')).toBe('/accounting/entry');
    expect(fab.getAttribute('aria-label')).toBe('新增記錄');
  });

  it('re-resolves the routes when the viewport crosses the phone breakpoint', async () => {
    const harness = await start('phone', '/accounting/entries/5');
    await find(harness, SCREEN);

    TestBed.inject(LayoutModeService).set('panes');

    await find(harness, LIST);
    await find(harness, PANE_PAGE);
    expect(TestBed.inject(Router).url).toBe('/accounting/entries/5');
  });

  it('renders the accounting settings page full width at 1280px', async () => {
    const harness = await start('panes', '/accounting/settings');

    await find(harness, SCREEN);
    expect(harness.routeNativeElement!.querySelector('.dbody')).toBeNull();
  });
  it('colours every page from the 支出收入顏色 preference through one layout class', async () => {
    const harness = await start('panes', '/accounting/entries/42');
    const http = TestBed.inject(HttpTestingController);
    const layoutHost = harness.routeNativeElement!;
    http.expectOne('/api/accounting/preference').flush(makePreference({ expense_income_colors: 'green_red' }));
    const total = await vi.waitFor(() => {
      http.match('/api/accounting/entries/42').forEach(req => req.flush(makeEntryDetail({ id: 42, amount: '-170.0000' })));
      return find(harness, '.detail-total');
    });

    expect(layoutHost.classList).toContain('green-red');
    expect(total.classList).toContain('neg');
    // The detail's `.neg` reads --tone-neg, which the layout sets to the green token under `.green-red`.
    expect(getComputedStyle(layoutHost).getPropertyValue('--tone-neg').trim()).toBe('var(--c-green)');

    // A preference saved elsewhere is re-read.
    TestBed.inject(AccountingService).updatePreference(makePreference()).subscribe();
    http.expectOne(r => r.method === 'PUT').flush(makePreference());
    harness.detectChanges();
    await vi.waitFor(() => {
      harness.detectChanges();
      expect(layoutHost.classList).not.toContain('green-red');
    });
  });
  it('shows a passbook entry beside the accounts list at 1280px', async () => {
    const harness = await start('panes', '/accounting/accounts/5/entries/9');

    expect((await find(harness, LIST)).tagName.toLowerCase()).toBe('app-accounting-accounts');
    expect((await find(harness, PANE_PAGE)).tagName.toLowerCase()).toBe('app-entry-detail');
    expect(TestBed.inject(Router).url).toBe('/accounting/accounts/5/entries/9');
  });

  it('shows the reminder centre beside the timeline at 1280px and as its own screen on a phone', async () => {
    const harness = await start('panes', '/accounting/reminders');
    expect((await find(harness, LIST)).tagName.toLowerCase()).toBe('app-ledger-timeline');
    expect((await find(harness, PANE_PAGE)).tagName.toLowerCase()).toBe('app-accounting-reminders');
    expect(TestBed.inject(Router).url).toBe('/accounting/reminders');
  });

  it('renders the reminder centre as a full page on a phone', async () => {
    const harness = await start('phone', '/accounting/reminders');
    expect((await find(harness, SCREEN)).tagName.toLowerCase()).toBe('app-accounting-reminders');
    expect(harness.routeNativeElement!.querySelector('.dbody')).toBeNull();
  });

  it('moves into expanded reminder rows with ↓ and leaves ↓ alone without them', async () => {
    const harness = await start('phone', '/accounting/reminders');
    await find(harness, SCREEN);
    const router = TestBed.inject(Router);
    expect(harness.routeNativeElement!.querySelector('[data-entry-id]')).toBeNull();
    expect(press('ArrowDown').defaultPrevented).toBe(false);
    expect(router.url).toBe('/accounting/reminders');

    addRows(harness, [72, 74]);
    expect(press('ArrowDown').defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(router.url).toBe('/accounting/entries/72'));
    // ✕ on that detail returns to the reminder centre.
    expect((TestBed.inject(Location).getState() as Record<string, unknown>)['closeTo']).toBe('reminders');
  });

  it('keeps ↓ inside the reminder centre pane at 1280px, past the timeline rows on the left', async () => {
    const harness = await start('panes', '/accounting/reminders');
    const list = await find(harness, LIST);
    const pane = await find(harness, PANE_PAGE);
    const router = TestBed.inject(Router);
    const timelineRows = document.createElement('div');
    for (const id of [5, 6]) {
      const row = document.createElement('button');
      row.dataset['entryId'] = String(id);
      timelineRows.appendChild(row);
    }
    list.appendChild(timelineRows);
    // No expanded counterparty yet: ↓ is left to the page, never taken by the timeline's rows.
    expect(press('ArrowDown').defaultPrevented).toBe(false);
    expect(router.url).toBe('/accounting/reminders');

    const expanded = document.createElement('div');
    for (const id of [72, 74]) {
      const row = document.createElement('button');
      row.dataset['entryId'] = String(id);
      expanded.appendChild(row);
    }
    pane.appendChild(expanded);
    expect(press('ArrowDown').defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(router.url).toBe('/accounting/entries/72'));
    expect((TestBed.inject(Location).getState() as Record<string, unknown>)['closeTo']).toBe('reminders');
  });

  /** Rows as the list pages render them; the layout moves through `[data-entry-id]` in its host. */
  function addRows(harness: RouterTestingHarness, ids: number[]): HTMLElement {
    const box = document.createElement('div');
    for (const id of ids) {
      const row = document.createElement('a');
      row.dataset['entryId'] = String(id);
      box.appendChild(row);
    }
    harness.routeNativeElement!.appendChild(box);
    return box;
  }

  function press(key: string, init: KeyboardEventInit = {}): KeyboardEvent {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
    document.body.dispatchEvent(event);
    return event;
  }

  it('moves through passbook rows with ↓ / ↑ and stays in the passbook', async () => {
    const harness = await start('phone', '/accounting/accounts/5');
    const router = TestBed.inject(Router);
    addRows(harness, [7, 8, 9]);

    expect(press('ArrowDown').defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(router.url).toBe('/accounting/accounts/5/entries/7'));
    addRows(harness, [7, 8, 9]);
    press('ArrowDown');
    await vi.waitFor(() => expect(router.url).toBe('/accounting/accounts/5/entries/8'));
    press('ArrowUp');
    await vi.waitFor(() => expect(router.url).toBe('/accounting/accounts/5/entries/7'));
  });

  it('leaves keys already handled, IME commits and arrows without rows to the page', async () => {
    const harness = await start('phone', '/accounting');
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigate');

    const handled = new KeyboardEvent('keydown', { key: 'n', bubbles: true, cancelable: true });
    handled.preventDefault();
    document.body.dispatchEvent(handled);
    expect(press('n', { isComposing: true }).defaultPrevented).toBe(false);
    expect(press('n', { keyCode: 229 } as KeyboardEventInit).defaultPrevented).toBe(false);
    // No rows on screen: ↓ is not taken from the page (it may scroll).
    expect(harness.routeNativeElement!.querySelector('[data-entry-id]')).toBeNull();
    expect(press('ArrowDown').defaultPrevented).toBe(false);

    expect(navigate).not.toHaveBeenCalled();
    expect(router.url).toBe('/accounting');
  });
  it('closes the sheet only on a horizontal swipe that starts on the grip', async () => {
    const harness = await start('sheet', '/accounting');
    await find(harness, LIST);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/accounting/entries/5');
    const sheet = await find(harness, '.detail-pane.open');
    const grip = await find(harness, '.sheet-grip');
    expect(grip.querySelector('.sheet-close')).not.toBeNull();
    const swipe = (on: Element, dx: number, dy: number) => {
      on.dispatchEvent(new MouseEvent('pointerdown', { clientX: 100, clientY: 20, bubbles: true }));
      on.dispatchEvent(new MouseEvent('pointermove', { clientX: 100 + dx, clientY: 20 + dy, bubbles: true }));
      on.dispatchEvent(new MouseEvent('pointerup', { clientX: 100 + dx, clientY: 20 + dy, bubbles: true }));
    };

    // Anywhere else in the pane (a text field of the page) never closes.
    const field = document.createElement('input');
    sheet.appendChild(field);
    swipe(field, 100, 10);
    swipe(sheet, 100, 10);
    swipe(grip, 50, 0);
    swipe(grip, 100, 60);
    const control = document.createElement('button'); grip.appendChild(control);
    swipe(control, 100, 10); control.remove();
    grip.dispatchEvent(new MouseEvent('pointerdown', { clientX: 100, clientY: 20, bubbles: true }));
    grip.dispatchEvent(new MouseEvent('pointermove', { clientX: 200, clientY: 80, bubbles: true }));
    grip.dispatchEvent(new MouseEvent('pointerup', { clientX: 200, clientY: 20, bubbles: true }));
    harness.detectChanges();
    expect(router.url).toBe('/accounting/entries/5');

    // A cancelled pointer never closes either.
    grip.dispatchEvent(new MouseEvent('pointerdown', { clientX: 100, clientY: 20, bubbles: true }));
    grip.dispatchEvent(new MouseEvent('pointercancel', { bubbles: true }));
    grip.dispatchEvent(new MouseEvent('pointerup', { clientX: 200, clientY: 20, bubbles: true }));
    expect(router.url).toBe('/accounting/entries/5');

    swipe(grip, 100, 10);
    await vi.waitFor(() => expect(router.url).toBe('/accounting'));
    field.remove();
  });

  it('keeps a grip without handle or ✕ in the two-pane layout and never swipes there', async () => {
    const harness = await start('panes', '/accounting/entries/5');
    await find(harness, PANE_PAGE);
    const grip = await find(harness, '.sheet-grip');
    expect(grip.classList).not.toContain('sheet-grip--active');
    expect(grip.querySelector('.sheet-close')).toBeNull();
    grip.dispatchEvent(new MouseEvent('pointerdown', { clientX: 100, clientY: 20, bubbles: true }));
    grip.dispatchEvent(new MouseEvent('pointerup', { clientX: 300, clientY: 20, bubbles: true }));
    expect(TestBed.inject(Router).url).toBe('/accounting/entries/5');
  });

});
