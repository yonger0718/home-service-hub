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
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

import { LayoutModeService } from '../../../services/layout-mode.service';

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
  imports: [NgTemplateOutlet],
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
  readonly remove = output<string>();
  readonly clearAll = output<void>();
  readonly reset = output<void>();
  readonly openChange = output<boolean>();

  readonly panelId = `filter-panel-${++nextPanelId}`;
  readonly open = signal(false);
  readonly count = computed(() => this.chips().length);
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');

  constructor() {
    inject(DestroyRef).onDestroy(() => this.stopListening());
  }

  toggle(): void {
    if (this.open()) {
      this.close();
      return;
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
    if (restoreFocus) this.toggleBtn()?.nativeElement.focus({ preventScroll: true });
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
    this.close(false);
  }

  private stopListening(): void {
    this.unlistenPointerDown?.();
    this.unlistenPointerDown = null;
  }
}
