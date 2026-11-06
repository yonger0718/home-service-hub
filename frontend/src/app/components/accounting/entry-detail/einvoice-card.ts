import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Observable, Subscription } from 'rxjs';

import { EntryDetail } from '../../../models/accounting.model';
import { slashDate } from '../dates';
import { EinvoiceLinkTarget, EinvoicePicker } from '../inbox/einvoice.model';
import { invoiceAmount } from '../inbox/einvoice-format';
import { einvoiceErrorText } from '../inbox/einvoice-section/einvoice-section';
import { EinvoiceService } from '../inbox/einvoice.service';

type PickerState = { status: 'idle' } | { status: 'loading' } | { status: 'failed' } | { status: 'ready'; picker: EinvoicePicker };

/** 發票 card on the entry detail: the linked invoice with 解除連結, or a picker to link one (spec §10). */
@Component({
  selector: 'app-entry-einvoice-card',
  standalone: true,
  templateUrl: './einvoice-card.html',
  styles: `
    .einvoice-pick { align-items: flex-start; background: none; border: 0; border-bottom: 1px solid var(--app-border); color: inherit; display: flex; flex-direction: column; font: inherit; font-size: 0.88rem; gap: 2px; min-height: 44px; padding: 8px 0; text-align: left; width: 100%; }
    .einvoice-sub { color: var(--app-text-muted); font-size: 0.8rem; }
    .einvoice-error { color: var(--app-danger); font-size: 0.85rem; margin: 4px 0 0; }
    .einvoice-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    .einvoice-actions button { min-height: 44px; }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppEntryEinvoiceCardComponent {
  private readonly service = inject(EinvoiceService);
  private readonly destroyRef = inject(DestroyRef);

  readonly detail = input.required<EntryDetail>();
  readonly changed = output<void>();

  readonly slashDate = slashDate;
  readonly amount = invoiceAmount;

  readonly pickerState = signal<PickerState>({ status: 'idle' });
  readonly scope = signal<'entry' | 'group' | null>(null);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);

  /** The card shows nothing once the picker says the entry is not eligible (spec §10). */
  readonly visible = computed(() => {
    const state = this.pickerState();
    return !(state.status === 'ready' && !this.eligible(state.picker));
  });

  private pickerSub?: Subscription;

  constructor() {
    const key = computed(() => `${this.detail().id}:${this.detail().einvoice?.id ?? ''}`);
    effect(() => {
      key();
      this.pickerSub?.unsubscribe();
      this.pickerState.set({ status: 'idle' });
      this.scope.set(null);
      this.busy.set(false);
      this.error.set(null);
    });
  }

  unlink(id: number): void {
    this.write(this.service.unlink(id));
  }

  openPicker(): void {
    if (this.pickerState().status === 'loading') {
      return;
    }
    this.error.set(null);
    this.pickerState.set({ status: 'loading' });
    this.pickerSub?.unsubscribe();
    this.pickerSub = this.service.picker(this.detail().id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (picker) => {
        this.scope.set(picker.entry_eligible && picker.group_eligible ? null : picker.group_eligible ? 'group' : 'entry');
        this.pickerState.set({ status: 'ready', picker });
      },
      error: () => this.pickerState.set({ status: 'failed' }),
    });
  }

  choose(scope: 'entry' | 'group'): void {
    this.scope.set(scope);
  }

  link(invoiceId: number, picker: EinvoicePicker): void {
    const scope = this.scope();
    if (!scope) {
      return;
    }
    const target: EinvoiceLinkTarget = scope === 'group' ? { group_id: picker.group_id as number } : { entry_id: this.detail().id };
    this.write(this.service.link(invoiceId, target));
  }

  eligible(picker: EinvoicePicker): boolean {
    return picker.entry_eligible || picker.group_eligible;
  }

  askScope(picker: EinvoicePicker): boolean {
    return picker.entry_eligible && picker.group_eligible && this.scope() === null;
  }

  private write(call: Observable<unknown>): void {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    call.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        // busy stays on until the reloaded detail resets the card, so a second tap cannot send another write.
        this.changed.emit();
      },
      error: (err: unknown) => {
        this.busy.set(false);
        this.error.set(einvoiceErrorText(err));
      },
    });
  }
}
