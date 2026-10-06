import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  computed,
  inject,
  input,
  model,
  output,
  signal,
} from '@angular/core';

import { CategoryNode, Counterparty, EntryInput, LedgerAccount } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { amountString, parseAmountText } from '../amount-text';
import { emptyEntryInput, resolveCounterpartyId } from '../entry-form/entry-save';
import { CategoryIconSource, colorOf, focusSheetField, iconOf, sheetKeyAction, trapFocus, restoreOverlayFocus } from '../accounting-ui';
import { formatMoney } from '../format';

export type SplitKind = 'expense' | 'income' | 'receivable' | 'payable';

export const SPLIT_KIND_LABELS: Record<SplitKind, string> = {
  expense: '支出',
  income: '收入',
  receivable: '應收',
  payable: '應付',
};

interface CategoryOption extends CategoryIconSource {
  id: number;
  label: string;
}

function flatten(tree: CategoryNode[], kind: SplitKind): CategoryOption[] {
  const options: CategoryOption[] = [];
  for (const main of tree.filter(node => !node.is_hidden)) {
    options.push({ id: main.id, label: main.name, icon: main.icon, color: main.color, mainName: main.name, kind });
    for (const sub of (main.children ?? []).filter(node => !node.is_hidden)) {
      options.push({
        id: sub.id,
        label: sub.name,
        icon: sub.icon ?? main.icon,
        color: sub.color ?? main.color,
        mainName: main.name,
        kind,
      });
    }
  }
  return options;
}

@Component({
  selector: 'app-split-lines',
  standalone: true,
  templateUrl: './split-lines.html',
  styleUrls: ['../sheet.scss', './split-lines.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onHostKeydown($event)' },
})
export class SplitLinesComponent {
  private service = inject(AccountingService);
  private host = inject<ElementRef<HTMLElement>>(ElementRef);
  private injector = inject(Injector);
  private opener: HTMLElement | null = null;

  readonly members = model<EntryInput[]>([]);
  readonly accounts = input<LedgerAccount[]>([]);
  readonly categories = input<Partial<Record<SplitKind, CategoryNode[]>>>({});
  readonly defaultAccountId = input<number | null>(null);
  readonly disabled = input(false);
  /** The entry form's counterparties; the component fetches its own only when this is empty. */
  readonly knownCounterparties = input<Counterparty[]>([]);
  /** A counterparty created here (typed 對象 not yet known), for the form's list. */
  readonly counterpartyCreated = output<Counterparty>();

  readonly open = signal(false);
  readonly draftKind = signal<SplitKind>('expense');
  readonly draftCategoryId = signal<number | null>(null);
  readonly draftAmount = signal('');
  readonly draftAccountId = signal<number | null>(null);
  readonly draftCounterparty = signal('');
  readonly error = signal<string | null>(null);
  readonly loaded = signal<Partial<Record<SplitKind, CategoryNode[]>>>({});
  /** Counterparties fetched or created here, on top of `knownCounterparties`. */
  private readonly ownCounterparties = signal<Counterparty[]>([]);
  readonly counterparties = computed(() => {
    const known = this.knownCounterparties();
    const ids = new Set(known.map(party => party.id));
    return [...known, ...this.ownCounterparties().filter(party => !ids.has(party.id))];
  });
  private counterpartiesRequested = false;
  /** A line is being added (its counterparty POST in flight): a second ⏎ / 加入 does nothing. */
  readonly adding = signal(false);

  readonly kinds = Object.keys(SPLIT_KIND_LABELS) as SplitKind[];
  readonly kindLabels = SPLIT_KIND_LABELS;
  readonly activeAccounts = computed(() => this.accounts().filter(account => !account.is_archived));
  readonly needsCounterparty = computed(() => this.draftKind() === 'receivable' || this.draftKind() === 'payable');
  readonly categoryOptions = computed(() => flatten(this.treeFor(this.draftKind()), this.draftKind()));
  private readonly allOptions = computed(() => this.kinds.flatMap(kind => flatten(this.treeFor(kind), kind)));

  private treeFor(kind: SplitKind): CategoryNode[] {
    return this.categories()[kind] ?? this.loaded()[kind] ?? [];
  }

  openSheet(event?: Event): void {
    if (this.disabled()) {
      return;
    }
    this.opener = (event?.currentTarget ?? this.host.nativeElement.ownerDocument.activeElement) as HTMLElement | null;
    this.draftKind.set('expense');
    this.draftCategoryId.set(null);
    this.draftAmount.set('');
    this.draftCounterparty.set('');
    this.error.set(null);
    this.draftAccountId.set(this.defaultAccountId() ?? this.activeAccounts()[0]?.id ?? null);
    this.ensureCategories('expense');
    if (!this.counterpartiesRequested && this.knownCounterparties().length === 0) {
      this.counterpartiesRequested = true;
      this.service.getCounterparties().subscribe({
        next: list => this.ownCounterparties.set(list),
        error: () => {
          // Retried on the next open.
          this.counterpartiesRequested = false;
          this.error.set('無法讀取對象');
        },
      });
    }
    this.open.set(true);
    focusSheetField(this.host.nativeElement, '.line-amount', this.injector);
  }

