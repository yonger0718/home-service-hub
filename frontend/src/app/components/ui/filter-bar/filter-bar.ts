import { DOCUMENT, NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  Renderer2,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
  viewChild,
} from '@angular/core';

import { LayoutModeService } from '../../../services/layout-mode.service';
import { SheetPullDirective } from '../../accounting/sheet-pull.directive';
import { FilterChipsComponent } from './filter-chips';

export interface FilterChip {
  key: string;
  label: string;
}

/** PrimeNG 21 panels are portaled to <body>, outside the host; pointerdown in them must not close the popover. */
export const PORTALED_OVERLAY_SELECTOR = '.p-overlay, .p-datepicker-panel, .p-select-overlay, .p-multiselect-overlay';

let nextPanelId = 0;

@Component({
  selector: 'app-filter-bar',
  standalone: true,
  imports: [NgTemplateOutlet, SheetPullDirective, FilterChipsComponent],
  templateUrl: './filter-bar.html',
  styleUrl: './filter-bar.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FilterBarComponent {
  private readonly layoutMode = inject(LayoutModeService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private readonly renderer = inject(Renderer2);
  private readonly document = inject(DOCUMENT);
  private readonly toggleBtn = viewChild<ElementRef<HTMLButtonElement>>('toggleBtn');
  private readonly panel = viewChild<ElementRef<HTMLElement>>('panel');
  /** Removes the document pointerdown listener; only set while the panel is open. */
  private unlistenPointerDown: (() => void) | null = null;

  readonly chips = input<FilterChip[]>([]);
  /** Hides only the search / 篩選 row; the chips and the panel still render (the panel opens through `toggle`). */
  readonly rowHidden = input(false);
  /** Hides only the chips (a pane elsewhere shows them). */
  readonly chipsHidden = input(false);
  readonly remove = output<string>();
  readonly clearAll = output<void>();
  readonly reset = output<void>();
  readonly openChange = output<boolean>();

  readonly panelId = `filter-panel-${++nextPanelId}`;
  readonly open = signal(false);
  readonly count = computed(() => this.chips().length);
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');

  /** The outside button the panel is anchored to (null: the bar's own 篩選 button). */
  anchor: HTMLElement | null = null;
  readonly anchorTop = signal<number | null>(null);
  readonly anchorRight = signal<number | null>(null);
  private openPhone = false;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.stopListening());
    // A layout change (phone sheet <-> popover) while open closes the panel without moving focus.
    effect(() => {
      const phone = this.isPhone();
      untracked(() => {
        if (this.open() && phone !== this.openPhone) this.close(false);
      });
    });
  }

  /** With `anchor` the popover is fixed under that button, right-aligned to it, and focus returns to it on close. */
  toggle(anchor?: HTMLElement): void {
    if (this.open()) {
      this.close();
      return;
    }
    this.anchor = anchor ?? null;
    this.openPhone = this.isPhone();
    if (anchor) {
      const rect = anchor.getBoundingClientRect();
      const width = this.document.defaultView?.innerWidth ?? 1024;
      const panelWidth = Math.min(560, width - 16);
      this.anchorTop.set(Math.round(rect.bottom + 6));
      this.anchorRight.set(Math.round(Math.min(Math.max(width - rect.right, 8), Math.max(8, width - panelWidth - 8))));
    }
    this.open.set(true);
    this.openChange.emit(true);
    this.unlistenPointerDown = this.renderer.listen(this.document, 'pointerdown', (e: Event) =>
      this.onDocumentPointerDown(e),
    );
    // Focus the dialog itself, not its first control: a focused datepicker input auto-opens its calendar.
    afterNextRender(() => this.panel()?.nativeElement.focus({ preventScroll: true }), { injector: this.injector });
  }

  /** `restoreFocus` is false for an outside pointerdown: the user's click target keeps focus. */
  close(restoreFocus = true): void {
    if (!this.open()) return;
    this.stopListening();
    this.open.set(false);
    this.openChange.emit(false);
    const returnTo = this.anchor ?? this.toggleBtn()?.nativeElement;
    this.anchor = null;
    this.anchorTop.set(null);
    this.anchorRight.set(null);
    if (restoreFocus) returnTo?.focus({ preventScroll: true });
  }

  /** A child that handled Escape (an open calendar or select overlay) calls preventDefault(); IME composition too. */
  onPanelEscape(event: Event): void {
    const e = event as KeyboardEvent;
    if (e.defaultPrevented || e.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    this.close();
  }

  private onDocumentPointerDown(event: Event): void {
    if (!this.open() || this.isPhone()) return;
    const target = event.target as Element | null;
    if (!target || this.host.nativeElement.contains(target)) return;
    if (target.closest?.(PORTALED_OVERLAY_SELECTOR)) return;
    if (this.anchor?.contains(target)) return;
    this.close(false);
  }

  private stopListening(): void {
    this.unlistenPointerDown?.();
    this.unlistenPointerDown = null;
  }
}
