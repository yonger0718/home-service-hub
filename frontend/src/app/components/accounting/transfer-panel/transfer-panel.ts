import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { Observable } from 'rxjs';

import { CategoryNode, ChildInput, LedgerAccount, LedgerEntry, TransferInput } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AmountKeypadComponent } from '../amount-keypad/amount-keypad';
import { amountString, parseAmountText } from '../amount-text';
import { TransferCommon, TransferEdit, buildTransferInput, transferRateLabel } from '../entry-form/transfer-math';
import { currencyDecimals, formatAmount } from '../format';

export type TransferSide = 'out' | 'in';

export type { TransferEdit } from '../entry-form/transfer-math';

interface SideChildren {
  fee: string;
  discount: string;
}

const EMPTY_CHILDREN: SideChildren = { fee: '', discount: '' };

/**
 * A ⏎ or Esc a bottom sheet handles itself (and marks handled, so the entry form neither saves nor leaves); null for
 * IME commits, other keys, and ⏎ on a button (left to its native click).
 */
export function sheetKey(event: KeyboardEvent): 'Enter' | 'Escape' | null {
  if (event.isComposing || event.keyCode === 229) {
    return null;
  }
  if (event.key === 'Escape') {
    return 'Escape';
  }
  return event.key === 'Enter' && (event.target as HTMLElement | null)?.tagName !== 'BUTTON' ? 'Enter' : null;
}

function childrenFrom(children: LedgerEntry[] | undefined): SideChildren {
  const sum = (kind: string) =>
    (children ?? []).filter(child => child.kind === kind).reduce((total, child) => total + Math.abs(Number(child.amount)), 0);
  const fee = sum('fee');
  const discount = sum('discount');
  return { fee: fee ? String(fee) : '', discount: discount ? String(discount) : '' };
}

