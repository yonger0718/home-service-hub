import { describe, expect, it } from 'vitest';

import { holdingKey } from './portfolio.model';

describe('holdingKey', () => {
  it('keeps the same symbol distinct across markets', () => {
    expect(holdingKey({ symbol: 'AAPL', market: 'US' })).toBe('AAPL|US');
    expect(holdingKey({ symbol: 'AAPL', market: 'TW' })).toBe('AAPL|TW');
  });

  it('requires market at compile time', () => {
    type HoldingKeyInput = Parameters<typeof holdingKey>[0];

    // @ts-expect-error bare-symbol holdings are not valid key inputs
    const bareSymbolOnly: HoldingKeyInput = { symbol: 'AAPL' };

    expect(bareSymbolOnly.symbol).toBe('AAPL');
  });
});
