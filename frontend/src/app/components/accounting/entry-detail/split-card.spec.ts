import { expect, it } from 'vitest';

import { makeEntry } from '../testing/fixtures';
import { storedGroupNet } from './split-card';

it('nets only stored amounts in their currencies', () => {
  expect(storedGroupNet([
    makeEntry({ amount: '-100', currency: 'TWD' }),
    makeEntry({ kind: 'receivable', is_settlement: true, amount: '40', currency: 'TWD' }),
    makeEntry({ amount: '-8', currency: 'USD', original_currency: 'JPY', original_amount: '1000' }),
  ])).toEqual([{ currency: 'TWD', amount: -60 }, { currency: 'USD', amount: -8 }]);
});
