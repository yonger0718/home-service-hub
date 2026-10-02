import { Type } from '@angular/core';

export type AccountingPage =
  | 'timeline'
  | 'accounts'
  | 'accountEntries'
  | 'accountSettings'
  | 'entryForm'
  | 'entryDetail'
  | 'settings';

/** Pages that act as the left-hand list in the two-pane layout (Task 21). */
export type AccountingListKey = Extract<AccountingPage, 'timeline' | 'accounts'>;

type PageLoader = () => Promise<Type<unknown>>;

const placeholder: PageLoader = () =>
  import('./accounting-placeholder/accounting-placeholder').then(m => m.AccountingPlaceholderComponent);

/**
 * Lazy loaders for every accounting page, shared by `app.routes.ts` and the layout's list pane.
 * Tasks 22–28 each replace their own `placeholder` line with the real component.
 */
export const ACCOUNTING_PAGES: Record<AccountingPage, PageLoader> = {
  timeline: placeholder,
  accounts: () => import('./accounts/accounts').then(m => m.AccountingAccountsComponent),
  accountEntries: () => import('./account-entries/account-entries').then(m => m.AccountingAccountEntriesComponent),
  accountSettings: placeholder,
  entryForm: placeholder,
  entryDetail: placeholder,
  settings: placeholder,
};
