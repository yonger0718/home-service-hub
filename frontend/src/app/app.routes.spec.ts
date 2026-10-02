import { describe, expect, it } from 'vitest';

import { routes } from './app.routes';
import { NAV_GROUPS, NAV_ITEMS } from './components/shell/navigation';

const REMOVED_ACCOUNTING_PAGES = ['dashboard', 'transactions', 'settings', 'cards', 'categories', 'recurring'];

describe('accounting routes', () => {
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
});
