import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
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
import { accountLabel, focusSheetField, isHandledKey, sheetKeyAction } from '../accounting-ui';
import { currencyDecimals, formatMoney } from '../format';

export type TransferSide = 'out' | 'in';

export type { TransferEdit } from '../entry-form/transfer-math';

interface SideChildren {
  fee: string;
  discount: string;
}

const EMPTY_CHILDREN: SideChildren = { fee: '', discount: '' };
const CATEGORIES_FAILED = '轉帳類別讀取失敗，請重新整理';

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
  host: { '(keydown)': 'onHostKeydown($event)' },
})
export class TransferPanelComponent implements OnInit {
  private service = inject(AccountingService);
  private layout = inject(LayoutModeService);
  private host = inject<ElementRef<HTMLElement>>(ElementRef);
  private injector = inject(Injector);

  readonly accounts = input<LedgerAccount[]>([]);
  readonly locked = input(false);
  readonly keypadLayout = input<'calculator' | 'phone'>('calculator');
  readonly edit = input<TransferEdit | null>(null);
  /** Editing a saved transfer: an archived leg stays selectable (labelled). A copy moves it to an open account. */
  readonly keepArchived = input(false);
  /** 週期 on the entry form: schedules carry no fee / discount, so both "+" buttons are hidden (排程不支援). */
  readonly scheduled = input(false);
  readonly saveRequested = output<boolean>();

  readonly categories = signal<CategoryNode[]>([]);
  readonly categoryTouched = signal(false);
  readonly categoryId = signal<number | null>(null);
  readonly fromId = signal<number | null>(null);
  readonly toId = signal<number | null>(null);
  readonly outText = signal('');
  readonly inText = signal('');
  readonly children = signal<Record<TransferSide, SideChildren>>({ out: EMPTY_CHILDREN, in: EMPTY_CHILDREN });
  readonly sheet = signal<TransferSide | null>(null);
  readonly activeSide = signal<TransferSide | null>(null);
  readonly error = signal<string | null>(null);
  private readonly categoriesFailed = signal(false);

  readonly activeAccounts = computed(() => this.accounts().filter(account => !account.is_archived));
  /** Select options: open accounts, plus an archived leg of the transfer being edited. */
  readonly legOptions = computed(() =>
    this.accounts().filter(
      account =>
        !account.is_archived || (this.keepArchived() && (account.id === this.fromId() || account.id === this.toId())),
    ),
  );
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
  /** Defaults and hydrated categories are derived; explicit category choices belong to the draft. */
  readonly draftKey = computed(() => JSON.stringify([
    this.fromId(), this.toId(), this.outText(), this.inText(), this.children(),
    this.categoryTouched() ? this.categoryId() : null,
  ]));

  selectCategory(id: number): void {
    this.categoryTouched.set(true);
    this.categoryId.set(id);
  }

  markClean(): void {
    this.categoryTouched.set(false);
  }

  readonly phone = computed(() => this.layout.mode() === 'phone');
  readonly showKeypad = computed(() => this.phone() && this.activeSide() !== null && !this.locked());
  readonly activeText = computed(() => (this.activeSide() === 'in' ? this.inText() : this.outText()));
  /** Decimals the keypad accepts for the active side (Task 19's `decimals` input). */
  readonly activeDecimals = computed(() => {
    const account = this.activeSide() === 'in' ? this.to() : this.from();
    return account ? currencyDecimals(account.currency) : 0;
  });

  readonly formatMoney = formatMoney;
  readonly accountLabel = accountLabel;

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
    this.service.getCategories('transfer_out').subscribe({
      next: tree => {
        const visible = tree.filter(node => !node.is_hidden);
        this.categories.set(visible);
        if (this.categoryId() === null && visible.length > 0) {
          this.categoryId.set(visible[0].id);
        }
      },
      error: () => {
        this.categoriesFailed.set(true);
        this.error.set(CATEGORIES_FAILED);
      },
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
    // Plain `keydown`, not `keydown.enter`: Angular's `enter` filter would skip ⇧⏎. With a sheet open, the host decides.
    if (event.key !== 'Enter' || isHandledKey(event) || this.sheet()) {
      return;
    }
    event.preventDefault();
    this.normalize(side);
    if (!this.error()) {
      this.saveRequested.emit(event.shiftKey);
    }
  }

  /** While the fee sheet is open, ⏎ / Esc anywhere in the panel close it (see `sheetKeyAction`); the form never sees them. */
  onHostKeydown(event: KeyboardEvent): void {
    if (this.sheet() && sheetKeyAction(event, this.host.nativeElement.querySelector('.sheet'))) {
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
      focusSheetField(this.host.nativeElement, '.fee-input', this.injector);
    }
  }

  setChild(side: TransferSide, field: keyof SideChildren, value: string): void {
    this.children.update(current => ({ ...current, [side]: { ...current[side], [field]: value } }));
  }

  sideLabel(side: TransferSide): string {
    const account = side === 'out' ? this.from() : this.to();
    const value = side === 'out' ? this.outAmount() : this.inAmount();
    return account ? formatMoney(value ?? 0, account.currency) : '—';
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
    this.markClean();
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
    if (this.categoryId() === null && this.categoriesFailed()) {
      this.error.set(CATEGORIES_FAILED);
      return null;
    }
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
    // A copy is a new transfer: it never lands on an archived account (as the entry form's copy).
    const usable = (id: number, other: number | null) =>
      this.keepArchived() || this.activeAccounts().some(account => account.id === id)
        ? id
        : (this.activeAccounts().find(account => account.id !== other)?.id ?? null);
    const fromId = usable(edit.out.account_id, edit.in.account_id);
    this.fromId.set(fromId);
    this.toId.set(usable(edit.in.account_id, fromId));
    this.categoryId.set(edit.out.category_id ?? this.categoryId());
    this.outText.set(String(Math.abs(Number(edit.out.amount))));
    this.inText.set(String(Math.abs(Number(edit.in.amount))));
    this.children.set({ out: childrenFrom(edit.out.children), in: childrenFrom(edit.in.children) });
  }
}
