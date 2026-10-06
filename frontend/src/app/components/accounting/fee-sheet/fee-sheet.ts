import { ChangeDetectionStrategy, Component, OnInit, ElementRef, Injector, afterNextRender, inject, computed, input, model, output, signal } from '@angular/core';

import { ChildInput, ENTRY_KIND_LABELS, EntryKind } from '../../../models/accounting.model';
import { focusables, isHandledKey, trapFocus, restoreOverlayFocus } from '../accounting-ui';
import { roundHalfAway } from '../amount-math';
import { currencyDecimals, formatNumber } from '../format';

function positive(text: string): number {
  const value = Number(text.replace(/,/g, ''));
  return Number.isFinite(value) && value > 0 ? value : 0;
}

/** 手續費 / 折扣 sheet (mockup `ov-fee`): 金額, 手續費 ✎, 折扣 ✎, 總額. */
@Component({
  selector: 'app-fee-sheet',
  standalone: true,
  templateUrl: './fee-sheet.html',
  styleUrls: ['../sheet.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onHostKeydown($event)' },
})
export class FeeSheetComponent implements OnInit {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private opener: HTMLElement | null = null;

  /** Signed main amount in the account currency (−1166 for an expense). */
  readonly amount = input.required<number>();
  readonly currency = input.required<string>();
  readonly kind = input('expense');
  readonly fee = model<ChildInput | null>(null);
  readonly discount = model<ChildInput | null>(null);
  readonly closed = output<void>();

  readonly feeText = signal('');
  readonly feeName = signal('');
  readonly discountText = signal('');
  readonly discountName = signal('');
  readonly editingFeeName = signal(false);
  readonly editingDiscountName = signal(false);

  readonly title = computed(() => ENTRY_KIND_LABELS[this.kind() as EntryKind] ?? '金額');
  readonly total = computed(() => this.amount() - positive(this.feeText()) + positive(this.discountText()));
  readonly formatNumber = formatNumber;

  ngOnInit(): void {
    this.opener = this.host.nativeElement.ownerDocument.activeElement as HTMLElement | null;
    afterNextRender(() => {
      const dialog = this.dialog();
      if (dialog) focusables(dialog)[0]?.focus();
    }, { injector: this.injector });
    this.feeText.set(this.fee()?.amount ?? '');
    this.feeName.set(this.fee()?.name ?? '');
    this.discountText.set(this.discount()?.amount ?? '');
    this.discountName.set(this.discount()?.name ?? '');
  }

  confirm(): void {
    this.fee.set(this.child(this.feeText(), this.feeName()));
    this.discount.set(this.child(this.discountText(), this.discountName()));
    restoreOverlayFocus(this.opener, this.host.nativeElement);
    this.closed.emit();
  }

  cancel(): void {
    restoreOverlayFocus(this.opener, this.host.nativeElement);
    this.closed.emit();
  }

  private child(amountText: string, name: string): ChildInput | null {
    const amount = positive(amountText);
    if (!amount) {
      return null;
    }
    return { amount: String(roundHalfAway(amount, currencyDecimals(this.currency()))), name: name.trim() || null };
  }
  private dialog(): HTMLElement | null {
    return this.host.nativeElement.querySelector<HTMLElement>('.sheet');
  }

  /** Overlay contract (spec §3.1): Esc closes only this sheet, marked handled; Tab stays inside it. */
  onHostKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      this.cancel();
      return;
    }
    const dialog = this.dialog();
    if (event.key === 'Tab' && dialog) {
      trapFocus(dialog, event);
    }
  }

}
