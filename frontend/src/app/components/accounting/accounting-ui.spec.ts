import { describe, expect, it } from 'vitest';

import {
  KIND_PILLS,
  NO_ENTER_SAVE_TAGS,
  accountLabel,
  colorOf,
  fxConversionLine,
  fxLine,
  iconOf,
  isHandledKey,
  pad,
  shiftMonth,
} from './accounting-ui';
import { makeEntry } from './testing/fixtures';

describe('accounting-ui', () => {
  it('falls back to the default icon table and the neutral colour', () => {
    expect(iconOf(makeEntry({ category_icon: '🍜' }))).toBe('🍜');
    const plain = makeEntry({ category_icon: null, category_color: null, category: null, kind: 'expense' });
    expect(iconOf(plain)).not.toBe('🧾');
    expect(colorOf(plain)).toBe('var(--app-surface-soft)');
    expect(iconOf({ icon: null, color: null, mainName: null, kind: 'receivable' })).toBe(
      iconOf(makeEntry({ category_icon: null, category: null, kind: 'receivable' })),
    );
  });

  it('writes FX lines only for a foreign entry and leaves an unknown rate out', () => {
    const yen = { amount: '-1166.0000', currency: 'TWD', original_amount: '-5390.0000', original_currency: 'JPY', fx_rate: '0.2163000000' };
    expect(fxLine(yen)).toBe('¥5,390 @ 0.2163');
    expect(fxConversionLine(yen)).toBe('¥5,390 × 0.2163 = $1,166');
    expect(fxLine({ ...yen, fx_rate: null })).toBe('¥5,390');
    expect(fxConversionLine({ ...yen, fx_rate: null })).toBe('¥5,390 = $1,166');
    expect(fxLine({ ...yen, original_currency: 'TWD' })).toBeNull();
    expect(fxLine({ ...yen, original_amount: null, original_currency: null })).toBeNull();
  });

  it('keeps 退款 neutral and 回饋 in the reward tone', () => {
    expect(KIND_PILLS.refund).toEqual({ label: '退款', tone: '' });
    expect(KIND_PILLS.reward?.tone).toBe('rw');
  });

  it('pads, shifts months across years and labels archived accounts', () => {
    expect(pad(3)).toBe('03');
    expect(shiftMonth(2026, 1, -1)).toEqual([2025, 12]);
    expect(shiftMonth(2026, 12, 1)).toEqual([2027, 1]);
    expect(accountLabel({ name: '舊卡', is_archived: true })).toBe('舊卡（已封存）');
    expect(accountLabel({ name: '錢包', is_archived: false })).toBe('錢包');
  });

  it('treats handled keys and IME commits as not ours, and never saves on ⏎ from a select or link', () => {
    const handled = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true });
    handled.preventDefault();
    expect(isHandledKey(handled)).toBe(true);
    expect(isHandledKey(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true }))).toBe(true);
    expect(isHandledKey(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 229 } as KeyboardEventInit))).toBe(true);
    expect(isHandledKey(new KeyboardEvent('keydown', { key: 'Enter' }))).toBe(false);
    expect([...NO_ENTER_SAVE_TAGS].sort()).toEqual(['A', 'BUTTON', 'SELECT', 'SUMMARY', 'TEXTAREA']);
  });
});
