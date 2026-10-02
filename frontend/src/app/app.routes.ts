import { Routes } from '@angular/router';
import { ACCOUNTING_PAGES } from './components/accounting/accounting-pages';

/** One screen per route. Task 21 keeps this table for phones and adds a two-pane table for wider screens. */
const ACCOUNTING_ROUTES: Routes = [
  { path: '', pathMatch: 'full', loadComponent: ACCOUNTING_PAGES.timeline },
  { path: 'accounts', loadComponent: ACCOUNTING_PAGES.accounts },
  { path: 'accounts/new', loadComponent: ACCOUNTING_PAGES.accountSettings },
  { path: 'accounts/:id/settings', loadComponent: ACCOUNTING_PAGES.accountSettings },
  { path: 'accounts/:id', loadComponent: ACCOUNTING_PAGES.accountEntries },
  { path: 'entry', loadComponent: ACCOUNTING_PAGES.entryForm },
  { path: 'entries/:id/edit', loadComponent: ACCOUNTING_PAGES.entryForm },
  { path: 'entries/:id', loadComponent: ACCOUNTING_PAGES.entryDetail },
  { path: 'settings', loadComponent: ACCOUNTING_PAGES.settings },
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
