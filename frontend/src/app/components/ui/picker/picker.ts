import { DOCUMENT, NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
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
import { isHandledKey, restoreOverlayFocus, trapFocus } from '../../accounting/accounting-ui';
import { SheetPullDirective } from '../../accounting/sheet-pull.directive';

export interface PickerOption {
  value: string;
  label: string;
  hint?: string;
  disabled?: boolean;
}

/**
 * Generic select replacement: a trigger showing the chosen label, a popover under it on wider layouts and a bottom
 * sheet on phones. Escape, an overlay tap or an outside pointerdown close without emitting.
 */
@Component({
  selector: 'app-picker',
  standalone: true,
  imports: [NgTemplateOutlet, SheetPullDirective],
  templateUrl: './picker.html',
  styleUrls: ['../../accounting/sheet.scss', './picker.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(keydown)': 'onHostKeydown($event)',
    '(document:pointerdown)': 'onDocumentPointerDown($event)',
  },
})
export class PickerComponent {
  private readonly layoutMode = inject(LayoutModeService);
  private readonly injector = inject(Injector);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly document = inject(DOCUMENT);

  readonly options = input<PickerOption[]>([]);
  readonly value = input('');
  readonly label = input.required<string>();
  readonly disabled = input(false);
  readonly change = output<string>();

  private readonly trigger = viewChild.required<ElementRef<HTMLButtonElement>>('trigger');
  private readonly panel = viewChild<ElementRef<HTMLElement>>('panel');

  readonly open = signal(false);
  readonly activeValue = signal<string | null>(null);

  readonly phone = computed(() => this.layoutMode.mode() === 'phone');
  readonly selectedLabel = computed(() => this.options().find(o => o.value === this.value())?.label ?? this.label());

  toggle(): void {
    if (this.open()) {
      this.close();
    } else if (!this.disabled()) {
      const enabled = this.options().filter(o => !o.disabled);
      this.activeValue.set(enabled.find(o => o.value === this.value())?.value ?? enabled[0]?.value ?? null);
      this.open.set(true);
      afterNextRender(() => (this.optionElement(this.activeValue()) ?? this.panel()?.nativeElement)?.focus(), {
        injector: this.injector,
      });
    }
  }

  close(restoreFocus = true): void {
    if (!this.open()) {
      return;
    }
    this.open.set(false);
    if (restoreFocus) {
      restoreOverlayFocus(this.trigger().nativeElement, this.host.nativeElement);
    }
  }

  choose(option: PickerOption): void {
    if (option.disabled) {
      return;
    }
    this.close();
    if (option.value !== this.value()) {
      this.change.emit(option.value);
    }
  }

  onDocumentPointerDown(event: Event): void {
    if (this.open() && !this.phone() && !this.host.nativeElement.contains(event.target as Node | null)) {
      this.close(false);
    }
  }

  onHostKeydown(event: KeyboardEvent): void {
    const panel = this.panel()?.nativeElement;
    if (!this.open() || !panel || isHandledKey(event)) {
      return;
    }
    switch (event.key) {
      case 'Escape':
        event.preventDefault();
        this.close();
        return;
      case 'Tab':
        trapFocus(panel, event);
        return;
      case 'ArrowDown':
      case 'ArrowUp':
        event.preventDefault();
        this.move(event.key === 'ArrowDown' ? 1 : -1);
        return;
      case 'Enter': {
        event.preventDefault();
        const current = this.options().find(o => o.value === this.activeValue());
        if (current) {
          this.choose(current);
        }
        return;
      }
    }
  }

  onOptionFocus(option: PickerOption): void {
    if (!option.disabled) {
      this.activeValue.set(option.value);
    }
  }

  private move(delta: 1 | -1): void {
    const enabled = this.options().filter(o => !o.disabled);
    if (enabled.length === 0) {
      return;
    }
    const index = enabled.findIndex(o => o.value === this.activeValue());
    const next = enabled[(index + delta + enabled.length) % enabled.length];
    this.activeValue.set(next.value);
    afterNextRender(() => this.optionElement(next.value)?.focus(), { injector: this.injector });
  }

  private optionElement(value: string | null): HTMLElement | null {
    return value === null
      ? null
      : (Array.from(this.panel()?.nativeElement.querySelectorAll<HTMLElement>('[role=option]') ?? []).find(
          el => el.dataset['value'] === value,
        ) ?? null);
  }
}
