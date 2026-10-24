import { describe, expect, it } from 'vitest';

import { Amount, MoneyOptions, formatMoney, formatPct } from './format';

describe('formatMoney', () => {
  const rows: [Amount, MoneyOptions, string][] = [
    [3021869.34, {}, '$3,021,869'],
    [3021869.34, { symbol: false }, '3,021,869'],
    [292227.2, { sign: 'change' }, '+$292,227'],
    [1326, { currency: 'USD' }, '1,326.00 USD'],
    [1326, { currency: 'USD', sign: 'change' }, '+1,326.00 USD'],
    [5390.4, { currency: 'JPY' }, '5,390 JPY'],
    [35205, { sign: 'change' }, '+$35,205'],
    [448.5, { sign: 'change' }, '+$449'],
    [-4076.5, { sign: 'change' }, '−$4,077'],
    [-4076, {}, '−$4,076'],
    [4076, {}, '$4,076'],
    [-3021869, { symbol: false }, '−3,021,869'],
    [-0.4, { sign: 'change' }, '$0'],
    [0, { currency: 'USD', sign: 'change' }, '0.00 USD'],
    ['150000', {}, '$150,000'],
    [1234.5, { currency: 'twd' }, '$1,235'],
    [12.345, { currency: 'GBp' }, '12.35 GBp'],
    [null, {}, '—'],
    ['', {}, '—'],
    ['abc', {}, '—'],
  ];

  it.each(rows)('formatMoney(%s, %o) is %s', (value, options, expected) => {
    expect(formatMoney(value, options)).toBe(expected);
  });
});

describe('formatPct', () => {
  const rows: [Amount, { sign?: 'change'; digits?: number }, string][] = [
    [12.345, {}, '12.3%'],
    [123.4, {}, '123%'],
    [-2.35, { sign: 'change' }, '−2.4%'],
    [2.35, { sign: 'change' }, '+2.4%'],
    [2.35, {}, '2.4%'],
    [-0.04, { sign: 'change' }, '0.0%'],
    [99.95, {}, '100%'],
    [99.94, {}, '99.9%'],
    [100, { digits: 2 }, '100.00%'],
    [0, {}, '0.0%'],
    [0.27, { digits: 2 }, '0.27%'],
    [0.274, { digits: 2 }, '0.27%'],
    [null, {}, '—'],
  ];

  it.each(rows)('formatPct(%s, %o) is %s', (value, options, expected) => {
    expect(formatPct(value, options)).toBe(expected);
  });
});
