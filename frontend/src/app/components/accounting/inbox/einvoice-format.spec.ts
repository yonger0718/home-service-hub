import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';

import { einvoiceErrorText, invoiceAmount } from './einvoice-format';
import { INBOX_GENERIC_ERROR } from './inbox-view';

describe('invoiceAmount', () => {
  it('formats an integral amount as TWD', () => {
    expect(invoiceAmount('120.0000')).toBe('$120');
  });

  it('keeps a fractional amount as a plain 2-decimal number', () => {
    expect(invoiceAmount('20.9900')).toBe('20.99');
    expect(invoiceAmount('1234.5000')).toBe('1,234.50');
  });
});

describe('einvoiceErrorText', () => {
  const err = (detail: unknown) =>
    new HttpErrorResponse({ status: 409, error: { detail } });

  it('maps the 409 codes, with or without a detail suffix', () => {
    expect(einvoiceErrorText(err('einvoice_void'))).toBe('沒成功：發票已作廢');
    expect(einvoiceErrorText(err('not_linked'))).toBe('沒成功：已解除連結');
    expect(einvoiceErrorText(err('seller_muted: x'))).toBe('沒成功：這家已設為不提醒');
    expect(einvoiceErrorText(err('retry'))).toBe('沒成功：請再試一次');
  });

  it('falls back to the generic text', () => {
    expect(einvoiceErrorText(err('???'))).toBe(INBOX_GENERIC_ERROR);
    expect(einvoiceErrorText(new Error('x'))).toBe(INBOX_GENERIC_ERROR);
  });
});
