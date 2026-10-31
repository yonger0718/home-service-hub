import { inject } from '@angular/core';
import { Routes, UrlMatchResult, UrlSegment } from '@angular/router';
import { ACCOUNTING_PAGES, AccountingPage } from './components/accounting/accounting-pages';
import { landingPathFor } from './components/shell/navigation';
import { phoneLayoutGuard, wideLayoutGuard } from './services/layout-mode.service';
import { NavigationPreferencesService } from './services/navigation-preferences.service';

/**
 * Every deep-linkable accounting path must also match Caddy's `@hub_spa` matcher (docs/deploy). PR-9 adds the 多類別
 * group view: `/accounting/entries/[0-9]+/group` and `/accounting/accounts/[0-9]+/entries/[0-9]+/group`.
 */

/**
 * Pages with their own URL below the list: one screen per route below 760 px, the layout's `pane` outlet beside
 * `list` from 760 px. Both route tables are derived from this one list; order matters (more specific paths first).
 */
const PANE_PAGES: readonly { path: string; list: 'timeline' | 'accounts'; page: AccountingPage }[] = [
  { path: 'accounts/new', list: 'accounts', page: 'accountSettings' },
  { path: 'accounts/:id/settings', list: 'accounts', page: 'accountSettings' },
  { path: 'accounts/:id/statements/:sid', list: 'accounts', page: 'statementDetail' },
  { path: 'accounts/:id/statements', list: 'accounts', page: 'statementList' },
  // An entry opened from a passbook keeps the passbook in its URL (✕ / delete return there; ↑ ↓ stay in it).
  { path: 'accounts/:id/entries/:eid/group', list: 'accounts', page: 'splitGroup' },
  { path: 'accounts/:id/entries/:eid', list: 'accounts', page: 'entryDetail' },
  { path: 'accounts/:id', list: 'accounts', page: 'accountEntries' },
  { path: 'entry', list: 'timeline', page: 'entryForm' },
  { path: 'entries/:id/edit', list: 'timeline', page: 'entryForm' },
  // The 多類別 group view, routed by one of its members (the API reads a split through a member).
  { path: 'entries/:id/group', list: 'timeline', page: 'splitGroup' },
  { path: 'entries/:id', list: 'timeline', page: 'entryDetail' },
  { path: 'reminders', list: 'timeline', page: 'reminders' },
];

/** Below 760 px: one screen per route, in the layout's primary outlet. */
const ACCOUNTING_PHONE_ROUTES: Routes = [
  { path: '', pathMatch: 'full', loadComponent: ACCOUNTING_PAGES.timeline },
  { path: 'accounts', pathMatch: 'full', loadComponent: ACCOUNTING_PAGES.accounts },
  ...PANE_PAGES.map(({ path, page }) => ({ path, loadComponent: ACCOUNTING_PAGES[page] })),
  { path: 'settings', loadComponent: ACCOUNTING_PAGES.settings },
  { path: 'reports', loadComponent: ACCOUNTING_PAGES.reports },
  { path: 'reports/custom/new', loadComponent: ACCOUNTING_PAGES.customReport },
  { path: 'reports/custom/:id', loadComponent: ACCOUNTING_PAGES.customReport },
];

/** The page shown in the layout's `pane` outlet; an empty-path named child keeps the URL free of `(pane:…)`. */
function pane(page: AccountingPage): Routes {
  return [{ path: '', outlet: 'pane', loadComponent: ACCOUNTING_PAGES[page] }];
}

/** 760 px and wider: the layout renders `data.list` on the left and the `pane` outlet on the right. */
const ACCOUNTING_WIDE_ROUTES: Routes = [
  { path: '', pathMatch: 'full', data: { list: 'timeline' }, children: [] },
  { path: 'accounts', pathMatch: 'full', data: { list: 'accounts' }, children: [] },
  ...PANE_PAGES.map(({ path, list, page }) => ({ path, data: { list }, children: pane(page) })),
  { path: 'settings', loadComponent: ACCOUNTING_PAGES.settings },
  // 報表 and the saved-report editor are whole pages on every width, like 記帳設定 (no list beside them).
  { path: 'reports', loadComponent: ACCOUNTING_PAGES.reports },
  { path: 'reports/custom/new', loadComponent: ACCOUNTING_PAGES.customReport },
  { path: 'reports/custom/:id', loadComponent: ACCOUNTING_PAGES.customReport },
];

