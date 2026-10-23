import { describe, expect, it } from 'vitest';

import { MARKET_LABELS, isForeign, marketLabel } from './market';

describe('market', () => {
  it('labels every market code', () => {
    expect(MARKET_LABELS).toEqual({ TW: '台股', US: '美股', LSE: '英股' });
  });

  it('labels a code, falls back to the code itself, and to empty for null', () => {
    expect(marketLabel('TW')).toBe('台股');
    expect(marketLabel('US')).toBe('美股');
    expect(marketLabel('LSE')).toBe('英股');
    expect(marketLabel('XX')).toBe('XX');
    expect(marketLabel(null)).toBe('');
    expect(marketLabel(undefined)).toBe('');
  });

  it('treats every market but TW as foreign', () => {
    expect(isForeign('TW')).toBe(false);
    expect(isForeign('US')).toBe(true);
    expect(isForeign('LSE')).toBe(true);
    expect(isForeign('XX')).toBe(true);
    expect(isForeign(null)).toBe(false);
    expect(isForeign(undefined)).toBe(false);
    expect(isForeign('')).toBe(false);
  });
});
