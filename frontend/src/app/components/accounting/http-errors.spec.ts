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
    expect(writeErrorMessage(httpError(409, { detail: 'definition_ended' }))).toBe('排程已結束，無法編輯');
    expect(writeErrorMessage(httpError(409, { code: 'conflict', message: 'definition_ended' }))).toBe('排程已結束，無法編輯');
    // Final review F4: the posting race codes read in Chinese.
    expect(writeErrorMessage(httpError(409, { detail: 'already_posted' }))).toBe('此期已入帳');
    expect(writeErrorMessage(httpError(409, { detail: 'skipped' }))).toBe('此期已略過');
    expect(writeErrorMessage(httpError(409, { detail: 'definition_changed' }))).toBe('排程剛變更，請重試');
    expect(writeErrorMessage(httpError(409, { detail: 'busy' }))).toBe('排程工作正在執行，請稍後再試');
    expect(writeErrorMessage(httpError(409, { code: 'conflict', message: 'already_posted' }))).toBe('此期已入帳');
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

  it.each([
    ['retry', '記錄剛被更新，請重新載入後再試'],
    ['member_locked', '子項已受保護，請重新載入後再試'],
    ['already_grouped', '此記錄已屬於群組，請重新載入'],
    ['entry_locked', '此記錄目前不能拆帳'],
    ['kind_not_splittable', '此記錄類型不能拆帳'],
    ['import_running', '匯入進行中，請稍後再試'],
    ['group_scheduled', '排程產生的群組不可在此修改'],
    ['locked_until_cutover', 'MOZE 匯入資料，切換後可編輯'],
  ])('translates the real shared-lib 409 envelope: %s', (message, translated) => {
    expect(writeErrorMessage(httpError(409, { code: 'conflict', message, trace_id: 'test-trace' }))).toBe(translated);
  });

  it.each([
    [409, 'retry: member 7 changed', '記錄剛被更新，請重新載入後再試'],
    [409, 'member_locked: member 7 (settlement)', '子項已受保護，請重新載入後再試'],
    [409, 'already_grouped: entry 7 belongs to group 4', '此記錄已屬於群組，請重新載入'],
    [409, 'kind_not_splittable: entry 7 is listed by a posted schedule period', '此記錄類型不能拆帳'],
    [409, 'group_scheduled: instance 77', '排程產生的群組不可在此修改'],
    [409, 'locked_until_cutover: entry 7', 'MOZE 匯入資料，切換後可編輯'],
    [404, 'member_not_found: member 7', '找不到群組中的子項，請重新載入'],
  ] as const)('reads message prefix for HTTP %i: %s', (status, message, expected) => {
    expect(writeErrorMessage(httpError(status, {
      code: status === 404 ? 'NOT_FOUND' : 'CONFLICT', message, trace_id: 'test',
    }))).toBe(expected);
  });

  it('does not treat an arbitrary prefix as a known conflict code', () => {
    expect(writeErrorMessage(httpError(409, { code: 'CONFLICT', message: 'retrying is unavailable', trace_id: 'test' })))
      .toBe('retrying is unavailable');
    // A business code on another status is not a conflict translation.
    expect(writeErrorMessage(httpError(422, { code: 'X', message: 'retry: no' }))).toBe('retry: no');
  });
});
