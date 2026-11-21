import { focusables } from '../../accounting/accounting-ui';

/** The containers that hold focus as a modal: the sheet, bottom sheets, drawers, PrimeNG dialogs and confirm dialogs. */
export const MODAL_SELECTOR = '[aria-modal="true"], [role="dialog"], [role="alertdialog"]';

/** PrimeNG overlay panels (picker / select / popover) are appended to `body` and may carry role="dialog": never a modal. */
const OVERLAY_PANEL_SELECTOR =
  '.p-datepicker, .p-select-overlay, .p-multiselect-overlay, .p-autocomplete-overlay, .p-cascadeselect-overlay, ' +
  '.p-treeselect-overlay, .p-popover, .p-overlay, [data-overlay]';

/**
 * The OUTERMOST modal container holding `element` (a nested role="dialog" or an overlay inside a dialog never becomes
 * "the modal"). Rule for PrimeNG overlays appended to `body`: they are not inside any modal element, so there is no
 * modal and the outlet leaves Tab alone there (the overlay's own handling and the browser decide); an outermost match
 * that is itself an overlay panel counts as no modal too.
 */
export function outermostModal(element: Element): HTMLElement | null {
  let found: HTMLElement | null = null;
  for (let node: Element | null = element; node; node = node.parentElement) {
    if (node.matches(MODAL_SELECTOR)) {
      found = node as HTMLElement;
    }
  }
  return found && !found.matches(OVERLAY_PANEL_SELECTOR) ? found : null;
}

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
