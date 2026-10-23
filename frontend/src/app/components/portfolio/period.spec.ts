import { describe, expect, it } from 'vitest';

import { periodLabel } from './period';

describe('periodLabel', () => {
  it('is 全部期間 without date bounds', () => {
    expect(periodLabel({})).toBe('全部期間');
    expect(periodLabel({ date_from: null, date_to: '' })).toBe('全部期間');
  });

  it('formats a full range with an en dash', () => {
    expect(periodLabel({ date_from: '2026-01-01', date_to: '2026-10-22' })).toBe('2026/01/01 – 2026/10/22');
  });

  it('formats an open-ended range', () => {
    expect(periodLabel({ date_from: '2026-01-01' })).toBe('2026/01/01 起');
    expect(periodLabel({ date_to: '2026-10-22' })).toBe('至 2026/10/22');
  });
});
