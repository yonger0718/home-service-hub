// frontend/src/app/components/accounting/account-picker/recent-accounts.ts
/** This device's recently used accounts (ids, most recent first), written by the hosts after a successful save. */
export const RECENT_ACCOUNTS_KEY = 'hh.accounting.recentAccounts';
export const RECENT_ACCOUNTS_MAX = 8;

export function readRecentAccounts(): number[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(RECENT_ACCOUNTS_KEY) ?? '[]');
    return Array.isArray(parsed) ? parsed.filter((id): id is number => Number.isInteger(id)) : [];
  } catch {
    // Blocked storage or garbage: no recent row.
    return [];
  }
}

export function rememberRecentAccounts(ids: readonly (number | null | undefined)[]): void {
  const fresh = ids.filter((id): id is number => typeof id === 'number' && Number.isInteger(id));
  if (fresh.length === 0) {
    return;
  }
  const next = [...new Set([...fresh, ...readRecentAccounts()])].slice(0, RECENT_ACCOUNTS_MAX);
  try {
    localStorage.setItem(RECENT_ACCOUNTS_KEY, JSON.stringify(next));
  } catch {
    // Storage blocked (private mode): the list simply is not remembered.
  }
}
