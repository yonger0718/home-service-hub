import { describe, expect, it } from 'vitest';

import { NativeAmountPipe } from './native-amount.pipe';

describe('NativeAmountPipe', () => {
  const pipe = new NativeAmountPipe();

  it('formats GBp with four decimals and suffix', () => {
    expect(pipe.transform(8050, 'GBp')).toBe('8050.0000 GBp');
  });

  it('formats USD with two decimals and suffix', () => {
    expect(pipe.transform(190.5, 'USD')).toBe('190.50 USD');
  });

  it('omits the suffix for TWD', () => {
    expect(pipe.transform(590, 'TWD')).toBe('590.00');
  });

  it('returns a dash for null values', () => {
    expect(pipe.transform(null, 'USD')).toBe('—');
  });
});
