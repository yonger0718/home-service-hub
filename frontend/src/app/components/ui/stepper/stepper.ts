import { DOCUMENT } from '@angular/common';
import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  ElementRef,
  inject,
  Injector,
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
        #dec
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
          (blur)="onBlur()"
        />
      } @else {
        <button
          #val
          type="button"
          class="val"
          [attr.aria-label]="display() + '，輸入數值'"
          [disabled]="disabled()"
          (click)="startEdit()"
        >{{ display() }}</button>
      }
      <button
        #inc
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
  private readonly valButton = viewChild<ElementRef<HTMLButtonElement>>('val');
  private readonly decButton = viewChild.required<ElementRef<HTMLButtonElement>>('dec');
  private readonly incButton = viewChild.required<ElementRef<HTMLButtonElement>>('inc');
  private readonly injector = inject(Injector);
  private readonly document = inject(DOCUMENT);

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
    // At max (or min / null) the pressed button turns disabled and would drop focus to <body>; hand it to the value.
    const button = (direction === 1 ? this.incButton() : this.decButton()).nativeElement;
    this.focusValueAfterRender(() => button.disabled && (this.document.activeElement === button || this.focusLost()));
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
      this.focusValueAfterRender();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.editing.set(false);
      this.focusValueAfterRender();
    }
  }

  /** Blur commits; focus returns to the value only when it fell to <body>, never when the user moved elsewhere. */
  protected onBlur(): void {
    if (!this.editing()) return;
    this.commit();
    this.focusValueAfterRender(() => this.focusLost());
  }

  private focusLost(): boolean {
    const active = this.document.activeElement;
    return active === null || active === this.document.body;
  }

  /** The value button is re-created by `@if` when editing ends, so focus waits for the next render. */
  private focusValueAfterRender(when: () => boolean = () => true): void {
    afterNextRender(
      () => {
        if (when()) this.valButton()?.nativeElement.focus();
      },
      { injector: this.injector },
    );
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
