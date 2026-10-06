// frontend/src/app/components/accounting/account-picker/picker-groups.ts
import { AccountGroup, LedgerAccount } from '../../../models/accounting.model';

export const UNGROUPED = '未分組';
export const ARCHIVED_GROUP = '已封存';

export interface PickerGroup {
  key: string;
  name: string;
  accounts: LedgerAccount[];
}

export interface PickerFilter {
  allowArchived: boolean;
  exclude: readonly number[];
  query: string;
}

/** Accounts the picker may offer at all (before the search box narrows them). */
export function visibleAccounts(accounts: LedgerAccount[], filter: Omit<PickerFilter, 'query'>): LedgerAccount[] {
  const excluded = new Set(filter.exclude);
  return accounts.filter(account => !excluded.has(account.id) && (filter.allowArchived || !account.is_archived));
}

/**
 * Picker sections (spec §3.1): `groups` in `sort_order` (or, without them, `group_name` in order of first
 * appearance), then 未分組, then 已封存 (only when allowed). Search matches the account or group name, any case.
 */
export function pickerGroups(accounts: LedgerAccount[], groups: AccountGroup[] | null, filter: PickerFilter): PickerGroup[] {
  const query = filter.query.trim().toLowerCase();
  const matches = (account: LedgerAccount) =>
    !query ||
    account.name.toLowerCase().includes(query) ||
    (account.group_name ?? UNGROUPED).toLowerCase().includes(query);
  const shown = visibleAccounts(accounts, filter).filter(matches);
  const open = shown.filter(account => !account.is_archived);
  const archived = shown.filter(account => account.is_archived);

  const known = new Set((groups ?? []).map(group => group.id));
  const keyOf = (account: LedgerAccount): string => {
    if (groups) {
      return account.group_id !== null && known.has(account.group_id) ? `g${account.group_id}` : 'none';
    }
    return account.group_name ? `n${account.group_name}` : 'none';
  };
  const order: { key: string; name: string }[] = groups
    ? [...groups].sort((a, b) => a.sort_order - b.sort_order).map(group => ({ key: `g${group.id}`, name: group.name }))
    : [];
  if (!groups) {
    for (const account of open) {
      const key = keyOf(account);
      if (key !== 'none' && !order.some(entry => entry.key === key)) {
        order.push({ key, name: account.group_name! });
      }
    }
  }
  order.push({ key: 'none', name: UNGROUPED });

  const sections = order
    .map(entry => ({ ...entry, accounts: open.filter(account => keyOf(account) === entry.key) }))
    .filter(section => section.accounts.length > 0);
  if (archived.length > 0) {
    sections.push({ key: 'archived', name: ARCHIVED_GROUP, accounts: archived });
  }
  return sections;
}
