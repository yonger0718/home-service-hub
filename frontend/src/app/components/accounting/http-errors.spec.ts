import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';

import { fieldErrors, writeErrorMessage } from './http-errors';

function httpError(status: number, error: unknown): HttpErrorResponse {
  return new HttpErrorResponse({ status, error, url: '/api/accounting/x' });
}

describe('http error helpers', () => {
  it('maps a 422 body to field messages using the last string of loc', () => {
    const err = httpError(422, { detail: [{ loc: ['body', 'credit_limit'], msg: 'requires is_credit' }, { loc: ['currency'], msg: 'frozen' }] });
    expect(fieldErrors(err)).toEqual({ credit_limit: 'requires is_credit', currency: 'frozen' });
  });

  it('maps the service 422 body (single field loc, value_error type) and dotted split-member locs', () => {
    const err = httpError(422, {
      detail: [
        { loc: ['amount'], msg: 'more than 4 decimals', type: 'value_error' },
        { loc: ['members.1.account_id'], msg: 'unknown account', type: 'value_error' },
      ],
    });
    expect(fieldErrors(err)).toEqual({ amount: 'more than 4 decimals', 'members.1.account_id': 'unknown account' });
  });

  it('keeps a shared_lib-shaped 422 message under the "_" key and ignores other statuses', () => {
    expect(fieldErrors(httpError(422, { code: 'unprocessable', message: 'bad input', trace_id: 't-1' }))).toEqual({ _: 'bad input' });
    expect(fieldErrors(httpError(500, 'boom'))).toEqual({});
  });

  it('explains the cutover lock, shared_lib messages, string details and validation messages', () => {
    expect(writeErrorMessage(httpError(409, { code: 'conflict', message: 'locked_until_cutover', trace_id: 't-1' }))).toBe(
      'MOZE 匯入資料，切換後可編輯',
    );
    expect(writeErrorMessage(httpError(409, { detail: 'locked_until_cutover' }))).toBe('MOZE 匯入資料，切換後可編輯');
    expect(
      writeErrorMessage(httpError(409, { code: 'conflict', message: 'account has entries; archive it instead (is_archived)', trace_id: 't-2' })),
    ).toBe('account has entries; archive it instead (is_archived)');
    expect(writeErrorMessage(httpError(409, { detail: 'account has entries; archive it instead (is_archived)' }))).toBe(
      'account has entries; archive it instead (is_archived)',
    );
    expect(writeErrorMessage(httpError(422, { detail: [{ loc: ['amount'], msg: 'exceeds open amount' }] }))).toBe('exceeds open amount');
    expect(writeErrorMessage(httpError(0, null))).toBe('無法連線到伺服器');
    expect(writeErrorMessage(new Error('x'))).toBe('儲存失敗，請稍後再試');
  });
});