  closeSheet(): void {
    this.open.set(false);
    if (this.opener) restoreOverlayFocus(this.opener, this.host.nativeElement);
    this.opener = null;
  }

  setKind(kind: SplitKind): void {
    this.draftKind.set(kind);
    this.draftCategoryId.set(null);
    this.ensureCategories(kind);
  }

  setCategory(value: string): void {
    this.draftCategoryId.set(value ? Number(value) : null);
  }

  setAccount(value: string): void {
    this.draftAccountId.set(Number(value));
  }

  private ensureCategories(kind: SplitKind): void {
    if (this.categories()[kind] || this.loaded()[kind]) {
      return;
    }
    this.loaded.update(current => ({ ...current, [kind]: [] }));
    this.service.getCategories(kind).subscribe({
      next: tree => this.loaded.update(current => ({ ...current, [kind]: tree })),
      error: () => {
        // Retried on the next open or kind switch.
        this.loaded.update(current => {
          const { [kind]: _failed, ...rest } = current;
          return rest;
        });
        this.error.set('無法讀取分類');
      },
    });
  }

  add(): void {
    if (this.adding()) {
      return;
    }
    const kind = this.draftKind();
    const account = this.activeAccounts().find(candidate => candidate.id === this.draftAccountId());
    if (!account) {
      this.error.set('請選擇帳戶');
      return;
    }
    const value = parseAmountText(this.draftAmount(), account.currency);
    if (value === null) {
      this.error.set('請輸入金額');
      return;
    }
    const finish = (counterpartyId: number | null) => {
      const line = emptyEntryInput({
        account_id: account.id,
        kind,
        amount: amountString(value, account.currency),
        category_id: this.draftCategoryId(),
        counterparty_id: counterpartyId,
      });
      this.members.update(list => [...list, line]);
      this.adding.set(false);
      this.error.set(null);
      this.closeSheet();
    };
    if (!this.needsCounterparty()) {
      finish(null);
      return;
    }
    if (!this.draftCounterparty().trim()) {
      this.error.set('應收／應付需要對象');
      return;
    }
    const created = (party: Counterparty) => {
      this.ownCounterparties.update(list => [...list, party]);
      this.counterpartyCreated.emit(party);
    };
    this.adding.set(true);
    resolveCounterpartyId(this.service, this.draftCounterparty(), this.counterparties(), created).subscribe({
      next: finish,
      error: () => {
        this.adding.set(false);
        this.error.set('無法新增對象');
      },
    });
  }

  /** While the sheet is open: ⏎ in a sheet field adds the line, Esc anywhere closes it (see `sheetKeyAction`). */
  onHostKeydown(event: KeyboardEvent): void {
    if (!this.open()) {
      return;
    }
    const dialog = this.host.nativeElement.querySelector<HTMLElement>('.sheet');
    if (event.key === 'Tab' && dialog) { trapFocus(dialog, event); return; }
    const action = sheetKeyAction(event, dialog);
    if (action === 'confirm') {
      this.add();
    } else if (action === 'close') {
      this.closeSheet();
    }
  }

  remove(index: number): void {
    this.members.update(list => list.filter((_, position) => position !== index));
  }

  label(line: EntryInput): string {
    const category = this.allOptions().find(option => option.id === line.category_id)?.label ?? SPLIT_KIND_LABELS[line.kind as SplitKind];
    const counterparty = this.counterparties().find(candidate => candidate.id === line.counterparty_id)?.name;
    return counterparty ? `${category} · ${counterparty}` : category;
  }

  /** The line's category icon source; an uncategorised line falls back to the default icon of its kind. */
  private iconSource(line: EntryInput): CategoryIconSource {
    return (
      this.allOptions().find(option => option.id === line.category_id) ?? { icon: null, color: null, mainName: null, kind: line.kind }
    );
  }

  icon(line: EntryInput): string {
    return iconOf(this.iconSource(line));
  }

  color(line: EntryInput): string {
    return colorOf(this.iconSource(line));
  }

  amountLabel(line: EntryInput): string {
    const account = this.accounts().find(candidate => candidate.id === line.account_id);
    return formatMoney(line.amount ?? 0, account?.currency ?? 'TWD');
  }

  accountName(line: EntryInput): string {
    return this.accounts().find(candidate => candidate.id === line.account_id)?.name ?? '';
  }
}
