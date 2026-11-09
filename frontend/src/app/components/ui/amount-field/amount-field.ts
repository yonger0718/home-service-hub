import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

/** Remove thousands separators and whitespace; everything else is left for validation. */
export function stripAmount(raw: string): string {
  return raw.replace(/[,\s]/g, '');
}

/** Insert `,` every three integer digits. String-only so large values keep full precision. */
export function groupAmount(text: string): string {
  const match = /^(-?)(\d+)(\..*)?$/.exec(text);
  if (!match) return text;
  const [, sign, int, frac = ''] = match;
  return sign + int.replace(/\B(?=(\d{3})+$)/g, ',') + frac;
}

@Component({
  selector: 'app-amount-field',
  template: `
    <label class="field" [class.invalid]="invalid()" [class.disabled]="disabled()">
      @if (prefix()) {
        <span class="affix prefix">{{ prefix() }}</span>
      }
      <input
        class="amt"
        inputmode="decimal"
        autocomplete="off"
        [attr.aria-label]="ariaLabel()"
        [attr.aria-invalid]="invalid() ? 'true' : null"
        [attr.placeholder]="placeholder() || null"
        [disabled]="disabled()"
        [value]="shown()"
        (focus)="focused.set(true)"
        (blur)="focused.set(false)"
        (input)="onInput($event)"
      />
      @if (suffix()) {
        <span class="affix suffix">{{ suffix() }}</span>
      }
    </label>
  `,
  styleUrl: './amount-field.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AmountFieldComponent {
  readonly value = input('');
  readonly prefix = input('');
  readonly suffix = input('');
  readonly ariaLabel = input.required<string>();
  readonly placeholder = input('');
  readonly disabled = input(false);
  readonly invalid = input(false);
  readonly valueChange = output<string>();

  protected readonly focused = signal(false);
  protected readonly shown = computed(() =>
    this.focused() ? this.value() : groupAmount(this.value()),
  );

  protected onInput(event: Event): void {
    this.valueChange.emit(stripAmount((event.target as HTMLInputElement).value));
  }
}
