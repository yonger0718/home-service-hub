// frontend/src/app/components/accounting/account-picker/recent-accounts.spec.ts
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RECENT_ACCOUNTS_KEY, readRecentAccounts, rememberRecentAccounts } from './recent-accounts';

describe('recent accounts', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.removeItem(RECENT_ACCOUNTS_KEY);
  });

  it('keeps the most recent first, without duplicates, at most 8', () => {
    rememberRecentAccounts([3]);
    rememberRecentAccounts([5, 7]);
    rememberRecentAccounts([3, null, undefined]);
    expect(readRecentAccounts()).toEqual([3, 5, 7]);
    rememberRecentAccounts([10, 11, 12, 13, 14, 15]);
    expect(readRecentAccounts()).toEqual([10, 11, 12, 13, 14, 15, 3, 5]);
  });

  it('reads garbage as empty', () => {
    localStorage.setItem(RECENT_ACCOUNTS_KEY, '{"not": "a list"}');
    expect(readRecentAccounts()).toEqual([]);
    localStorage.setItem(RECENT_ACCOUNTS_KEY, '[1, "x", 2.5, 4]');
    expect(readRecentAccounts()).toEqual([1, 4]);
    localStorage.setItem(RECENT_ACCOUNTS_KEY, 'nope');
    expect(readRecentAccounts()).toEqual([]);
  });

  it('tolerates blocked storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(readRecentAccounts()).toEqual([]);
    expect(() => rememberRecentAccounts([1])).not.toThrow();
  });
});
