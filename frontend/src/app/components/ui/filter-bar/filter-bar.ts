import { NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  Injector,
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

const FOCUSABLE = 'input, select, textarea, button, [href], [tabindex]:not([tabindex="-1"])';

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
  private readonly toggleBtn = viewChild<ElementRef<HTMLButtonElement>>('toggleBtn');
  private readonly panel = viewChild<ElementRef<HTMLElement>>('panel');

  readonly chips = input<FilterChip[]>([]);
  readonly remove = output<string>();
  readonly clearAll = output<void>();
  readonly reset = output<void>();
  readonly openChange = output<boolean>();

  readonly open = signal(false);
  readonly count = computed(() => this.chips().length);
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');

  toggle(): void {
    if (this.open()) {
      this.close();
      return;
    }
    this.open.set(true);
    this.openChange.emit(true);
    afterNextRender(
      () => this.panel()?.nativeElement.querySelector<HTMLElement>(FOCUSABLE)?.focus(),
      { injector: this.injector },
    );
  }

  close(): void {
    if (!this.open()) return;
    this.open.set(false);
    this.openChange.emit(false);
    this.toggleBtn()?.nativeElement.focus();
  }

  @HostListener('document:pointerdown', ['$event'])
  onDocumentPointerDown(event: Event): void {
    if (!this.open() || this.isPhone()) return;
    const target = event.target as Element | null;
    if (!target || this.host.nativeElement.contains(target)) return;
    if (target.closest?.(PORTALED_OVERLAY_SELECTOR)) return;
    this.close();
  }
}
