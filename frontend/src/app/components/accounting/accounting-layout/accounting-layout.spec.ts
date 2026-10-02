import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { describe, expect, it, vi } from 'vitest';

import { routes } from '../../../app.routes';
import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
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
/** A page in the layout's primary outlet (`:scope >`: jsdom 27 mis-caches a bare `router-outlet + *`). */
const SCREEN = ':scope > router-outlet + *';

describe('AccountingLayoutComponent', () => {
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
});
