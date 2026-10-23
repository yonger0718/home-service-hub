import { describe, expect, it } from 'vitest';

import { ALLOCATION_SERIES, abbrInk, holdingAbbr } from './dashboard/dashboard-math';
import { squareToken, stockSquare } from './square';

describe('square', () => {
  it('picks a series token by a deterministic hash of the symbol', () => {
    const symbols = ['2330', '0050', '2317', '00878', 'AAPL', 'VWRA.L'];
    for (const symbol of symbols) {
      expect(ALLOCATION_SERIES).toContain(squareToken(symbol));
      expect(squareToken(symbol)).toBe(squareToken(symbol));
    }
    expect(new Set(symbols.map(squareToken)).size).toBeGreaterThan(1);
    expect(squareToken('')).toBe(ALLOCATION_SERIES[5381 % ALLOCATION_SERIES.length]);
  });

  it('builds the row square from holdingAbbr, squareToken and abbrInk', () => {
    const token = squareToken('00878');
    expect(stockSquare('00878', '國泰永續高股息')).toEqual({ text: holdingAbbr('00878', '國泰永續高股息'), token, ink: abbrInk(token) });
    expect(stockSquare('AAPL', null).text).toBe('A');
  });
});
