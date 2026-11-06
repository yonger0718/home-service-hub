import { describe, expect, it } from 'vitest';

import { invoiceAmount } from './einvoice-format';

describe('invoiceAmount', () => {
  it('formats an integral amount as TWD', () => {
    expect(invoiceAmount('120.0000')).toBe('$120');
  });

  it('keeps a fractional amount as a plain 2-decimal number', () => {
    expect(invoiceAmount('20.9900')).toBe('20.99');
    expect(invoiceAmount('1234.5000')).toBe('1,234.50');
  });
});
