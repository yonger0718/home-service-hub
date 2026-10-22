import { ENTRY_FORM_PATH, ShortcutKey } from '../accounting/keyboard-shortcuts';

const TYPING_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

/**
 * App-wide keys, handled by the shell on every page: N / ⇧N opens the entry form (記一筆). `targetTag` is the focused
 * element's tag name (`TEXTAREA` for contenteditable elements) or null; `inDialog` is true inside a dialog other than
 * the accounting detail sheet, whose own keys come first. On the entry form itself N is left to the form.
 */
export function resolveGlobalShortcut(
  event: ShortcutKey,
  context: { url: string; targetTag: string | null; inDialog: boolean },
): { type: 'navigate'; commands: ['/accounting/entry'] } | null {
  if (event.ctrlKey || event.metaKey || event.altKey || context.inDialog) {
    return null;
  }
  if ((event.key !== 'n' && event.key !== 'N') || TYPING_TAGS.has(context.targetTag ?? '')) {
    return null;
  }
  return ENTRY_FORM_PATH.test(context.url.split(/[?#]/)[0]) ? null : { type: 'navigate', commands: ['/accounting/entry'] };
}
