import { Routes } from '@angular/router';
import { ACCOUNTING_PAGES, AccountingPage } from './components/accounting/accounting-pages';
import { phoneLayoutGuard, wideLayoutGuard } from './services/layout-mode.service';

/** Below 760 px: one screen per route, in the layout's primary outlet. */
const ACCOUNTING_PHONE_ROUTES: Routes = [
  { path: '', pathMatch: 'full', loadComponent: ACCOUNTING_PAGES.timeline },
  { path: 'accounts', loadComponent: ACCOUNTING_PAGES.accounts },
  { path: 'accounts/new', loadComponent: ACCOUNTING_PAGES.accountSettings },
  { path: 'accounts/:id/settings', loadComponent: ACCOUNTING_PAGES.accountSettings },
  // An entry opened from a passbook keeps the passbook in its URL (✕ / delete return there; ↑ ↓ stay in it).
  { path: 'accounts/:id/entries/:eid', loadComponent: ACCOUNTING_PAGES.entryDetail },
  { path: 'accounts/:id', loadComponent: ACCOUNTING_PAGES.accountEntries },
  { path: 'entry', loadComponent: ACCOUNTING_PAGES.entryForm },
  { path: 'entries/:id/edit', loadComponent: ACCOUNTING_PAGES.entryForm },
  { path: 'entries/:id', loadComponent: ACCOUNTING_PAGES.entryDetail },
  { path: 'settings', loadComponent: ACCOUNTING_PAGES.settings },
  { path: 'reminders', loadComponent: ACCOUNTING_PAGES.reminders },
];

/** The page shown in the layout's `pane` outlet; an empty-path named child keeps the URL free of `(pane:…)`. */
function pane(page: AccountingPage): Routes {
  return [{ path: '', outlet: 'pane', loadComponent: ACCOUNTING_PAGES[page] }];
}

/** 760 px and wider: the layout renders `data.list` on the left and the `pane` outlet on the right. */
const ACCOUNTING_WIDE_ROUTES: Routes = [
  { path: '', pathMatch: 'full', data: { list: 'timeline' }, children: [] },
  { path: 'entry', data: { list: 'timeline' }, children: pane('entryForm') },
  { path: 'entries/:id/edit', data: { list: 'timeline' }, children: pane('entryForm') },
  { path: 'entries/:id', data: { list: 'timeline' }, children: pane('entryDetail') },
  { path: 'reminders', data: { list: 'timeline' }, children: pane('reminders') },
  { path: 'accounts', pathMatch: 'full', data: { list: 'accounts' }, children: [] },
  { path: 'accounts/new', data: { list: 'accounts' }, children: pane('accountSettings') },
  { path: 'accounts/:id/settings', data: { list: 'accounts' }, children: pane('accountSettings') },
  { path: 'accounts/:id/entries/:eid', data: { list: 'accounts' }, children: pane('entryDetail') },
  { path: 'accounts/:id', data: { list: 'accounts' }, children: pane('accountEntries') },
  { path: 'settings', loadComponent: ACCOUNTING_PAGES.settings },
];

const ACCOUNTING_ROUTES: Routes = [
  { path: '', canMatch: [phoneLayoutGuard], children: ACCOUNTING_PHONE_ROUTES },
  { path: '', canMatch: [wideLayoutGuard], children: ACCOUNTING_WIDE_ROUTES },
];

export const routes: Routes = [
  { path: '', loadComponent: () => import('./components/item-list/item-list').then(m => m.ItemListComponent) },
  { path: 'shopping-list', loadComponent: () => import('./components/shopping-list/shopping-list').then(m => m.ShoppingListComponent) },
  { path: 'settings', loadComponent: () => import('./components/settings/settings').then(m => m.SettingsComponent) },
  
  // Portfolio routes
  { path: 'portfolio', loadComponent: () => import('./components/portfolio/dashboard/dashboard').then(m => m.PortfolioDashboardComponent) },
  { path: 'portfolio/transactions', loadComponent: () => import('./components/portfolio/transaction-list/transaction-list').then(m => m.PortfolioTransactionListComponent) },
  { path: 'portfolio/dividends', loadComponent: () => import('./components/portfolio/dividend-list/dividend-list').then(m => m.PortfolioDividendListComponent) },
  { path: 'portfolio/realized-pnl', loadComponent: () => import('./components/portfolio/realized-pnl/realized-pnl').then(m => m.PortfolioRealizedPnlComponent) },
  { path: 'portfolio/accounts', loadComponent: () => import('./components/portfolio/accounts/accounts-list').then(m => m.PortfolioAccountsListComponent) },
  { path: 'portfolio/accounts/:id', loadComponent: () => import('./components/portfolio/accounts/account-detail').then(m => m.PortfolioAccountDetailComponent) },
  { path: 'portfolio/import', loadComponent: () => import('./components/portfolio/import/import').then(m => m.PortfolioImportComponent) },
  { path: 'portfolio/import-broker', loadComponent: () => import('./components/portfolio/broker-import/broker-import').then(m => m.PortfolioBrokerImportComponent) },

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

  { path: '**', redirectTo: '' }
];
