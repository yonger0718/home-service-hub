import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  ElementRef,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

@Component({
  selector: 'app-stepper',
  template: `
    <div
      class="stepper"
      role="group"
      [class.invalid]="invalid()"
      [attr.aria-label]="ariaLabel()"
      (keydown)="onGroupKeydown($event)"
    >
      <button
        type="button"
        class="dec"
        [attr.aria-label]="decLabel()"
        [disabled]="decDisabled()"
        (click)="step(-1)"
      >−</button>
      @if (editing()) {
        <input
          #field
          class="val-input"
          inputmode="numeric"
          [attr.aria-label]="ariaLabel()"
          [value]="draft()"
          (input)="onInput($event)"
          (keydown)="onInputKeydown($event)"
          (blur)="commit()"
        />
      } @else {
        <button
          type="button"
          class="val"
          [attr.aria-label]="display() + '，輸入數值'"
          [disabled]="disabled()"
          (click)="startEdit()"
        >{{ display() }}</button>
      }
      <button
        type="button"
        class="inc"
        [attr.aria-label]="incLabel()"
        [disabled]="incDisabled()"
        (click)="step(1)"
      >＋</button>
    </div>
  `,
  styleUrl: './stepper.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StepperComponent {
  readonly value = input<number | null>(null);
  readonly min = input(1);
  readonly max = input(31);
  readonly nullable = input(false);
  readonly nullLabel = input('—');
  readonly suffix = input('');
  readonly ariaLabel = input.required<string>();
  readonly decLabel = input('減一');
  readonly incLabel = input('加一');
  readonly disabled = input(false);
  readonly invalid = input(false);
  readonly valueChange = output<number | null>();

  protected readonly editing = signal(false);
  protected readonly draft = signal('');
  private readonly field = viewChild<ElementRef<HTMLInputElement>>('field');

  protected readonly display = computed(() => {
    const v = this.value();
    if (v === null) return this.nullLabel();
    return this.suffix() ? `${v} ${this.suffix()}` : `${v}`;
  });

  protected readonly decDisabled = computed(() => {
    if (this.disabled()) return true;
    const v = this.value();
    if (v === null) return true;
    return v <= this.min() && !this.nullable();
  });

  protected readonly incDisabled = computed(() => {
    const v = this.value();
    return this.disabled() || (v !== null && v >= this.max());
  });

  constructor() {
    effect(() => {
      const el = this.field()?.nativeElement;
      if (el) {
        el.focus();
        el.select();
      }
    });
  }

  protected step(direction: 1 | -1): void {
    if (direction === 1 ? this.incDisabled() : this.decDisabled()) return;
    const v = this.value();
    if (direction === 1) {
      this.valueChange.emit(v === null ? this.min() : v + 1);
    } else if (v !== null) {
      this.valueChange.emit(v <= this.min() ? null : v - 1);
    }
  }

  protected onGroupKeydown(event: KeyboardEvent): void {
    if (this.editing() || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return;
    event.preventDefault();
    this.step(event.key === 'ArrowUp' ? 1 : -1);
  }

  protected startEdit(): void {
    if (this.disabled()) return;
    const v = this.value();
    this.draft.set(v === null ? '' : String(v));
    this.editing.set(true);
  }

  protected onInput(event: Event): void {
    this.draft.set((event.target as HTMLInputElement).value);
  }

  protected onInputKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      this.commit();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.editing.set(false);
    }
  }

  protected commit(): void {
    if (!this.editing()) return;
    this.editing.set(false);
    const text = this.draft().trim();
    if (text === '') {
      if (this.nullable()) this.valueChange.emit(null);
      return;
    }
    const parsed = Math.trunc(Number(text));
    if (!Number.isFinite(parsed)) return;
    this.valueChange.emit(Math.min(this.max(), Math.max(this.min(), parsed)));
  }
}
