import { RewardBatch } from '../../models/accounting.model';
import { makeEntry } from './testing/fixtures';
import { batchKey, batchSub, foldPassbook, loadedBatches } from './reward-batch';

function batch(overrides: Partial<RewardBatch> = {}): RewardBatch {
  return {
    rule_id: 301, rule_name: '測試規則', window_start: '2026-10-07', window_end: '2026-11-06', entry_date: '2026-11-06',
    count: 3, total: '10.0000', currency: 'TWD', ...overrides,
  };
}

function reward(id: number, amount: string, running: string | null, b: RewardBatch | null = batch()) {
  return makeEntry({ id, kind: 'reward', amount, running_balance: running, entry_date: b?.entry_date ?? '2026-11-06', reward_batch: b });
}

describe('reward batches (#304)', () => {
  it('keys a row by rule, window and posting day; rows without a batch have none', () => {
    expect(batchKey(reward(1, '2.0000', null))).toBe('b301|2026-10-07|2026-11-06|2026-11-06');
    expect(batchKey(makeEntry({ id: 2 }))).toBeNull();
    expect(batchKey(makeEntry({ id: 3, reward_batch: null }))).toBeNull();
  });

  it('folds only batches with at least two loaded members', () => {
    const lone = batch({ rule_id: 302, count: 4 });
    const loaded = loadedBatches([reward(1, '2.0000', null), reward(2, '3.0000', null), reward(3, '9.0000', null, lone)]);
    expect([...loaded.keys()]).toEqual(['b301|2026-10-07|2026-11-06|2026-11-06']);
    expect(loaded.get('b301|2026-10-07|2026-11-06|2026-11-06')!.map(e => e.id)).toEqual([1, 2]);
  });

  it('describes a batch by its whole count and window', () => {
    expect(batchSub(batch())).toBe('3 筆 · 10/07–11/06');
    expect(batchSub(batch({ window_start: '2026-12-25', window_end: '2027-01-24' }))).toBe('3 筆 · 12/25–1/24');
  });

  it('folds a passbook day showing only real balances: each folded row the balance after its newest member', () => {
    const other = batch({ rule_id: 302, total: '20.0000', count: 2 });
    const items = foldPassbook([
      reward(1, '2.0000', '130.0000'),
      reward(2, '10.0000', '128.0000', other),
      reward(3, '3.0000', '118.0000'),
      reward(4, '10.0000', '115.0000', other),
      reward(5, '5.0000', '105.0000'),
      makeEntry({ id: 6, amount: '-40.0000', running_balance: '100.0000', entry_date: '2026-11-05' }),
    ]);
    expect(items.map(i => [i.kind, i.kind === 'batch' ? i.members.map(m => m.id) : i.entry.id, i.balance])).toEqual([
      ['batch', [1, 3, 5], '130.0000'],
      ['batch', [2, 4], '128.0000'],
      ['entry', 6, '100.0000'],
    ]);
  });

  it('keeps plain rows and a one-member batch as entries with their own balance', () => {
    const items = foldPassbook([reward(1, '2.0000', '12.0000'), makeEntry({ id: 2, running_balance: '10.0000', entry_date: '2026-11-06' })]);
    expect(items.map(i => [i.kind, i.balance])).toEqual([['entry', '12.0000'], ['entry', '10.0000']]);
  });

  it('lists every row unfolded with server balances when told not to fold (a filter may cut a batch)', () => {
    const items = foldPassbook([reward(1, '2.0000', '12.0000'), reward(2, '3.0000', '10.0000')], false);
    expect(items.map(i => [i.kind, i.balance])).toEqual([['entry', '12.0000'], ['entry', '10.0000']]);
  });

  it('shows no balance on a folded row whose newest member has none', () => {
    const items = foldPassbook([reward(1, '2.0000', null), reward(2, '3.0000', '8.0000')]);
    expect(items.map(i => [i.kind, i.balance])).toEqual([['batch', null]]);
  });
});
