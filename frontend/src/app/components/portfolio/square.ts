import { ALLOCATION_SERIES, abbrInk, holdingAbbr } from './dashboard/dashboard-math';

/** A stock row's initials square: text, fill token and the ink that stays readable on it. */
export interface StockSquare {
  text: string;
  token: string;
  ink: 'light' | 'dark';
}

/**
 * Square fill for a symbol: a djb2-style hash of the symbol's UTF-16 code units mod the series length.
 * Deterministic and page-independent (the same symbol always gets the same fill); unlike the dashboard,
 * which assigns the series by allocation rank, so a symbol's colour may differ between the pages.
 */
export function squareToken(symbol: string): string {
  let hash = 5381;
  for (let i = 0; i < symbol.length; i++) hash = (hash * 33 + symbol.charCodeAt(i)) >>> 0;
  return ALLOCATION_SERIES[hash % ALLOCATION_SERIES.length];
}

/** The transactions and dividends row square: `holdingAbbr` initials on the symbol's `squareToken` fill. */
export function stockSquare(symbol: string, name: string | null | undefined): StockSquare {
  const token = squareToken(symbol);
  return { text: holdingAbbr(symbol, name), token, ink: abbrInk(token) };
}
