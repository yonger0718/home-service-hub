import { Type } from '@angular/core';

export type AccountingPage =
  | 'timeline'
  | 'accounts'
  | 'accountEntries'
  | 'accountSettings'
  | 'entryForm'
  | 'entryDetail'
  | 'settings'
  | 'reminders';

/** Pages that act as the left-hand list in the two-pane layout (Task 21). */
export type AccountingListKey = Extract<AccountingPage, 'timeline' | 'accounts'>;

type PageLoader = () => Promise<Type<unknown>>;

/**
 * Lazy loaders for every accounting page, shared by `app.routes.ts` and the layout's list pane.
 * Every page is a real component; the layout reuses the list loaders for its left pane.
 */
export const ACCOUNTING_PAGES: Record<AccountingPage, PageLoader> = {
  timeline: () => import('./timeline/timeline').then(m => m.LedgerTimelineComponent),
  accounts: () => import('./accounts/accounts').then(m => m.AccountingAccountsComponent),
  accountEntries: () => import('./account-entries/account-entries').then(m => m.AccountingAccountEntriesComponent),
  accountSettings: () => import('./account-settings/account-settings').then(m => m.AccountSettingsComponent),
  entryForm: () => import('./entry-form/entry-form').then(m => m.EntryFormComponent),
  entryDetail: () => import('./entry-detail/entry-detail').then(m => m.EntryDetailComponent),
  settings: () => import('./accounting-settings/accounting-settings').then(m => m.AccountingSettingsComponent),
  reminders: () => import('./reminders/reminders').then(m => m.AccountingRemindersComponent),
};
