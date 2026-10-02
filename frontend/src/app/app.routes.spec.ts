import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { describe, expect, it } from 'vitest';

import { routes } from './app.routes';
import { DockComponent } from './components/dock/dock';
import { NAV_GROUPS, NAV_ITEMS, navItemForUrl } from './components/shell/navigation';

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

  it('serves the account entry history page', () => {
    expect(paths).toContain('accounting/accounts/:id');
  });

  it('keeps the Accounting dock item highlighted on an account history page', () => {
    TestBed.configureTestingModule({ imports: [DockComponent], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(DockComponent);

    fixture.componentRef.setInput('activeId', navItemForUrl('/accounting/accounts/5').id);
    fixture.detectChanges();

    const active = (fixture.nativeElement as HTMLElement).querySelector('.dock-item[aria-current="page"]');
    expect(navItemForUrl('/accounting/accounts/5').id).toBe('accounting');
    expect(active?.getAttribute('aria-label')).toBe('記帳帳戶');
    expect(active?.classList).toContain('active');
  });
});
