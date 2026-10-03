import { describe, expect, it } from 'vitest';

import { ShortcutKey, resolveShortcut } from './keyboard-shortcuts';

function key(value: string, extra: Partial<ShortcutKey> = {}): ShortcutKey {
  return { key: value, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, ...extra };
}

describe('resolveShortcut', () => {
  it('opens the entry form with N on list pages', () => {
    for (const url of ['/accounting', '/accounting/entries/9', '/accounting/accounts/5', '/accounting?month=2026-09']) {
      expect(resolveShortcut(key('n'), { url, targetTag: null })).toEqual({ type: 'navigate', commands: ['/accounting/entry'] });
    }
    expect(resolveShortcut(key('N', { shiftKey: true }), { url: '/accounting', targetTag: null })?.type).toBe('navigate');
  });

  it('edits the selected entry with E only when one is selected', () => {
    expect(resolveShortcut(key('e'), { url: '/accounting/entries/9', targetTag: null })).toEqual({
      type: 'navigate',
      commands: ['/accounting/entries', 9, 'edit'],
    });
    expect(resolveShortcut(key('e'), { url: '/accounting', targetTag: null })).toBeNull();
    // An entry opened from a passbook edits the same way.
    expect(resolveShortcut(key('e'), { url: '/accounting/accounts/5/entries/9', targetTag: null })).toEqual({
      type: 'navigate',
      commands: ['/accounting/entries', 9, 'edit'],
    });
    expect(resolveShortcut(key('e'), { url: '/accounting/accounts/5', targetTag: null })).toBeNull();
  });

  it('moves the selection with the arrow keys', () => {
    expect(resolveShortcut(key('ArrowDown'), { url: '/accounting', targetTag: null })).toEqual({ type: 'move', delta: 1 });
    expect(resolveShortcut(key('ArrowUp'), { url: '/accounting/entries/9', targetTag: null })).toEqual({ type: 'move', delta: -1 });
    expect(resolveShortcut(key('ArrowDown'), { url: '/accounting/accounts/5', targetTag: null })).toEqual({ type: 'move', delta: 1 });
    expect(resolveShortcut(key('ArrowDown'), { url: '/accounting/accounts/5/entries/9', targetTag: null })).toEqual({ type: 'move', delta: 1 });
    // The reminder centre lists entry rows under an expanded counterparty (the layout leaves ↓ alone without rows).
    expect(resolveShortcut(key('ArrowDown'), { url: '/accounting/reminders', targetTag: null })).toEqual({ type: 'move', delta: 1 });
    expect(resolveShortcut(key('ArrowUp'), { url: '/accounting/reminders', targetTag: null })).toEqual({ type: 'move', delta: -1 });
    expect(resolveShortcut(key('e'), { url: '/accounting/reminders', targetTag: null })).toBeNull();
    expect(resolveShortcut(key('ArrowDown'), { url: '/accounting/reminders/5', targetTag: null })).toBeNull();
  });

  it('ignores list keys while typing, with modifiers, and outside list pages', () => {
    expect(resolveShortcut(key('n'), { url: '/accounting', targetTag: 'INPUT' })).toBeNull();
    expect(resolveShortcut(key('n'), { url: '/accounting', targetTag: 'TEXTAREA' })).toBeNull();
    expect(resolveShortcut(key('n', { metaKey: true }), { url: '/accounting', targetTag: null })).toBeNull();
    expect(resolveShortcut(key('n'), { url: '/accounting/settings', targetTag: null })).toBeNull();
    expect(resolveShortcut(key('n'), { url: '/accounting/accounts/5/settings', targetTag: null })).toBeNull();
  });

  it('saves, saves and continues, and cancels on the entry form', () => {
    for (const url of ['/accounting/entry', '/accounting/entry?kind=income', '/accounting/entries/9/edit']) {
      expect(resolveShortcut(key('Enter'), { url, targetTag: 'INPUT' })).toEqual({ type: 'entry', command: 'save' });
      expect(resolveShortcut(key('Enter', { shiftKey: true }), { url, targetTag: null })).toEqual({ type: 'entry', command: 'save-continue' });
      expect(resolveShortcut(key('Escape'), { url, targetTag: 'INPUT' })).toEqual({ type: 'entry', command: 'cancel' });
    }
    expect(resolveShortcut(key('Enter'), { url: '/accounting/entry', targetTag: 'TEXTAREA' })).toBeNull();
    expect(resolveShortcut(key('Enter'), { url: '/accounting/entry', targetTag: 'BUTTON' })).toBeNull();
    expect(resolveShortcut(key('n'), { url: '/accounting/entry', targetTag: null })).toBeNull();
  });
});
