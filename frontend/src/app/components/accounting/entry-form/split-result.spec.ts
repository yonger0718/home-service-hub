import { describe, expect, it } from 'vitest';

import { newChild } from './split-draft';
import { memberError, resultIds } from './split-result';

describe('split results', () => {
  it('maps by client key even when response array differs', () => {
    const a = newChild();
    const b = newChild();
    const ids = resultIds({ group_id: 4, member_ids: [7, 8], members: [
      { id: 8, client_key: b.key }, { id: 7, client_key: a.key },
    ] }, [a, b]);
    expect(ids.get(a.key)).toBe(7);
    expect(ids.get(b.key)).toBe(8);
  });

  it('routes indexed errors using submitted keys', () => {
    expect(memberError({ error: { detail: [{ loc: ['body', 'members', 1, 'amount'], msg: 'must be positive' }] } }, ['a', 'b']))
      .toEqual({ key: 'b', field: 'amount', message: 'must be positive' });
    expect(memberError({ error: { detail: { 'members.1.amount': 'invalid' } } }, ['a', 'b']))
      .toEqual({ key: 'b', field: 'amount', message: 'invalid' });
    // The service's own ValidationError arrives as one dotted loc string.
    expect(memberError({ error: { detail: [{ loc: ['members.0.counterparty_id'], msg: 'required' }] } }, ['a', 'b']))
      .toEqual({ key: 'a', field: 'counterparty_id', message: 'required' });
    expect(memberError({ error: { code: 'conflict', message: 'retry' } }, ['a', 'b'])).toBeNull();
    expect(memberError({ error: { detail: [{ loc: ['body', 'members', 5, 'amount'], msg: 'x' }] } }, ['a', 'b'])).toBeNull();
  });

  it('normalizes discriminated member loc before field routing', () => {
    expect(memberError({ error: { detail: [
      { loc: ['body', 'members', 1, 'SplitMemberIn', 'amount'], msg: 'invalid amount' },
    ] } }, ['a', 'b'])).toEqual({ key: 'b', field: 'amount', message: 'invalid amount' });
  });

  it('rejects missing/duplicate keys instead of navigating to a wrong row', () => {
    const a = newChild();
    const b = newChild();
    expect(() => resultIds({ group_id: null, member_ids: [7], members: [{ id: 7, client_key: null }] }, [a])).toThrow();
    expect(() => resultIds({ group_id: 4, member_ids: [7, 8], members: [
      { id: 7, client_key: a.key }, { id: 8, client_key: a.key },
    ] }, [a, b])).toThrow();
    expect(() => resultIds({ group_id: 4, member_ids: [7], members: [{ id: 7, client_key: a.key }] }, [a, b])).toThrow();
  });
});
