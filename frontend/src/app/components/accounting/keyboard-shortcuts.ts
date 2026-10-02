import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';

import { NO_ENTER_SAVE_TAGS } from './accounting-ui';

export type EntryCommand = 'save' | 'save-continue' | 'cancel';

export type ShortcutAction =
  | { type: 'navigate'; commands: (string | number)[] }
  | { type: 'move'; delta: 1 | -1 }
  | { type: 'entry'; command: EntryCommand };

export interface ShortcutKey {
  key: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
}

const ENTRY_FORM = /^\/accounting\/(entry|entries\/\d+\/edit)$/;
const LIST_PAGE = /^\/accounting(\/entries\/\d+|\/accounts\/\d+(\/entries\/\d+)?)?$/;
/** The selected entry: `/accounting/entries/:id` or, opened from a passbook, `/accounting/accounts/:id/entries/:eid`. */
const SELECTED = /^\/accounting(?:\/accounts\/\d+)?\/entries\/(\d+)$/;
const TYPING_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

/**
 * Maps a key press to an accounting action. `targetTag` is the focused element's tag name
 * (`TEXTAREA` for contenteditable elements) or null.
 */
export function resolveShortcut(event: ShortcutKey, context: { url: string; targetTag: string | null }): ShortcutAction | null {
  if (event.ctrlKey || event.metaKey || event.altKey) {
    return null;
  }
  const path = context.url.split(/[?#]/)[0];
  if (ENTRY_FORM.test(path)) {
    if (event.key === 'Escape') {
      return { type: 'entry', command: 'cancel' };
    }
    if (event.key === 'Enter' && !NO_ENTER_SAVE_TAGS.has(context.targetTag ?? '')) {
      return { type: 'entry', command: event.shiftKey ? 'save-continue' : 'save' };
    }
    return null;
  }
  if (!LIST_PAGE.test(path) || TYPING_TAGS.has(context.targetTag ?? '')) {
    return null;
  }
  switch (event.key) {
    case 'n':
    case 'N':
      return { type: 'navigate', commands: ['/accounting/entry'] };
    case 'e':
    case 'E': {
      const selected = SELECTED.exec(path);
      return selected ? { type: 'navigate', commands: ['/accounting/entries', Number(selected[1]), 'edit'] } : null;
    }
    case 'ArrowDown':
      return { type: 'move', delta: 1 };
    case 'ArrowUp':
      return { type: 'move', delta: -1 };
    default:
      return null;
  }
}

/** Carries ⏎ / ⇧⏎ / Esc from the layout's key listener to the entry form. */
@Injectable({ providedIn: 'root' })
export class AccountingShortcutsService {
  readonly entryCommands = new Subject<EntryCommand>();
}
