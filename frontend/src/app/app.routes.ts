import { Routes } from '@angular/router';

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

  // Accounting routes
  { path: 'accounting', redirectTo: 'accounting/accounts', pathMatch: 'full' },
  { path: 'accounting/dashboard', redirectTo: 'accounting/accounts' },
  { path: 'accounting/transactions', redirectTo: 'accounting/accounts' },
  { path: 'accounting/settings', redirectTo: 'accounting/accounts' },
  { path: 'accounting/cards', redirectTo: 'accounting/accounts' },
  { path: 'accounting/categories', redirectTo: 'accounting/accounts' },
  { path: 'accounting/recurring', redirectTo: 'accounting/accounts' },

  { path: '**', redirectTo: '' }
];