/** The first visible group's page, as the sidebar logo; absolute, so `**` can use it too (redirects do not chain). */
const landingPath = () => landingPathFor(inject(NavigationPreferencesService).hiddenGroups());

const ACCOUNTING_ROUTES: Routes = [
  { path: '', canMatch: [phoneLayoutGuard], children: ACCOUNTING_PHONE_ROUTES },
  { path: '', canMatch: [wideLayoutGuard], children: ACCOUNTING_WIDE_ROUTES },
];

/**
 * `portfolio/accounts` and `portfolio/accounts/:id` as one route (Decision 8), so the master-detail page and its list
 * stay mounted when the selection (or the first auto-selection) changes the URL.
 */
export function portfolioAccountsMatcher(segments: UrlSegment[]): UrlMatchResult | null {
  if (segments.length < 2 || segments.length > 3 || segments[0].path !== 'portfolio' || segments[1].path !== 'accounts') {
    return null;
  }
  return segments.length === 3 ? { consumed: segments, posParams: { id: segments[2] } } : { consumed: segments };
}

export const routes: Routes = [
  // The bare URL (the manifest's start_url) opens the first visible group, as the sidebar logo does.
  { path: '', pathMatch: 'full', redirectTo: landingPath },
  { path: 'inventory', loadComponent: () => import('./components/item-list/item-list').then(m => m.ItemListComponent) },
  { path: 'shopping-list', loadComponent: () => import('./components/shopping-list/shopping-list').then(m => m.ShoppingListComponent) },
  { path: 'settings', loadComponent: () => import('./components/settings/settings').then(m => m.SettingsComponent) },
  
  // Portfolio routes
  { path: 'portfolio', loadComponent: () => import('./components/portfolio/dashboard/dashboard').then(m => m.PortfolioDashboardComponent) },
  { path: 'portfolio/transactions', loadComponent: () => import('./components/portfolio/transaction-list/transaction-list').then(m => m.PortfolioTransactionListComponent) },
  { path: 'portfolio/dividends', loadComponent: () => import('./components/portfolio/dividend-list/dividend-list').then(m => m.PortfolioDividendListComponent) },
  { path: 'portfolio/realized-pnl', loadComponent: () => import('./components/portfolio/realized-pnl/realized-pnl').then(m => m.PortfolioRealizedPnlComponent) },
  { matcher: portfolioAccountsMatcher, loadComponent: () => import('./components/portfolio/accounts/accounts-page').then(m => m.PortfolioAccountsPageComponent) },
  { path: 'portfolio/import', loadComponent: () => import('./components/portfolio/import/import').then(m => m.PortfolioImportComponent) },
  // 券商對帳單 is a type of the unified import page; old links land on it.
  { path: 'portfolio/import-broker', redirectTo: '/portfolio/import?type=broker', pathMatch: 'full' },

  // Accounting routes: phase 1 bookmarks land on the timeline; every page lives under the accounting layout.
  { path: 'accounting/dashboard', redirectTo: 'accounting' },
  { path: 'accounting/transactions', redirectTo: 'accounting' },
  { path: 'accounting/cards', redirectTo: 'accounting' },
  { path: 'accounting/categories', redirectTo: 'accounting' },
  { path: 'accounting/recurring', redirectTo: 'accounting' },
  {
    path: 'accounting',
    loadComponent: () => import('./components/accounting/accounting-layout/accounting-layout').then(m => m.AccountingLayoutComponent),
    children: ACCOUNTING_ROUTES,
  },

  { path: '**', redirectTo: landingPath }
];
