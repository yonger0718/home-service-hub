// frontend/src/app/components/accounting/swipe-x.directive.ts
import { Directive, ElementRef, OnDestroy, effect, inject, input, output } from '@angular/core';

export const SWIPE_MIN_DX = 48;
export const SWIPE_DOMINANCE = 1.5;
export const SWIPE_CLICK_GRACE_MS = 400;
/** Horizontal travel after which the host takes pointer capture (a plain tap never captures, so its click still hits the child). */
export const SWIPE_CAPTURE_DX = 10;

/** Horizontal swipe on the host: emits 1 for right-to-left (later), -1 for left-to-right (earlier). Vertical drags scroll. */
@Directive({
  selector: '[appSwipeX]',
  host: {
    '(pointerdown)': 'onDown($event)',
    '(pointermove)': 'onMove($event)',
    '(pointerup)': 'onUp($event)',
    '(pointercancel)': 'reset()',
  },
})
export class SwipeXDirective implements OnDestroy {
  readonly swipeX = output<1 | -1>();
  /** While true the host is inert: no touch-action, no gesture, no emit, no click guard. */
  readonly appSwipeXDisabled = input(false);

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  /** touch-action goes through the element's style property (jsdom does not model the CSS property, so a style binding would not show up there). */
  private readonly touchAction = effect(() => {
    this.host.style.touchAction = this.appSwipeXDisabled() ? '' : 'pan-y';
  });
  private gesture: { x: number; y: number; pointerId: number; captured: boolean } | null = null;
  private clickGuard: { listener: (e: Event) => void; timer: ReturnType<typeof setTimeout> } | null = null;

  onDown(event: PointerEvent): void {
    this.reset();
    this.clearClickGuard();
    if (this.appSwipeXDisabled()) return;
    if (event.button !== 0 || event.isPrimary === false) return;
    const target = event.target as Element | null;
    if (target?.closest?.('input, textarea, select, [contenteditable]')) return;
    this.gesture = { x: event.clientX, y: event.clientY, pointerId: event.pointerId, captured: false };
  }

  onMove(event: PointerEvent): void {
    const g = this.gesture;
    if (!g || g.captured || g.pointerId !== event.pointerId) return;
    const dx = event.clientX - g.x;
    const dy = event.clientY - g.y;
    if (Math.abs(dx) < SWIPE_CAPTURE_DX || Math.abs(dx) < SWIPE_DOMINANCE * Math.abs(dy)) return;
    g.captured = true;
    try {
      this.host.setPointerCapture(g.pointerId);
    } catch {
      // not every environment implements pointer capture
    }
  }

  onUp(event: PointerEvent): void {
    const g = this.gesture;
    if (!g || g.pointerId !== event.pointerId) return;
    const dx = event.clientX - g.x;
    const dy = event.clientY - g.y;
    this.reset();
    const horizontal = Math.abs(dx) >= SWIPE_MIN_DX && Math.abs(dx) >= SWIPE_DOMINANCE * Math.abs(dy);
    if (!horizontal || document.getSelection()?.isCollapsed === false) return;
    this.swallowNextClick();
    this.swipeX.emit(dx < 0 ? 1 : -1);
  }

  reset(): void {
    const g = this.gesture;
    this.gesture = null;
    if (!g?.captured) return;
    try {
      this.host.releasePointerCapture(g.pointerId);
    } catch {
      // capture may already be gone
    }
  }

  ngOnDestroy(): void {
    this.clearClickGuard();
    this.gesture = null;
  }

  private swallowNextClick(): void {
    this.clearClickGuard();
    const listener = (e: Event): void => {
      e.preventDefault();
      e.stopPropagation();
      this.clearClickGuard();
    };
    const timer = setTimeout(() => this.clearClickGuard(), SWIPE_CLICK_GRACE_MS);
    this.clickGuard = { listener, timer };
    this.host.addEventListener('click', listener, { capture: true, once: true });
  }

  private clearClickGuard(): void {
    const guard = this.clickGuard;
    if (!guard) return;
    this.clickGuard = null;
    clearTimeout(guard.timer);
    this.host.removeEventListener('click', guard.listener, { capture: true });
  }
}
