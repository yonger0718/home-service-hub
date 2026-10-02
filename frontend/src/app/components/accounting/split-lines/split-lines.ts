import { ChangeDetectionStrategy, Component, computed, inject, input, model, signal } from '@angular/core';

import { CategoryNode, Counterparty, EntryInput, LedgerAccount } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { amountString, parseAmountText } from '../amount-text';
import { emptyEntryInput } from '../entry-form/entry-save';
import { formatAmount } from '../format';
import { sheetKey } from '../transfer-panel/transfer-panel';

export type SplitKind = 'expense' | 'income' | 'receivable' | 'payable';

export const SPLIT_KIND_LABELS: Record<SplitKind, string> = {
  expense: '支出',
  income: '收入',
  receivable: '應收',
  payable: '應付',
};

interface CategoryOption {
  id: number;
  label: string;
  icon: string | null;
  color: string | null;
}

function flatten(tree: CategoryNode[]): CategoryOption[] {
  const options: CategoryOption[] = [];
  for (const main of tree.filter(node => !node.is_hidden)) {
    options.push({ id: main.id, label: main.name, icon: main.icon, color: main.color });
    for (const sub of (main.children ?? []).filter(node => !node.is_hidden)) {
      options.push({ id: sub.id, label: sub.name, icon: sub.icon ?? main.icon, color: sub.color ?? main.color });
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
})
export class SplitLinesComponent {
  private service = inject(AccountingService);

  readonly members = model<EntryInput[]>([]);
  readonly accounts = input<LedgerAccount[]>([]);
  readonly categories = input<Partial<Record<SplitKind, CategoryNode[]>>>({});
  readonly defaultAccountId = input<number | null>(null);
  readonly disabled = input(false);

  readonly open = signal(false);
  readonly draftKind = signal<SplitKind>('expense');
  readonly draftCategoryId = signal<number | null>(null);
  readonly draftAmount = signal('');
  readonly draftAccountId = signal<number | null>(null);
  readonly draftCounterparty = signal('');
  readonly error = signal<string | null>(null);
  readonly loaded = signal<Partial<Record<SplitKind, CategoryNode[]>>>({});
  readonly counterparties = signal<Counterparty[]>([]);
  private counterpartiesRequested = false;

  readonly kinds = Object.keys(SPLIT_KIND_LABELS) as SplitKind[];
  readonly kindLabels = SPLIT_KIND_LABELS;
  readonly activeAccounts = computed(() => this.accounts().filter(account => !account.is_archived));
  readonly needsCounterparty = computed(() => this.draftKind() === 'receivable' || this.draftKind() === 'payable');
  readonly categoryOptions = computed(() => flatten(this.treeFor(this.draftKind())));
  private readonly allOptions = computed(() => this.kinds.flatMap(kind => flatten(this.treeFor(kind))));

  private treeFor(kind: SplitKind): CategoryNode[] {
    return this.categories()[kind] ?? this.loaded()[kind] ?? [];
  }

  openSheet(): void {
    if (this.disabled()) {
      return;
    }
    this.draftKind.set('expense');
    this.draftCategoryId.set(null);
    this.draftAmount.set('');
    this.draftCounterparty.set('');
    this.error.set(null);
    this.draftAccountId.set(this.defaultAccountId() ?? this.activeAccounts()[0]?.id ?? null);
    this.ensureCategories('expense');
    if (!this.counterpartiesRequested) {
      this.counterpartiesRequested = true;
      this.service.getCounterparties().subscribe(list => this.counterparties.set(list));
    }
    this.open.set(true);
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
    this.service.getCategories(kind).subscribe(tree => this.loaded.update(current => ({ ...current, [kind]: tree })));
  }

  add(): void {
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
      this.error.set(null);
      this.open.set(false);
    };
    if (!this.needsCounterparty()) {
      finish(null);
      return;
    }
    const name = this.draftCounterparty().trim();
    if (!name) {
      this.error.set('應收／應付需要對象');
      return;
    }
    const existing = this.counterparties().find(candidate => candidate.name === name);
    if (existing) {
      finish(existing.id);
      return;
    }
    this.service.createCounterparty({ name }).subscribe({
      next: created => {
        this.counterparties.update(list => [...list, created]);
        finish(created.id);
      },
      error: () => this.error.set('無法新增對象'),
    });
  }

  /** ⏎ in the sheet adds the line, Esc closes it; both are marked handled so the entry form ignores them. */
  onSheetKeydown(event: KeyboardEvent): void {
    const key = sheetKey(event);
    if (!key) {
      return;
    }
    event.preventDefault();
    if (key === 'Enter') {
      this.add();
    } else {
      this.open.set(false);
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

  icon(line: EntryInput): CategoryOption | undefined {
    return this.allOptions().find(option => option.id === line.category_id);
  }

  amountLabel(line: EntryInput): string {
    const account = this.accounts().find(candidate => candidate.id === line.account_id);
    return formatAmount(line.amount ?? 0, account?.currency ?? 'TWD');
  }

  accountName(line: EntryInput): string {
    return this.accounts().find(candidate => candidate.id === line.account_id)?.name ?? '';
  }
}
