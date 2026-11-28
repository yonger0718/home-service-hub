import {
  ChangeDetectionStrategy, Component, DestroyRef, ElementRef, Injector, afterNextRender, computed, effect, inject, input, output, signal,
  untracked, viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Observable, Subscription } from 'rxjs';

import { EntryDetail } from '../../../models/accounting.model';
import { shortDate, slashDate } from '../dates';
import { EinvoiceLinkTarget, EinvoicePicker, EntryEinvoice } from '../inbox/einvoice.model';
import { invoiceAmount } from '../inbox/einvoice-format';
import { einvoiceErrorText } from '../inbox/einvoice-section/einvoice-section';
import { EinvoiceService } from '../inbox/einvoice.service';

/** One line in the item box: an item, the 「… 另外 N 項」 sum of the rest, or the 合計 row. */
export interface EinvoiceCardRow {
  kind: 'item' | 'rest' | 'total';
  name: string;
  amount: string;
}

/** The linked card's derived text (D12). */
export interface EinvoiceCardView {
  title: string;
  match: { tone: 'ok' | 'warn'; text: string } | null;
  source: string;
  meta: string;
  /** Group only: 「看 N 項明細」, toggling the item box; null for an entry (its box always shows) or an empty invoice. */
  toggle: string | null;
  rows: EinvoiceCardRow[];
}

const SHOWN_ITEMS = 3;

const cents = (value: string): number => Math.round(Number(value) * 100);
const fromCents = (value: number): string => String(value / 100);

/** The linked invoice's card text: match / source chips, the meta line and the item rows (all items for a group). */
export function einvoiceCardView(inv: EntryEinvoice): EinvoiceCardView {
  const group = inv.linked_to === 'group';
  const amount = invoiceAmount;
  const recorded = inv.recorded_amount ?? null;
  const diff = recorded === null ? null : Math.abs(cents(inv.total_amount) - cents(recorded));
  const match = diff === null ? null : diff === 0 ? { tone: 'ok' as const, text: '✓ 金額相符' } : { tone: 'warn' as const, text: `差 ${amount(fromCents(diff))}` };
  const count = inv.items.length;
  const total: EinvoiceCardRow = { kind: 'total', name: `合計 ${count} 項`, amount: amount(inv.total_amount) };
  const item = (i: { name: string; subtotal: string }): EinvoiceCardRow => ({ kind: 'item', name: i.name, amount: amount(i.subtotal) });
  let rows: EinvoiceCardRow[] = [];
  if (count > 0) {
    if (group || count <= SHOWN_ITEMS) {
      rows = [...inv.items.map(item), total];
    } else {
      const rest = inv.items.slice(SHOWN_ITEMS);
      const restSum = rest.reduce((sum, i) => sum + cents(i.subtotal), 0);
      rows = [...inv.items.slice(0, SHOWN_ITEMS).map(item), { kind: 'rest', name: `… 另外 ${rest.length} 項`, amount: amount(fromCents(restSum)) }, total];
    }
  }
  const head = `${inv.seller_name} · ${shortDate(inv.issue_date)}`;
  const note = group && recorded !== null && diff !== 0 ? `（這組記了 ${amount(recorded)}）` : '';
  return {
    title: group ? '發票 · 整組' : '發票',
    match,
    source: inv.linked_by === 'auto' ? '自動連結' : '手動連結',
    meta: group ? `${head} · ${count} 項 · ${amount(inv.total_amount)}${note}` : `${head} · ${inv.number}`,
    toggle: group && count > 0 ? `看 ${count} 項明細` : null,
    rows,
  };
}

type PickerState = { status: 'idle' } | { status: 'loading' } | { status: 'failed' } | { status: 'ready'; picker: EinvoicePicker };

/**
 * 發票 card on the entry detail (spec §10, D12): the linked invoice with match / source chips, its items and a ⋯ menu
 * (換一張發票, 解除連結), or a picker to link one.
 */
@Component({
  selector: 'app-entry-einvoice-card',
  standalone: true,
  templateUrl: './einvoice-card.html',
  styleUrl: './einvoice-card.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(document:click)': 'outsideClick($event)' },
})
export class AppEntryEinvoiceCardComponent {
  private readonly service = inject(EinvoiceService);
  private readonly destroyRef = inject(DestroyRef);

  readonly detail = input.required<EntryDetail>();
  readonly changed = output<void>();
  /** The calendar drawer: unlinked, the idle row reads the picker once to hint how many invoices can be picked. */
  readonly compact = input(false);

  readonly slashDate = slashDate;
  readonly amount = invoiceAmount;

  private readonly injector = inject(Injector);
  private readonly moreButton = viewChild<ElementRef<HTMLButtonElement>>('more');
  private readonly menuWrap = viewChild<ElementRef<HTMLElement>>('menuWrap');
  private readonly menu = viewChild<ElementRef<HTMLElement>>('menu');

  readonly card = computed(() => {
    const inv = this.detail().einvoice;
    return inv ? einvoiceCardView(inv) : null;
  });
  readonly menuOpen = signal(false);
  /** Group card: the item box behind 看 N 項明細. */
  readonly itemsOpen = signal(false);
  /** 換一張發票 is offered only where the unlinked entry keeps the card (it may be a target). */
  readonly canReplace = computed(() => this.maybeTarget());
  /** Set by 換一張發票: once the reloaded detail has no invoice, the picker opens. */
  private reopenPicker = false;

