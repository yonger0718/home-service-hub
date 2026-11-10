// frontend/src/app/components/accounting/sheet-pull.testing.ts
// Spec helper (no vitest import: tsconfig.app.json type-checks every non-spec file).

/** jsdom may lack PointerEvent: a MouseEvent with the pointer fields SheetPullDirective reads. */
function pointer(type: string, clientY: number): Event {
  const ev = new MouseEvent(type, { clientY, button: 0, bubbles: true, cancelable: true });
  Object.defineProperty(ev, 'pointerId', { value: 1 });
  Object.defineProperty(ev, 'isPrimary', { value: true });
  Object.defineProperty(ev, 'pointerType', { value: 'touch' });
  return ev;
}

/**
 * Drags a bottom sheet's grab bar `dy` px down (pointerdown / pointermove / pointerup) with the enclosing `.sheet`
 * reporting `sheetHeight` px. The defaults pass the close threshold (min(80, 30 % of 400)).
 * The caller runs change detection afterwards.
 */
export function pullGrab(grab: HTMLElement, dy = 100, sheetHeight = 400): void {
  const sheet = grab.closest<HTMLElement>('.sheet');
  if (!sheet) throw new Error('pullGrab: the grab bar is not inside a .sheet');
  Object.defineProperty(sheet, 'getBoundingClientRect', {
    configurable: true,
    value: () =>
      ({ x: 0, y: 0, top: 0, left: 0, right: 360, bottom: sheetHeight, width: 360, height: sheetHeight, toJSON: () => ({}) }) as DOMRect,
  });
  const startY = 100;
  grab.dispatchEvent(pointer('pointerdown', startY));
  grab.dispatchEvent(pointer('pointermove', startY + dy));
  grab.dispatchEvent(pointer('pointerup', startY + dy));
}
