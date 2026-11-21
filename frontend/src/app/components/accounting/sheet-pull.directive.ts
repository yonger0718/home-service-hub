// frontend/src/app/components/accounting/sheet-pull.directive.ts
import { DOCUMENT } from '@angular/common';
import { Directive, ElementRef, OnDestroy, inject, output } from '@angular/core';

/** A pull of this many px (or 30 % of a shorter sheet's height) asks the host to close the sheet. */
export const SHEET_PULL_CLOSE_PX = 80;

/**
 * Pull-down on a phone bottom sheet's grab bar (the host): the enclosing `.sheet` follows the finger downwards and
 * springs back on release; a long enough pull emits `pulled`. The directive never closes anything itself — the host
 * decides (it may refuse while busy or ask to discard a draft; the sheet is then simply back at rest).
 * Only the grab bar listens: with pointer capture and the bar's `touch-action: none`, the sheet's own scroller never
 * takes the gesture, and a drag that starts on the sheet body never moves the sheet.
 */
@Directive({
  selector: '[appSheetPull]',
  host: {
    '(pointerdown)': 'onDown($event)',
    '(pointermove)': 'onMove($event)',
    '(pointerup)': 'onUp($event)',
    '(pointercancel)': 'onCancel($event)',
    '(lostpointercapture)': 'onCancel($event)',
  },
})
export class SheetPullDirective implements OnDestroy {
  readonly pulled = output<void>();

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly document = inject(DOCUMENT);
  private drag: { sheet: HTMLElement; pointerId: number; startY: number; closeAt: number } | null = null;
  /** Escape only cancels the drag: listened in the capture phase (a sheet handler that stops it cannot hide it from
   *  us) and never stopped or prevented, so the sheet's own Escape handling still runs. */
  private readonly escListener = (e: KeyboardEvent): void => {
    if (e.key === 'Escape') this.cancel();
  };

  onDown(event: PointerEvent): void {
    // A second finger or a secondary button is ignored first: it must not disturb an active drag.
    if (event.button !== 0 || event.isPrimary === false) return;
    if (this.drag) this.cancel();
    const sheet = this.host.closest<HTMLElement>('.sheet');
    if (!sheet) return;
    event.preventDefault();
    try {
      this.host.setPointerCapture(event.pointerId);
    } catch {
      // not every environment implements pointer capture
    }
    const height = sheet.getBoundingClientRect().height;
    this.drag = {
      sheet,
      pointerId: event.pointerId,
      startY: event.clientY,
      closeAt: Math.min(SHEET_PULL_CLOSE_PX, height * 0.3),
    };
    sheet.classList.add('pulling');
    this.document.addEventListener('keydown', this.escListener, true);
  }

  onMove(event: PointerEvent): void {
    const d = this.drag;
    if (!d || event.pointerId !== d.pointerId) return;
    d.sheet.style.transform = `translateY(${this.dy(event)}px)`;
  }

  onUp(event: PointerEvent): void {
    const d = this.drag;
    if (!d || event.pointerId !== d.pointerId) return;
    // dy is read before anything ends the drag: the browser releases the capture after pointerup and the
    // lostpointercapture that follows then finds no drag.
    const dy = this.dy(event);
    this.end();
    // dy > 0: a tap never closes, even on a sheet that reports no height.
    if (dy > 0 && dy >= d.closeAt) this.pulled.emit();
  }

  /** pointercancel and lost capture: the drag ends without emitting. */
  onCancel(event: PointerEvent): void {
    const d = this.drag;
    if (!d || event.pointerId !== d.pointerId) return;
    this.cancel();
  }

  ngOnDestroy(): void {
    // Host listeners go with the view; the document listener and the sheet's drag state go here. Nothing is emitted.
    this.cancel();
  }

  private dy(event: PointerEvent): number {
    return Math.max(0, event.clientY - this.drag!.startY);
  }

  private cancel(): void {
    const d = this.drag;
    if (!d) return;
    this.end();
    try {
      if (this.host.hasPointerCapture?.(d.pointerId)) this.host.releasePointerCapture(d.pointerId);
    } catch {
      // capture may already be gone
    }
  }

  /** Clears the tracking state, then springs the sheet back to rest. */
  private end(): void {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    this.document.removeEventListener('keydown', this.escListener, true);
    // `pulling` (transition: none) goes first and a reflow commits it, so clearing the transform animates; doing
    // both in one frame would snap the sheet back.
    d.sheet.classList.remove('pulling');
    void d.sheet.offsetHeight;
    d.sheet.style.transform = '';
  }
}
