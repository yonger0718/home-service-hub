/**
 * Keyboard guards shared by the review flow (D5) and the e-invoice flow (D10): which focused element or open dialog
 * keeps the flow's digit / S / ← → / Esc keys from acting, and the key hint under the answers.
 */

/** ARIA text widgets whose letters are input (as the shell's global keys). */
const TYPING_ROLES = '[role="combobox"], [role="listbox"], [role="spinbutton"]';
const TYPING_TAGS: ReadonlySet<string> = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
const EDITABLE = '[contenteditable]:not([contenteditable="false"])';

/** An open sheet or dialog: the flow's keys wait until it closes. */
export const OPEN_DIALOG = '[role="dialog"], [aria-modal="true"]';

/** The focused element takes typed letters (an input, an editable or an ARIA text widget). */
export function typing(node: Element | null): boolean {
  if (!(node instanceof HTMLElement)) {
    return false;
  }
  return (
    TYPING_TAGS.has(node.tagName) ||
    node.isContentEditable ||
    !!node.closest(EDITABLE) ||
    !!node.closest(TYPING_ROLES)
  );
}

/** 「1–3 選擇 · S 略過 · ← → 上下筆 · Esc 關閉」 with the digits this step really has (`unit` 筆 or 張). */
export function keyHint(digits: number, unit: '筆' | '張' = '筆'): string {
  const pick = digits === 0 ? null : digits === 1 ? '1 選擇' : `1–${digits} 選擇`;
  return [pick, 'S 略過', `← → 上下${unit}`, 'Esc 關閉'].filter((part) => part !== null).join(' · ');
}