@Component({
  selector: 'app-transfer-panel',
  standalone: true,
  imports: [AmountKeypadComponent],
  templateUrl: './transfer-panel.html',
  styleUrls: ['../sheet.scss', './transfer-panel.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TransferPanelComponent implements OnInit {
  private service = inject(AccountingService);
  private layout = inject(LayoutModeService);

  readonly accounts = input<LedgerAccount[]>([]);
  readonly locked = input(false);
  readonly keypadLayout = input<'calculator' | 'phone'>('calculator');
  readonly edit = input<TransferEdit | null>(null);
  readonly saveRequested = output<boolean>();

  readonly categories = signal<CategoryNode[]>([]);
  readonly categoryId = signal<number | null>(null);
  readonly fromId = signal<number | null>(null);
  readonly toId = signal<number | null>(null);
  readonly outText = signal('');
  readonly inText = signal('');
  readonly children = signal<Record<TransferSide, SideChildren>>({ out: EMPTY_CHILDREN, in: EMPTY_CHILDREN });
  readonly sheet = signal<TransferSide | null>(null);
  readonly activeSide = signal<TransferSide | null>(null);
  readonly error = signal<string | null>(null);

  readonly activeAccounts = computed(() => this.accounts().filter(account => !account.is_archived));
  readonly from = computed(() => this.accounts().find(account => account.id === this.fromId()) ?? null);
  readonly to = computed(() => this.accounts().find(account => account.id === this.toId()) ?? null);
  readonly sameCurrency = computed(() => {
    const from = this.from();
    const to = this.to();
    return !from || !to || from.currency === to.currency;
  });
  readonly outAmount = computed(() => {
    const from = this.from();
    return from ? parseAmountText(this.outText(), from.currency) : null;
  });
  readonly inAmount = computed(() => {
    const to = this.to();
    if (!to) {
      return null;
    }
    return this.sameCurrency() ? this.outAmount() : parseAmountText(this.inText(), to.currency);
  });
  readonly inDisplay = computed(() => {
    if (!this.sameCurrency()) {
      return this.inText();
    }
    const value = this.outAmount();
    const to = this.to();
    return value !== null && to ? amountString(value, to.currency) : '';
  });
  readonly rateLabel = computed(() =>
    transferRateLabel(this.outAmount(), this.inAmount(), this.from()?.currency ?? '', this.to()?.currency ?? ''),
  );
  readonly phone = computed(() => this.layout.mode() === 'phone');
  readonly showKeypad = computed(() => this.phone() && this.activeSide() !== null && !this.locked());
  readonly activeText = computed(() => (this.activeSide() === 'in' ? this.inText() : this.outText()));
  /** Decimals the keypad accepts for the active side (Task 19's `decimals` input). */
  readonly activeDecimals = computed(() => {
    const account = this.activeSide() === 'in' ? this.to() : this.from();
    return account ? currencyDecimals(account.currency) : 0;
  });

  readonly formatAmount = formatAmount;

  constructor() {
    effect(() => {
      const list = this.activeAccounts();
      untracked(() => {
        if (this.fromId() === null && list.length > 0) {
          this.fromId.set(list[0].id);
        }
        if (this.toId() === null && list.length > 1) {
          this.toId.set(list[1].id);
        }
      });
    });
    effect(() => {
      const edit = this.edit();
      if (edit) {
        untracked(() => this.applyEdit(edit));
      }
    });
  }

  ngOnInit(): void {
    this.service.getCategories('transfer_out').subscribe(tree => {
      const visible = tree.filter(node => !node.is_hidden);
      this.categories.set(visible);
      if (this.categoryId() === null && visible.length > 0) {
        this.categoryId.set(visible[0].id);
      }
    });
  }

  setFrom(value: string): void {
    this.fromId.set(Number(value));
  }

  setTo(value: string): void {
    this.toId.set(Number(value));
  }

  setText(side: TransferSide, value: string): void {
    (side === 'out' ? this.outText : this.inText).set(value);
  }

  setActiveText(value: string): void {
    this.setText(this.activeSide() ?? 'out', value);
  }

  /** On blur or ⏎: replace a valid expression by its value. */
  normalize(side: TransferSide): void {
    const account = side === 'out' ? this.from() : this.to();
    const text = side === 'out' ? this.outText() : this.inText();
    if (!account || !text.trim()) {
      return;
    }
    const value = parseAmountText(text, account.currency);
    if (value === null) {
      this.error.set('金額格式錯誤');
      return;
    }
    this.error.set(null);
    this.setText(side, amountString(value, account.currency));
  }

  /**
   * ⏎ in an amount: normalize it, then ask the form to save (⇧⏎: save and continue), as ⏎ in the form's own amount
   * does. Marked handled either way, so the entry form's ⏎ handler neither saves nor reads this input as its amount.
   */
  onAmountKeydown(side: TransferSide, event: KeyboardEvent): void {
    // Plain `keydown`, not `keydown.enter`: Angular's `enter` filter would skip ⇧⏎.
    if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) {
      return;
    }
    event.preventDefault();
    this.normalize(side);
    if (!this.error()) {
      this.saveRequested.emit(event.shiftKey);
    }
  }

  /** ⏎ / Esc inside the fee sheet close the sheet only; the form must not save or leave. ⏎ on a button clicks it. */
  onSheetKeydown(event: KeyboardEvent): void {
    if (sheetKey(event)) {
      event.preventDefault();
      this.sheet.set(null);
    }
  }

  nextSide(): void {
    this.activeSide.set(this.activeSide() === 'out' && !this.sameCurrency() ? 'in' : null);
  }

  swap(): void {
    const [fromId, toId] = [this.toId(), this.fromId()];
    const [outText, inText] = [this.inDisplay(), this.outText()];
    const current = this.children();
    this.fromId.set(fromId);
    this.toId.set(toId);
    this.outText.set(outText);
    this.inText.set(inText);
    this.children.set({ out: current.in, in: current.out });
  }

  openSheet(side: TransferSide, event: Event): void {
    event.stopPropagation();
    if (!this.locked()) {
      this.sheet.set(side);
    }
  }

  setChild(side: TransferSide, field: keyof SideChildren, value: string): void {
    this.children.update(current => ({ ...current, [side]: { ...current[side], [field]: value } }));
  }

  sideLabel(side: TransferSide): string {
    const account = side === 'out' ? this.from() : this.to();
    const value = side === 'out' ? this.outAmount() : this.inAmount();
    return account ? formatAmount(value ?? 0, account.currency) : '—';
  }

  private child(side: TransferSide, field: keyof SideChildren, name: string): ChildInput | null {
    const account = side === 'out' ? this.from() : this.to();
    if (!account) {
      return null;
    }
    const value = parseAmountText(this.children()[side][field], account.currency);
    return value === null ? null : { amount: amountString(value, account.currency), name };
  }

  /** 連續記帳: a fresh transfer between the same accounts in the same category. */
  reset(): void {
    this.outText.set('');
    this.inText.set('');
    this.children.set({ out: EMPTY_CHILDREN, in: EMPTY_CHILDREN });
    this.sheet.set(null);
    this.activeSide.set(null);
    this.error.set(null);
  }

  /** The request body, or null (with `error` set) when the panel is incomplete. */
  buildInput(common: TransferCommon): TransferInput | null {
    const from = this.from();
    const to = this.to();
    const outAmount = this.outAmount();
    const inAmount = this.inAmount();
    if (!from || !to) {
      this.error.set('請選擇轉出與轉入帳戶');
      return null;
    }
    if (from.id === to.id) {
      this.error.set('轉出與轉入帳戶不可相同');
      return null;
    }
    if (outAmount === null) {
      this.error.set('請輸入轉出金額');
      return null;
    }
    if (inAmount === null) {
      this.error.set('請輸入轉入金額');
      return null;
    }
    this.error.set(null);
    return buildTransferInput(
      {
        categoryId: this.categoryId(),
        fromAccount: from,
        toAccount: to,
        outAmount,
        inAmount,
        outFee: this.child('out', 'fee', '手續費'),
        outDiscount: this.child('out', 'discount', '折扣'),
        inFee: this.child('in', 'fee', '手續費'),
        inDiscount: this.child('in', 'discount', '折扣'),
      },
      common,
    );
  }

  /** `POST /transfers` for a new transfer, `PUT /transfers/{groupId}` when editing; null when invalid. */
  submit(common: TransferCommon, groupId: string | null): Observable<unknown> | null {
    const body = this.buildInput(common);
    if (!body) {
      return null;
    }
    return groupId ? this.service.updateTransfer(groupId, body) : this.service.createTransfer(body);
  }

  private applyEdit(edit: TransferEdit): void {
    this.fromId.set(edit.out.account_id);
    this.toId.set(edit.in.account_id);
    this.categoryId.set(edit.out.category_id ?? this.categoryId());
    this.outText.set(String(Math.abs(Number(edit.out.amount))));
    this.inText.set(String(Math.abs(Number(edit.in.amount))));
    this.children.set({ out: childrenFrom(edit.out.children), in: childrenFrom(edit.in.children) });
  }
}
