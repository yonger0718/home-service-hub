import { DOCUMENT } from '@angular/common';
import { DestroyRef, Injectable, InjectionToken, computed, inject, signal } from '@angular/core';
import { CanMatchFn } from '@angular/router';

/** `< 760` phone (one screen per route), `760–1023` sheet (right-hand sheet), `≥ 1024` panes (two panes). */
export type LayoutMode = 'phone' | 'sheet' | 'panes';

export const PHONE_QUERY = '(max-width: 759.98px)';
export const PANES_QUERY = '(min-width: 1024px)';
/** Short desktop screens (13-inch laptops, ≈800–900 px tall): the shell switches to its compact density. */
export const COMPACT_QUERY = '(max-height: 820px)';

export type MatchMediaFn = (query: string) => MediaQueryList;

/** `window.matchMedia`, or null where it does not exist; tests provide a fake. */
export const MATCH_MEDIA = new InjectionToken<MatchMediaFn | null>('MATCH_MEDIA', {
  providedIn: 'root',
  factory: () => {
    const win = inject(DOCUMENT).defaultView;
    return win && typeof win.matchMedia === 'function' ? (query: string) => win.matchMedia(query) : null;
  },
});

@Injectable({ providedIn: 'root' })
export class LayoutModeService {
  private readonly matchMedia = inject(MATCH_MEDIA);
  private readonly window = inject(DOCUMENT).defaultView;
  private readonly measured = signal<LayoutMode>(this.measure());
  private readonly forced = signal<LayoutMode | null>(null);
  private readonly measuredCompact = signal(this.measureCompact());

  readonly mode = computed(() => this.forced() ?? this.measured());
  /** True when the viewport is at most 820 px tall. */
  readonly compactHeight = this.measuredCompact.asReadonly();

  constructor() {
    const matchMedia = this.matchMedia;
    if (!matchMedia) {
      return;
    }
    const queries = [matchMedia(PHONE_QUERY), matchMedia(PANES_QUERY), matchMedia(COMPACT_QUERY)];
    const update = () => {
      this.measured.set(this.measure());
      this.measuredCompact.set(this.measureCompact());
    };
    queries.forEach(query => query.addEventListener('change', update));
    inject(DestroyRef).onDestroy(() => queries.forEach(query => query.removeEventListener('change', update)));
  }

  /** Force a mode (tests, previews); `null` returns to the viewport. */
  set(mode: LayoutMode | null): void {
    this.forced.set(mode);
  }

  private measure(): LayoutMode {
    if (this.matchMedia) {
      if (this.matchMedia(PHONE_QUERY).matches) {
        return 'phone';
      }
      return this.matchMedia(PANES_QUERY).matches ? 'panes' : 'sheet';
    }
    const width = this.window?.innerWidth ?? 1024;
    return width < 760 ? 'phone' : width >= 1024 ? 'panes' : 'sheet';
  }

  private measureCompact(): boolean {
    if (this.matchMedia) {
      return this.matchMedia(COMPACT_QUERY).matches;
    }
    return (this.window?.innerHeight ?? 900) <= 820;
  }
}

/** Route tables: one screen per route below 760 px … */
export const phoneLayoutGuard: CanMatchFn = () => inject(LayoutModeService).mode() === 'phone';
/** … list plus pane (or sheet) from 760 px. */
export const wideLayoutGuard: CanMatchFn = () => inject(LayoutModeService).mode() !== 'phone';
