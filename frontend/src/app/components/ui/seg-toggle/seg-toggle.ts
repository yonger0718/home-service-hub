import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  QueryList,
  ViewChildren,
  effect,
  input,
  output,
  signal,
} from '@angular/core';

export interface SegToggleOption {
  value: string;
  label: string;
  ariaLabel?: string;
}

@Component({
  selector: 'app-seg-toggle',
  template: `
    <div class="seg-toggle" role="radiogroup" [attr.aria-label]="ariaLabel() || null">
      @for (option of options(); track option.value; let index = $index) {
        <button
          #segmentButton
          type="button"
          role="radio"
          [class.active]="selected() === option.value"
          [attr.aria-checked]="selected() === option.value"
          [attr.aria-label]="option.ariaLabel || option.label"
          (click)="select(option.value)"
          (keydown)="onKeydown($event, index)"
        >
          {{ option.label }}
        </button>
      }
    </div>
  `,
  styleUrl: './seg-toggle.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SegToggleComponent {
  readonly options = input<SegToggleOption[]>([]);
  readonly value = input('');
  readonly ariaLabel = input('');
  readonly change = output<string>();

  @ViewChildren('segmentButton') private buttons?: QueryList<ElementRef<HTMLButtonElement>>;

  protected readonly selected = signal('');

  constructor() {
    effect(() => {
      const value = this.value();
      const options = this.options();
      this.selected.set(value || options[0]?.value || '');
    });
  }

  protected select(value: string): void {
    if (value === this.selected()) return;
    this.selected.set(value);
    this.change.emit(value);
  }

  protected onKeydown(event: KeyboardEvent, index: number): void {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();

    const options = this.options();
    if (!options.length) return;

    const offset = event.key === 'ArrowRight' ? 1 : -1;
    const nextIndex = (index + offset + options.length) % options.length;
    this.select(options[nextIndex].value);
    queueMicrotask(() => this.buttons?.get(nextIndex)?.nativeElement.focus());
  }
}
