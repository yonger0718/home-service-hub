import { focusables } from '../../accounting/accounting-ui';

/** The containers that hold focus as a modal: the sheet, bottom sheets, drawers, PrimeNG dialogs and confirm dialogs. */
export const MODAL_SELECTOR = '[aria-modal="true"], [role="dialog"], [role="alertdialog"]';

/** `focusables()` of accounting-ui.ts minus PrimeNG's hidden focus-trap sentinels. */
export function cycleFocusables(container: HTMLElement): HTMLElement[] {
  return focusables(container).filter(element => element.dataset['pHiddenFocusable'] !== 'true');
}

/**
 * The element Tab / Shift+Tab should move to when the cycle [modal…, toast…] crosses a group edge or wraps;
 * null when the move stays inside one group (left to the modal's trap or the browser) or no modal is involved.
 */
export function toastCycleTarget(input: {
  active: HTMLElement;
  modal: HTMLElement | null;
  toast: HTMLElement | null;
  backwards: boolean;
}): HTMLElement | null {
  const { active, modal, toast, backwards } = input;
  if (!modal || !toast) {
    return null;
  }
  const toastItems = cycleFocusables(toast);
  if (toastItems.length === 0) {
    return null;
  }
  const modalItems = cycleFocusables(modal);
  if (toast.contains(active)) {
    if (backwards) {
      return active === toastItems[0] ? (modalItems[modalItems.length - 1] ?? null) : null;
    }
    return active === toastItems[toastItems.length - 1] ? (modalItems[0] ?? null) : null;
  }
  if (!modal.contains(active)) {
    return null;
  }
  const atFirst = modalItems.length === 0 || active === modalItems[0] || active === modal;
  const atLast = modalItems.length === 0 || active === modalItems[modalItems.length - 1];
  if (backwards) {
    return atFirst ? toastItems[toastItems.length - 1] : null;
  }
  return atLast ? toastItems[0] : null;
}
