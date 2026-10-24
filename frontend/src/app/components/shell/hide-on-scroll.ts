import { DOCUMENT } from '@angular/common';
import { Directive, DestroyRef, ElementRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs';

import { FAB_IDLE_MS, PAGE_SCROLLER_SELECTOR, nextFabVisibility } from './fab-visibility';

/**
 * Hides the host while the page scrolls down; shows it on scroll up, at the end, after idling, on focus or navigation.
 * Navigation keeps the remembered offsets: the router does not restore scroll, so the document keeps its scrollTop and a
 * shorter page's clamped scroll must read as an upward move, not as a downward one from 0.
 */
@Directive({
  selector: 'a[appHideOnScroll]',
  host: { '[class.is-hidden]': 'hidden()', '(focus)': 'onFocus()' },
})
export class HideOnScrollDirective {
  private readonly document = inject(DOCUMENT);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private offsets = new WeakMap<Element, number>();
  private idleTimer: ReturnType<typeof setTimeout> | undefined;

  readonly hidden = signal(false);

  constructor() {
    const onScroll = (event: Event) => this.onScroll(event);
    this.document.addEventListener('scroll', onScroll, { capture: true, passive: true });
    inject(DestroyRef).onDestroy(() => {
      this.document.removeEventListener('scroll', onScroll, { capture: true });
      clearTimeout(this.idleTimer);
    });
    inject(Router)
      .events.pipe(
        filter(e => e instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(() => this.reset());
  }

  protected onFocus(): void {
    this.hidden.set(false);
  }

  private reset(): void {
    this.hidden.set(false);
    clearTimeout(this.idleTimer);
  }

  private onScroll(event: Event): void {
    const scroller = this.resolveScroller(event.target);
    if (!scroller) {
      return;
    }
    const top = scroller.scrollTop;
    // A scroller seen for the first time counts from 0, so one instant jump (reduced motion, a scrollbar drag) hides too.
    const previous = this.offsets.get(scroller) ?? 0;
    this.offsets.set(scroller, top);
    const atEnd = scroller.scrollHeight > scroller.clientHeight && top + scroller.clientHeight >= scroller.scrollHeight - 1;
    const focused = this.document.activeElement === this.host;
    this.hidden.set(!nextFabVisibility(!this.hidden(), { delta: top - previous, atEnd, focused }));
    clearTimeout(this.idleTimer);
    if (this.hidden()) {
      this.idleTimer = setTimeout(() => this.hidden.set(false), FAB_IDLE_MS);
    }
  }

  private resolveScroller(target: EventTarget | null): Element | null {
    if (target === this.document) {
      return this.document.scrollingElement ?? this.document.documentElement;
    }
    return target instanceof Element && target.matches(PAGE_SCROLLER_SELECTOR) ? target : null;
  }
}
