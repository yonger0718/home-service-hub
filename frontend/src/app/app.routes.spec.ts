import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { describe, expect, it } from 'vitest';

import { routes } from './app.routes';
import { NAV_GROUPS, NAV_ITEMS } from './components/shell/navigation';

const REMOVED_ACCOUNTING_PAGES = ['dashboard', 'transactions', 'settings', 'cards', 'categories', 'recurring'];

describe('accounting routes', () => {
  const paths = routes.map(route => route.path);

  it('redirects the removed accounting pages to the accounts page', () => {
    for (const removed of REMOVED_ACCOUNTING_PAGES) {
      const route = routes.find(r => r.path === `accounting/${removed}`);
      expect(route?.redirectTo).toBe('accounting/accounts');
      expect(route?.loadComponent).toBeUndefined();
    }
  });

  it('redirects /accounting to the accounts page', () => {
    expect(routes.find(route => route.path === 'accounting')?.redirectTo).toBe('accounting/accounts');
  });

  it('points the accounting navigation only at the ledger pages and settings', () => {
    const accounting = NAV_GROUPS.find(group => group.id === 'accounting')!;
    expect(accounting.defaultPath).toBe('/accounting/accounts');
    expect(accounting.items.map(item => item.path)).toEqual(['/accounting/accounts', '/settings']);
    expect(NAV_ITEMS.map(item => item.id)).not.toContain('accounting-dash');
  });

  it('serves the accounts list page', () => {
    expect(paths).toContain('accounting/accounts');
  });

  it('lands a bookmarked /accounting/cards on the accounts page', async () => {
    TestBed.configureTestingModule({
      providers: [provideRouter(routes), provideHttpClient(), provideHttpClientTesting()],
    });
    const harness = await RouterTestingHarness.create();

    await harness.navigateByUrl('/accounting/cards');

    expect(TestBed.inject(Router).url).toBe('/accounting/accounts');
  });
});
