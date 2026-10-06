// frontend/src/app/components/accounting/account-picker/picker-groups.spec.ts
import { describe, expect, it } from 'vitest';

import { AccountGroup } from '../../../models/accounting.model';
import { makeAccount } from '../testing/fixtures';
import { pickerGroups, visibleAccounts } from './picker-groups';

const ACCOUNTS = [
  makeAccount({ id: 1, name: '錢包', group_id: 1, group_name: '現金' }),
  makeAccount({ id: 2, name: '玉山 UNI', group_id: 3, group_name: '信用卡', is_credit: true }),
  makeAccount({ id: 3, name: 'Line Bank', group_id: 2, group_name: '銀行' }),
  makeAccount({ id: 4, name: '舊卡', group_id: 3, group_name: '信用卡', is_archived: true }),
  makeAccount({ id: 5, name: '悠遊卡', group_id: null, group_name: null }),
];
const GROUPS: AccountGroup[] = [
  { id: 3, name: '信用卡', sort_order: 0, moze_id: null },
  { id: 1, name: '現金', sort_order: 1, moze_id: null },
  { id: 2, name: '銀行', sort_order: 2, moze_id: null },
];
const names = (groups: ReturnType<typeof pickerGroups>) => groups.map(group => [group.name, group.accounts.map(a => a.name)]);

describe('pickerGroups', () => {
  it('orders groups by sort_order, then 未分組, then 已封存 when allowed', () => {
    expect(names(pickerGroups(ACCOUNTS, GROUPS, { allowArchived: true, exclude: [], query: '' }))).toEqual([
      ['信用卡', ['玉山 UNI']],
      ['現金', ['錢包']],
      ['銀行', ['Line Bank']],
      ['未分組', ['悠遊卡']],
      ['已封存', ['舊卡']],
    ]);
  });

  it('derives groups from group_name order without AccountGroup data and hides archived by default', () => {
    expect(names(pickerGroups(ACCOUNTS, null, { allowArchived: false, exclude: [], query: '' }))).toEqual([
      ['現金', ['錢包']],
      ['信用卡', ['玉山 UNI']],
      ['銀行', ['Line Bank']],
      ['未分組', ['悠遊卡']],
    ]);
  });

  it('hides excluded ids and filters by name or group name, ignoring case', () => {
    const filter = { allowArchived: false, exclude: [1], query: '' };
    expect(visibleAccounts(ACCOUNTS, filter).map(a => a.id)).toEqual([2, 3, 5]);
    expect(names(pickerGroups(ACCOUNTS, null, { ...filter, query: 'line' }))).toEqual([['銀行', ['Line Bank']]]);
    expect(names(pickerGroups(ACCOUNTS, null, { ...filter, query: '信用' }))).toEqual([['信用卡', ['玉山 UNI']]]);
    expect(pickerGroups(ACCOUNTS, null, { ...filter, query: 'zzz' })).toEqual([]);
  });
});
