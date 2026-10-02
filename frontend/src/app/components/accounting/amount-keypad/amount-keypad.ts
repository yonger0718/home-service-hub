import { ChangeDetectionStrategy, Component, computed, input, model, output, signal } from '@angular/core';

import { KeypadLayout } from '../../../models/accounting.model';
import { KEYPAD_CALCULATOR, KEYPAD_PHONE, amountErrorText, applyKey, keypadState } from '../amount-math';

const KEY_LABELS: Record<string, string> = {
  '÷': '除',
  '×': '乘',
  '−': '減',
  '+': '加',
  '⌫': '刪除',
  C: '清除',
  '↵': '下一欄',
  '✓': '儲存',
  '.': '小數點',
};

const FUNCTION_KEYS = new Set(['÷', '×', '−', '+', '⌫', 'C', '↵']);

/** Phone amount keypad (spec "Amount input"); `value` is the expression text, evaluated on ↵ and ✓. */
@Component({
  selector: 'app-amount-keypad',
  standalone: true,
  templateUrl: './amount-keypad.html',
  styleUrl: './amount-keypad.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AmountKeypadComponent {
  readonly layout = input<KeypadLayout>('calculator');
  readonly quickAmounts = input<number[]>([]);
  readonly decimals = input(0);
  readonly value = model('');
  /** ↵: move to the next field; carries the evaluated amount (null when empty). */
  readonly next = output<number | null>();
  /** ✓: save; carries the evaluated amount (null when empty, so the form can say so). */
  readonly save = output<number | null>();

  readonly error = signal<string | null>(null);
  readonly errorText = computed(() => {
    const error = this.error();
    return error === null ? null : amountErrorText(error);
  });
  readonly rows = computed(() => (this.layout() === 'phone' ? KEYPAD_PHONE : KEYPAD_CALCULATOR));

  press(key: string): void {
    const state = applyKey(keypadState(this.value(), this.decimals()), key);
    this.value.set(state.expression);
    this.error.set(state.error);
    if (state.error) {
      return;
    }
    const amount = state.expression === '' ? null : Number(state.expression.replace('−', '-'));
    if (key === '↵') {
      this.next.emit(amount);
    } else if (key === '✓') {
      this.save.emit(amount);
    }
  }

  pickQuick(amount: number): void {
    this.value.set(String(amount));
    this.error.set(null);
  }

  isFunction(key: string): boolean {
    return FUNCTION_KEYS.has(key);
  }

  ariaLabel(key: string): string {
    return KEY_LABELS[key] ?? key;
  }

  quickLabel(amount: number): string {
    return amount.toLocaleString('en-US');
  }
}
