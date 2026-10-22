import { describe, expect, it } from 'vitest';

import { ShortcutKey } from '../accounting/keyboard-shortcuts';
import { resolveGlobalShortcut } from './global-shortcuts';

function key(value: string, extra: Partial<ShortcutKey> = {}): ShortcutKey {
  return { key: value, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, ...extra };
}

const OPEN_FORM = { type: 'navigate', commands: ['/accounting/entry'] };
const at = (url: string, extra: { targetTag?: string | null; inDialog?: boolean } = {}) => ({
  url,
  targetTag: null,
  inDialog: false,
  ...extra,
});

describe('resolveGlobalShortcut', () => {
  it('opens the entry form with N (shift allowed) on every page', () => {
    for (const url of [
      '/portfolio',
      '/settings',
      '/accounting',
      '/accounting?month=2026-09',
      '/accounting/entries/9',
      '/accounting/accounts/5',
      '/accounting/reminders',
      '/accounting/accounts/5/settings',
    ]) {
      expect(resolveGlobalShortcut(key('n'), at(url))).toEqual(OPEN_FORM);
      expect(resolveGlobalShortcut(key('N', { shiftKey: true }), at(url))).toEqual(OPEN_FORM);
    }
  });

  it('ignores N while typing', () => {
    for (const targetTag of ['INPUT', 'TEXTAREA', 'SELECT']) {
      expect(resolveGlobalShortcut(key('n'), at('/portfolio', { targetTag }))).toBeNull();
    }
    // A focused button or link is not typing.
    expect(resolveGlobalShortcut(key('n'), at('/portfolio', { targetTag: 'BUTTON' }))).toEqual(OPEN_FORM);
  });

  it('ignores N with ctrl, meta or alt', () => {
    for (const modifier of ['ctrlKey', 'metaKey', 'altKey'] as const) {
      expect(resolveGlobalShortcut(key('n', { [modifier]: true }), at('/accounting'))).toBeNull();
    }
  });

  it('ignores N inside a dialog', () => {
    expect(resolveGlobalShortcut(key('n'), at('/accounting', { inDialog: true }))).toBeNull();
  });

  it('ignores N on the entry form itself', () => {
    for (const url of ['/accounting/entry', '/accounting/entry?kind=income', '/accounting/entries/9/edit']) {
      expect(resolveGlobalShortcut(key('n'), at(url))).toBeNull();
    }
  });

  it('leaves every other key alone', () => {
    for (const value of ['e', 'E', 'm', 'ArrowDown', 'ArrowUp', 'Enter', 'Escape', ' ']) {
      expect(resolveGlobalShortcut(key(value), at('/accounting/entries/9'))).toBeNull();
    }
  });
});
