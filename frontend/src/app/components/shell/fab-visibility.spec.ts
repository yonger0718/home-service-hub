import { describe, expect, it } from 'vitest';

import { nextFabVisibility } from './fab-visibility';

describe('nextFabVisibility', () => {
  it('hides on a downward scroll and shows on any upward one', () => {
    expect(nextFabVisibility(true, { delta: 12, atEnd: false, focused: false })).toBe(false);
    expect(nextFabVisibility(false, { delta: -1, atEnd: false, focused: false })).toBe(true);
  });
  it('keeps the state when the offset did not change (horizontal scroll)', () => {
    expect(nextFabVisibility(false, { delta: 0, atEnd: false, focused: false })).toBe(false);
    expect(nextFabVisibility(true, { delta: 0, atEnd: false, focused: false })).toBe(true);
  });
  it('shows at the end of the page even while scrolling down', () => {
    expect(nextFabVisibility(false, { delta: 40, atEnd: true, focused: false })).toBe(true);
  });
  it('never hides a focused button', () => {
    expect(nextFabVisibility(true, { delta: 40, atEnd: false, focused: true })).toBe(true);
  });
});