  readonly pickerState = signal<PickerState>({ status: 'idle' });
  readonly scope = signal<'entry' | 'group' | null>(null);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  /** The compact prefetch: its picker (for this entry only), 'failed', or null while pending / not asked. Never `pickerState`. */
  readonly hint = signal<{ entryId: number; picker: EinvoicePicker } | 'failed' | null>(null);
  private readonly heldPicker = computed(() => {
    const hint = this.hint();
    return hint !== null && hint !== 'failed' && hint.entryId === this.detail().id ? hint.picker : null;
  });
  /** `前後 7 天有 n 張可選` / `前後 7 天沒有可選的發票`; nothing while loading, failed or not eligible. */
  readonly hintText = computed(() => {
    const picker = this.heldPicker();
    if (!picker || !this.eligible(picker)) {
      return null;
    }
    return picker.invoices.length > 0 ? `前後 7 天有 ${picker.invoices.length} 張可選` : '前後 7 天沒有可選的發票';
  });

  /**
   * A linked invoice always shows. Unlinked, the card shows only for an entry that may be a link target: a top-level
   * expense, or a split-group member (its group may be one). The picker stays the final check: the card hides once
   * it says neither the entry nor its group is eligible (spec §10).
   */
  private readonly maybeTarget = computed(() => {
    const detail = this.detail();
    return (detail.kind === 'expense' && detail.parent_entry_id == null) || detail.group?.kind === 'split';
  });

  readonly visible = computed(() => {
    const detail = this.detail();
    if (detail.einvoice) {
      return true;
    }
    const maybe = this.maybeTarget();
    const state = this.pickerState();
    if (this.compact() && maybe && state.status === 'idle') {
      // Pending or failed: the row stays (no hint). Only a settled not-eligible answer hides the card.
      const held = this.heldPicker();
      return held === null || this.eligible(held);
    }
    return maybe && !(state.status === 'ready' && !this.eligible(state.picker));
  });

  private pickerSub?: Subscription;
  private hintSub?: Subscription;

  constructor() {
    const key = computed(() => `${this.detail().id}:${this.detail().einvoice?.id ?? ''}`);
    effect(() => {
      key();
      const compact = this.compact();
      untracked(() => {
        const reopen = this.reopenPicker;
        this.reopenPicker = false;
        this.menuOpen.set(false);
        this.itemsOpen.set(false);
        this.pickerSub?.unsubscribe();
        this.hintSub?.unsubscribe();
        this.pickerState.set({ status: 'idle' });
        this.hint.set(null);
        this.scope.set(null);
        this.busy.set(false);
        this.error.set(null);
        if (reopen && !this.detail().einvoice && this.maybeTarget()) {
          this.openPicker();
        } else if (compact && !this.detail().einvoice && this.maybeTarget()) {
          this.prefetch();
        }
      });
    });
  }

  private prefetch(): void {
    const entryId = this.detail().id;
    this.hintSub = this.service.picker(entryId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (picker) => this.hint.set({ entryId, picker }),
      error: () => this.hint.set('failed'),
    });
  }

  unlink(id: number): void {
    this.write(this.service.unlink(id));
  }

  toggleMenu(): void {
    if (this.menuOpen()) {
      this.closeMenu(true);
      return;
    }
    this.menuOpen.set(true);
    afterNextRender(() => this.menuItems()[0]?.focus(), { injector: this.injector });
  }

  closeMenu(refocus: boolean): void {
    this.menuOpen.set(false);
    if (refocus) {
      this.moreButton()?.nativeElement.focus();
    }
  }

  menuKey(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      event.preventDefault();
      this.closeMenu(true);
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const items = this.menuItems();
      const at = items.indexOf(document.activeElement as HTMLButtonElement);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      items[(at + step + items.length) % items.length]?.focus();
    }
  }

  outsideClick(event: MouseEvent): void {
    if (this.menuOpen() && !this.menuWrap()?.nativeElement.contains(event.target as Node)) {
      this.closeMenu(false);
    }
  }

  /** 換一張發票: unlink (the old one goes to 已標成不用記帳), then the reloaded detail opens the picker. */
  replace(id: number): void {
    if (this.busy()) {
      return;
    }
    this.closeMenu(false);
    this.reopenPicker = true;
    this.unlink(id);
  }

  unlinkFromMenu(id: number): void {
    if (this.busy()) {
      return;
    }
    this.closeMenu(false);
    this.unlink(id);
  }

  private menuItems(): HTMLButtonElement[] {
    return [...(this.menu()?.nativeElement.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])];
  }

  openPicker(): void {
    if (this.pickerState().status === 'loading') {
      return;
    }
    this.error.set(null);
    const held = this.heldPicker();
    if (held) {
      this.show(held);
      return;
    }
    this.hintSub?.unsubscribe(); // a click while the prefetch is pending reads afresh
    this.pickerState.set({ status: 'loading' });
    this.pickerSub?.unsubscribe();
    this.pickerSub = this.service.picker(this.detail().id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (picker) => this.show(picker),
      error: () => this.pickerState.set({ status: 'failed' }),
    });
  }

  private show(picker: EinvoicePicker): void {
    this.scope.set(picker.entry_eligible && picker.group_eligible ? null : picker.group_eligible ? 'group' : 'entry');
    this.pickerState.set({ status: 'ready', picker });
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
        this.reopenPicker = false;
        this.error.set(einvoiceErrorText(err));
      },
    });
  }
}
