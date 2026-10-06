import { describe, expect, it } from 'vitest';

import {
  KIND_PILLS,
  NO_ENTER_SAVE_TAGS,
  FOCUSABLE_SELECTOR, focusables, trapFocus,
  accountLabel,
  colorOf,
  contrastRatio,
  textOn,
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
  it('picks the readable header text for a category colour (WCAG contrast)', () => {
    expect(contrastRatio('#d4823b', '#1d1c1a')).toBeCloseTo(5.72, 2);
    expect(contrastRatio('#d4823b', '#ffffff')).toBeCloseTo(2.98, 2);
    expect(textOn('#d4823b')).toBe('#1d1c1a');
    expect(textOn('#2b2d42')).toBe('#ffffff');
    expect(textOn('#fff')).toBe('#1d1c1a');
    expect(textOn(null)).toBeNull();
    expect(textOn('var(--app-surface-soft)')).toBeNull();
  });

  describe('trapFocus', () => {
    function build(): { box: HTMLElement; first: HTMLButtonElement; middle: HTMLInputElement; last: HTMLButtonElement } {
      const box = document.createElement('div');
      box.innerHTML = `
        <button class="first">a</button>
        <button disabled>skip</button>
        <input class="middle" />
        <div tabindex="-1">skip</div>
        <span inert><button>skip</button></span>
        <button class="last">b</button>`;
      document.body.appendChild(box);
      return {
        box,
        first: box.querySelector('.first')!,
        middle: box.querySelector('.middle')!,
        last: box.querySelector('.last')!,
      };
    }

    const tab = (shiftKey = false) => new KeyboardEvent('keydown', { key: 'Tab', shiftKey, cancelable: true });

    it('lists only reachable focusables', () => {
      const { box, first, middle, last } = build();
      expect(FOCUSABLE_SELECTOR).toContain('button:not([disabled])');
      expect(focusables(box)).toEqual([first, middle, last]);
      box.remove();
    });

    it('wraps Tab from the last to the first and Shift+Tab from the first to the last', () => {
      const { box, first, middle, last } = build();
      last.focus();
      const forward = tab();
      expect(trapFocus(box, forward)).toBe(true);
      expect(forward.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(first);

      const back = tab(true);
      expect(trapFocus(box, back)).toBe(true);
      expect(document.activeElement).toBe(last);

      middle.focus();
      const inner = tab();
      expect(trapFocus(box, inner)).toBe(false);
      expect(inner.defaultPrevented).toBe(false);
      box.remove();
    });

    it('pulls focus back in from outside, ignores other keys and handled events, and holds an empty container', () => {
      const { box, first } = build();
      const outside = document.createElement('button');
      document.body.appendChild(outside);
      outside.focus();
      expect(trapFocus(box, tab())).toBe(true);
      expect(document.activeElement).toBe(first);

      expect(trapFocus(box, new KeyboardEvent('keydown', { key: 'Enter', cancelable: true }))).toBe(false);
      const handled = tab();
      handled.preventDefault();
      expect(trapFocus(box, handled)).toBe(false);

      const empty = document.createElement('div');
      empty.tabIndex = -1;
      document.body.appendChild(empty);
      const held = tab();
      expect(trapFocus(empty, held)).toBe(true);
      expect(document.activeElement).toBe(empty);
      [box, outside, empty].forEach(node => node.remove());
    });
  });

});
