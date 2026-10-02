import { Location } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  OnInit,
  computed,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import {
  EMPTY,
  Observable,
  Subject,
  catchError,
  combineLatest,
  defaultIfEmpty,
  distinctUntilChanged,
  filter,
  forkJoin,
  map,
  of,
  skip,
  switchMap,
  takeLast,
  tap,
} from 'rxjs';

import {
  AccountDetail,
  CategoryNode,
  ChildInput,
  Counterparty,
  DEFAULT_PREFERENCE,
  ENTRY_KIND_LABELS,
  EntryDetail,
  EntryInput,
  LedgerAccount,
  Preference,
  Project,
  WritableEntryKind,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AmountKeypadComponent } from '../amount-keypad/amount-keypad';
import { evaluateAmount, prettyExpression, roundHalfAway } from '../amount-math';
import { CategoryPickerComponent } from '../category-picker/category-picker';
import { FeeSheetComponent } from '../fee-sheet/fee-sheet';
import { currencyDecimals, formatMoney, formatNumber } from '../format';
import { FxSheetComponent, FxValue } from '../fx-sheet/fx-sheet';
import { writeErrorMessage } from '../http-errors';
import { LockBannerComponent } from '../lock-banner/lock-banner';
import { SplitLinesComponent } from '../split-lines/split-lines';
import { TransferPanelComponent } from '../transfer-panel/transfer-panel';
import {
  FORM_KINDS,
  FormKind,
  categoryKindFor,
  evalOrNull,
  findCategory,
  isFormKind,
  isWritableKind,
  nowTime,
  parseTags,
  quickAmounts,
  readLastUse,
  ruleLabel,
  rulesForDate,
  signFor,
  todayIso,
} from './entry-draft';
import {
  NO_RELATED,
  RelatedLoad,
  SharedFields,
  buildEntryInput,
  entryInputFromDetail,
  executeEntrySave,
  fxFromDetail,
  loadRelatedRows,
  planEntrySave,
  rememberEntryUse,
  resolveCounterpartyId,
} from './entry-save';
import { TransferEdit, transferCommonFrom, transferEditFrom } from './transfer-math';

export { NO_RELATED } from './entry-save';
export type { RelatedLoad } from './entry-save';

export const LONG_PRESS_MS = 600;

/** ⏎ that commits an IME candidate (Zhuyin, …): Safari sends it after compositionend with keyCode 229. */
function isImeEnter(event: KeyboardEvent): boolean {
  return event.isComposing || event.keyCode === 229;
}
const FX_FEE_NAME = '國外交易手續費';
const FLASH_MS = 1500;

interface FeeProposal {
  entryId: number;
  amount: string;
  input: EntryInput;
  continuous: boolean;
}

/** What `load()` fetches: an edit (`copy: false`) or a copy source. */
interface EntryTarget {
  entryId: number;
  copy: boolean;
}

/** One edit / copy / refetch load; `loadId` identifies the `load()` call that started it. */
interface EntryLoad extends EntryTarget {
  loadId: number;
}

interface LoadedEntry extends EntryLoad {
  detail: EntryDetail;
  related: RelatedLoad;
}

/**
 * `/accounting/entry` and `/accounting/entries/:id/edit` (spec "Entry page", mockups `s-entry`, `d-entry`).
 *
 * Load sequencing rule: every record load — route navigation, `?copy=`, a refetch after an action (`reload()`), a
 * reset for 連續記帳 — goes through `load()`, which bumps `loadId` and feeds the single `switchMap` sequence
 * `getEntry` → `loadRelated`. Nothing subscribes to `getEntry` or a related load on its own. `loading()` stays true
 * until both stages of the latest load have answered; an answer from either stage carrying an older `loadId` is
 * dropped.
 */
@Component({
  selector: 'app-entry-form',
  standalone: true,
  imports: [
    LockBannerComponent,
    CategoryPickerComponent,
    AmountKeypadComponent,
    FxSheetComponent,
    FeeSheetComponent,
    TransferPanelComponent,
    SplitLinesComponent,
  ],
  templateUrl: './entry-form.html',
  styleUrl: './entry-form.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onKeydown($event)' },
})
export class EntryFormComponent implements OnInit {
  private readonly accounting = inject(AccountingService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly location = inject(Location);
  private readonly destroyRef = inject(DestroyRef);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly layoutMode = inject(LayoutModeService);

  readonly kinds = FORM_KINDS;
  readonly ruleLabel = ruleLabel;

  // Reference data
  readonly ready = signal(false);
  readonly accounts = signal<LedgerAccount[]>([]);
  readonly projects = signal<Project[]>([]);
  readonly counterparties = signal<Counterparty[]>([]);
  readonly preference = signal<Preference>(DEFAULT_PREFERENCE);
  readonly categories = signal<CategoryNode[]>([]);
  readonly accountDetail = signal<AccountDetail | null>(null);

  // Form state
  readonly kind = signal<FormKind>('expense');
  /** Update target; set only when the matching `GET /entries/{id}` response is applied (null for new records and copies). */
  readonly entryId = signal<number | null>(null);
  /** The route has `:id` (edit mode), known from navigation before the record arrives. */
  readonly editing = signal(false);
  /** True from `load()` of an edit / copy / refetch until the record AND its related rows (`loadRelated`) are applied. */
  readonly loading = signal(false);
  /** Related rows of the applied record (split members, transfer counterpart). */
  readonly related = signal<RelatedLoad>(NO_RELATED);
  /** Account of the record being edited, kept selectable even when archived. */
  private readonly originalAccountId = signal<number | null>(null);
  readonly locked = signal(false);
  readonly unsupported = signal<string | null>(null);
  readonly category = signal<CategoryNode | null>(null);
  readonly accountId = signal<number | null>(null);
  readonly projectId = signal<number | null>(null);
  readonly amountExpr = signal('');
  readonly amountError = signal(false);
  readonly targetExpr = signal('');
  readonly name = signal('');
  readonly merchant = signal('');
  readonly counterpartyName = signal('');
  readonly entryDate = signal(todayIso());
  readonly entryTime = signal(nowTime());
  readonly postedDate = signal('');
  readonly invoiceNumber = signal('');
  readonly invoiceRandom = signal('');
  readonly description = signal('');
  readonly tags = signal<string[]>([]);
  readonly ruleIds = signal<number[]>([]);
  readonly fx = signal<FxValue | null>(null);
  readonly fee = signal<ChildInput | null>(null);
  readonly discount = signal<ChildInput | null>(null);
  readonly sheet = signal<'fx' | 'fee' | null>(null);
  readonly saving = signal(false);
  readonly error = signal<string | null>(null);
  readonly savedFlash = signal(false);
  readonly feeProposal = signal<FeeProposal | null>(null);
  /** Extra split lines; empty for a plain entry. */
  readonly members = signal<EntryInput[]>([]);
  /** The loaded entry's split group id, when editing a split member. */
  readonly groupId = signal<number | null>(null);
  readonly transferEdit = signal<TransferEdit | null>(null);
  readonly transferPanel = viewChild(TransferPanelComponent);

  private rulesTouched = false;
  /** Bumped by every `load()`; a response carrying an older value is dropped. */
  private loadId = 0;
  /** The one entry point of the load sequence (see the class comment). */
  private readonly loads = new Subject<EntryLoad | null>();
  private pendingCategoryId: number | null = null;
  private readonly categoryCache = new Map<string, CategoryNode[]>();
  private pressTimer: ReturnType<typeof setTimeout> | null = null;
  private flashTimer: ReturnType<typeof setTimeout> | null = null;
  private longPressed = false;

  // Derived
  /** Edit mode whose record failed to load: no update target, so ✓ stays disabled and save() refuses. */
  readonly loadFailed = computed(() => this.editing() && !this.loading() && this.entryId() === null);
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  readonly isSystem = computed(() => this.kind() === 'system');
  readonly isParty = computed(() => this.kind() === 'receivable' || this.kind() === 'payable');
  readonly categoryKind = computed(() => categoryKindFor(this.kind()));
  readonly title = computed(() => (this.editing() ? '編輯記錄' : '新增記錄'));
  readonly account = computed(() => this.accounts().find(account => account.id === this.accountId()) ?? null);
  /** Open accounts; in edit mode also the record's own account when it is archived. New records never offer archived ones. */
  readonly accountOptions = computed(() =>
    this.accounts().filter(
      account => !account.is_archived || (this.editing() && account.id === this.originalAccountId()),
    ),
  );
  readonly projectOptions = computed(() =>
    this.projects().filter(project => !project.is_archived || project.id === this.projectId()),
  );
  readonly accountCurrency = computed(() => this.account()?.currency ?? this.preference().main_currency);
  readonly accountDecimals = computed(() => currencyDecimals(this.accountCurrency()));
  readonly entryCurrency = computed(() => this.fx()?.original_currency ?? this.accountCurrency());
  readonly decimals = computed(() => currencyDecimals(this.entryCurrency()));
  readonly amount = computed(() => evalOrNull(this.amountExpr(), this.decimals()));
  readonly target = computed(() => evalOrNull(this.targetExpr(), this.accountDecimals()));
  readonly amountText = computed(() => prettyExpression(this.amountExpr()) || '0');
  readonly targetText = computed(() => prettyExpression(this.targetExpr()) || '0');
  readonly amountPlain = computed(() => {
    const amount = this.amount();
    return amount === null ? '' : String(amount);
  });
  readonly stripAmount = computed(() => {
    const amount = this.amount();
    return amount === null ? null : formatMoney(amount, this.entryCurrency());
  });
  /** Amount in the account currency (converted when FX is active), unsigned. */
  readonly convertedAmount = computed((): number | null => {
    const amount = this.amount();
    const fx = this.fx();
    if (amount === null) {
      return null;
    }
    if (!fx) {
      return amount;
    }
    if (!fx.use_online && fx.manual === 'amount' && fx.amount) {
      return Number(fx.amount);
    }
    return fx.fx_rate ? roundHalfAway(amount * Number(fx.fx_rate), this.accountDecimals()) : null;
  });
  readonly signedConverted = computed(() => {
    const kind = this.kind();
    const value = this.convertedAmount() ?? 0;
    return isWritableKind(kind) ? signFor(kind) * value : value;
  });
  readonly offeredRules = computed(() => rulesForDate(this.accountDetail()?.reward_rules ?? [], this.entryDate()));
  readonly quick = computed(() => {
    const category = this.category();
    return category ? quickAmounts(category.id) : [];
  });
  readonly currentBalance = computed(() => {
    const account = this.account();
    return account ? formatMoney(account.balance, account.currency) : '—';
  });
  readonly footer = computed(() => {
    if (this.kind() === 'transfer') {
      return '';
    }
    const parts: string[] = [];
    const fx = this.fx();
    const amount = this.amount();
    if (fx && amount !== null) {
      const converted = this.convertedAmount();
      const rate = fx.fx_rate ? String(Number(fx.fx_rate)) : '?';
      const result = converted === null ? '?' : formatMoney(converted, this.accountCurrency());
      parts.push(`${formatMoney(amount, fx.original_currency)} × ${rate} = ${result}`);
    }
    const fee = this.fee();
    const discount = this.discount();
    if (fee || discount) {
      const total = this.signedConverted() - Number(fee?.amount ?? 0) + Number(discount?.amount ?? 0);
      parts.push(`手續費 −${fee?.amount ?? 0} · 折扣 +${discount?.amount ?? 0} · 總額 ${formatNumber(total, this.accountCurrency())}`);
    }
    if (parts.length) {
      return parts.join(' · ');
    }
    if (this.isSystem()) {
      return '儲存時以當下餘額計算調整差額';
    }
    return this.category() ? '' : '先選類別，帳戶與專案會帶入上次使用的設定';
  });

  constructor() {
    toObservable(this.categoryKind)
      .pipe(
        distinctUntilChanged(),
        switchMap(kind => this.loadCategories(kind)),
        takeUntilDestroyed(),
      )
      .subscribe(list => {
        this.categories.set(list);
        this.resolvePendingCategory();
      });

    toObservable(this.accountId)
      .pipe(
        distinctUntilChanged(),
        switchMap(id => (id === null ? of(null) : this.accounting.getAccount(id).pipe(catchError(() => of(null))))),
        takeUntilDestroyed(),
      )
      .subscribe(detail => {
        this.accountDetail.set(detail);
        if (!this.rulesTouched) {
          this.ruleIds.set(this.basicRuleIds());
        }
      });

    // A preference saved elsewhere (keypad layout, main currency) reaches a mounted form too.
    toObservable(this.accounting.preferenceChanged)
      .pipe(
        skip(1),
        switchMap(() => this.accounting.getPreference().pipe(catchError(() => EMPTY))),
        takeUntilDestroyed(),
      )
      .subscribe(preference => this.preference.set(preference));

    this.loads
      .pipe(
        // Edit A → edit B (or a refetch): switchMap unsubscribes A's pending stage, whichever it is; fetchEntry() and
        // applyLoaded() also drop any answer for an older loadId.
        switchMap(request => (request === null ? of(null) : this.fetchEntry(request))),
        takeUntilDestroyed(),
      )
      .subscribe(loaded => {
        if (loaded !== null) {
          this.applyLoaded(loaded);
        }
      });

    combineLatest([this.route.paramMap, this.route.queryParamMap, toObservable(this.ready).pipe(filter(Boolean))])
      .pipe(takeUntilDestroyed())
      .subscribe(([params, query]) => this.load(this.start(params.get('id'), query.get('kind'), query.get('copy'))));

    this.destroyRef.onDestroy(() => {
      this.clearPress();
      if (this.flashTimer) {
        clearTimeout(this.flashTimer);
      }
    });
  }

  ngOnInit(): void {
    forkJoin({
      // Archived accounts too, so an edited record on an archived account still shows (and keeps) it.
      accounts: this.accounting.getAccounts(true),
      projects: this.accounting.getProjects(),
      counterparties: this.accounting.getCounterparties(),
      preference: this.accounting.getPreference(),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: data => {
          this.accounts.set(data.accounts);
          this.projects.set(data.projects);
          this.counterparties.set(data.counterparties);
          this.preference.set(data.preference);
          this.ready.set(true);
        },
        error: () => this.error.set('資料讀取失敗，請稍後再試。'),
      });
  }

  // ---- lifecycle of one record --------------------------------------------

  /**
   * The single entry point of the load sequence: a fresh `loadId`, `loading()` until both stages answer, and the
   * previous load's pending stage cancelled. `null` (blank record, 連續記帳) just invalidates whatever was in flight.
   */
  private load(target: EntryTarget | null): void {
    const loadId = ++this.loadId;
    this.loading.set(target !== null);
    this.loads.next(target === null ? null : { ...target, loadId });
  }

  /** Re-reads the edited record (and its related rows) through `load()`, e.g. after an action changed them. */
  reload(): void {
    const id = this.entryId();
    if (id !== null) {
      this.load({ entryId: id, copy: false });
    }
  }

  /** Resets the form for a navigation; returns the record to load (edit or copy), or null for a blank record. */
  private start(id: string | null, kindParam: string | null, copyParam: string | null): EntryTarget | null {
    this.resetFields();
    this.related.set(NO_RELATED);
    this.feeProposal.set(null);
    this.locked.set(false);
    this.unsupported.set(null);
    this.error.set(null);
    this.entryDate.set(todayIso());
    this.entryTime.set(nowTime());
    this.entryId.set(null);
    this.originalAccountId.set(null);
    this.accountId.set(null);
    this.editing.set(id !== null);
    if (id !== null) {
      return { entryId: Number(id), copy: false };
    }
    this.kind.set(isFormKind(kindParam) ? kindParam : 'expense');
    if (copyParam !== null) {
      return { entryId: Number(copyParam), copy: true };
    }
    this.accountId.set(this.accounts().find(account => !account.is_archived)?.id ?? null);
    return null;
  }

  private resetFields(): void {
    this.category.set(null);
    this.pendingCategoryId = null;
    this.projectId.set(null);
    this.amountExpr.set('');
    this.amountError.set(false);
    this.targetExpr.set('');
    this.name.set('');
    this.merchant.set('');
    this.counterpartyName.set('');
    this.postedDate.set('');
    this.invoiceNumber.set('');
    this.invoiceRandom.set('');
    this.description.set('');
    this.tags.set([]);
    this.ruleIds.set([]);
    this.rulesTouched = false;
    this.fx.set(null);
    this.fee.set(null);
    this.discount.set(null);
    this.sheet.set(null);
    this.members.set([]);
    this.groupId.set(null);
    this.transferEdit.set(null);
    this.transferPanel()?.reset();
  }

  /** Stage 1 `getEntry`, stage 2 `loadRelated`; each stage's answer is dropped when a newer `load()` exists. */
  private fetchEntry(request: EntryLoad): Observable<LoadedEntry | null> {
    const current = () => request.loadId === this.loadId;
    return this.accounting.getEntry(request.entryId).pipe(
      filter(current),
      switchMap(detail =>
        this.loadRelated(detail).pipe(
          // `loading()` turns false only once the related stage has completed (its last value is applied).
          defaultIfEmpty(NO_RELATED),
          takeLast(1),
          filter(current),
          map(related => ({ ...request, detail, related })),
        ),
      ),
      catchError(() => {
        if (request.loadId === this.loadId) {
          this.loading.set(false);
          this.error.set('記錄讀取失敗，請稍後再試。');
        }
        return of(null);
      }),
    );
  }

  /**
   * Dependent rows of `detail`, loaded inside the same sequence before `loading()` turns false: the other members of
   * a split and the transfer counterpart's detail. A newer navigation unsubscribes it (switchMap). Must complete; its
   * last value is applied on completion (an empty completion counts as `NO_RELATED`).
   */
  protected loadRelated(detail: EntryDetail): Observable<RelatedLoad> {
    return loadRelatedRows(this.accounting, detail);
  }

  /** Applies a loaded record only when it answers the latest `load()`; anything older is dropped. */
  private applyLoaded(loaded: LoadedEntry): void {
    if (loaded.loadId !== this.loadId || loaded.detail.id !== loaded.entryId) {
      return;
    }
    this.entryId.set(loaded.copy ? null : loaded.detail.id);
    this.related.set(loaded.related);
    this.applyDetail(loaded.detail, loaded.copy);
    // A transfer copy is prefilled from both legs but saves as a new transfer (saveTransfer sends no group id then).
    this.transferEdit.set(transferEditFrom(loaded.detail, loaded.related.transfer));
    if (!loaded.copy && loaded.detail.group?.kind === 'split') {
      // Editing a split member: the other members become split lines and the save replaces the group's members.
      this.groupId.set(loaded.detail.group.id);
      this.members.set(loaded.related.members.map(entryInputFromDetail));
    }
    this.loading.set(false);
  }

  private applyDetail(detail: EntryDetail, copy: boolean): void {
    this.locked.set(!copy && detail.locked);
    if (!copy) {
      this.originalAccountId.set(detail.account_id);
    }
    const transfer = detail.kind === 'transfer_out' || detail.kind === 'transfer_in';
    if (!transfer && !isWritableKind(detail.kind)) {
      this.unsupported.set(`${ENTRY_KIND_LABELS[detail.kind]}請在明細頁處理`);
      return;
    }
    this.name.set(detail.name ?? '');
    this.merchant.set(detail.merchant ?? '');
    this.description.set(detail.description ?? '');
    this.tags.set([...detail.tags]);
    this.entryDate.set(copy ? todayIso() : detail.entry_date);
    this.entryTime.set(copy ? nowTime() : (detail.entry_time ?? '').slice(0, 5));
    this.postedDate.set(copy || detail.posted_date === detail.entry_date ? '' : detail.posted_date);
    this.projectId.set(detail.project_id ?? this.projects().find(project => project.name === detail.project)?.id ?? null);
    if (transfer) {
      // Both legs reach the transfer panel through loadRelated() → applyLoaded() → transferEdit.
      this.kind.set('transfer');
      return;
    }
    this.rulesTouched = true;
    this.kind.set(detail.kind as WritableEntryKind);
    const archived = this.accounts().find(account => account.id === detail.account_id)?.is_archived ?? false;
    // A copy is a new record: it never lands on an archived account.
    this.accountId.set(
      copy && archived ? (this.accounts().find(account => !account.is_archived)?.id ?? null) : detail.account_id,
    );
    this.pendingCategoryId = detail.category_id;
    this.resolvePendingCategory();
    const input = entryInputFromDetail(detail);
    const fx = fxFromDetail(detail);
    this.fx.set(fx);
    this.amountExpr.set((fx ? fx.original_amount : input.amount) ?? '');
    this.counterpartyName.set(detail.counterparty ?? '');
    this.invoiceNumber.set(copy ? '' : (input.invoice_number ?? ''));
    this.invoiceRandom.set(copy ? '' : (input.invoice_random ?? ''));
    this.fee.set(input.fee);
    this.discount.set(input.discount);
    this.ruleIds.set(input.reward_rule_ids);
  }

  private loadCategories(kind: string | null): Observable<CategoryNode[]> {
    if (!kind) {
      return of([]);
    }
    const cached = this.categoryCache.get(kind);
    if (cached) {
      return of(cached);
    }
    return this.accounting.getCategories(kind).pipe(
      tap(list => this.categoryCache.set(kind, list)),
      catchError(() => of([])),
    );
  }

  private resolvePendingCategory(): void {
    const node = findCategory(this.categories(), this.pendingCategoryId);
    if (node) {
      this.category.set(node);
      this.pendingCategoryId = null;
    }
  }

  private basicRuleIds(): number[] {
    return this.offeredRules()
      .filter(rule => rule.is_basic)
      .map(rule => rule.id);
  }

  // ---- field handlers -----------------------------------------------------

  /** While editing, the record type cannot change into 系統 or between a transfer and a single entry. */
  tabDisabled(kind: FormKind): boolean {
    return this.editing() && (kind === 'system' || (kind === 'transfer') !== (this.kind() === 'transfer'));
  }

  accountLabel(account: LedgerAccount): string {
    return account.is_archived ? `${account.name}（已封存）` : account.name;
  }

  selectKind(kind: FormKind): void {
    if (kind === this.kind() || this.tabDisabled(kind)) {
      return;
    }
    this.kind.set(kind);
    this.category.set(null);
    this.error.set(null);
  }

  onCategoryPicked(node: CategoryNode): void {
    this.error.set(null);
    if (this.entryId() !== null) {
      return;
    }
    const usable = (id: number | null | undefined): id is number =>
      id !== null && id !== undefined && this.accounts().some(account => account.id === id && !account.is_archived);
    const last = readLastUse(node.id);
    if (usable(last?.account_id)) {
      this.accountId.set(last!.account_id);
    } else if (usable(node.default_account_id)) {
      this.accountId.set(node.default_account_id);
    }
    if (last) {
      this.projectId.set(last.project_id);
    } else if (node.default_project_id !== null) {
      this.projectId.set(node.default_project_id);
    }
  }

  setAccount(value: string): void {
    this.accountId.set(value ? Number(value) : null);
    if (this.entryId() === null) {
      this.rulesTouched = false;
    }
  }

  setProject(value: string): void {
    this.projectId.set(value ? Number(value) : null);
  }

  toggleRule(id: number): void {
    this.rulesTouched = true;
    this.ruleIds.update(ids => (ids.includes(id) ? ids.filter(other => other !== id) : [...ids, id]));
  }

  onTagEnter(event: Event): void {
    if (isImeEnter(event as KeyboardEvent)) {
      return;
    }
    event.preventDefault();
    const input = event.target as HTMLInputElement;
    this.addTags(input.value);
    input.value = '';
  }

  onTagBlur(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.addTags(input.value);
    input.value = '';
  }

  private addTags(text: string): void {
    const added = parseTags(text);
    if (added.length) {
      this.tags.update(tags => [...new Set([...tags, ...added])]);
    }
  }

  removeTag(tag: string): void {
    this.tags.update(tags => tags.filter(other => other !== tag));
  }

  onAmountInput(text: string): void {
    this.amountExpr.set(text);
    this.amountError.set(false);
  }

  /** Desktop field: evaluate on blur / ⏎; returns false (and marks the tile) when the expression is invalid. */
  commitAmountInput(text: string): boolean {
    return this.commitExpression(text, this.amountExpr, this.decimals(), this.entryCurrency());
  }

  onTargetInput(text: string): void {
    this.targetExpr.set(text);
    this.amountError.set(false);
  }

  commitTargetInput(text: string): boolean {
    return this.commitExpression(text, this.targetExpr, this.accountDecimals(), this.accountCurrency());
  }

  private commitExpression(
    text: string,
    target: { set(value: string): void },
    decimals: number,
    currency: string,
  ): boolean {
    if (!text.trim()) {
      target.set('');
      this.amountError.set(false);
      return true;
    }
    const result = evaluateAmount(text, decimals);
    if (result.ok) {
      target.set(formatNumber(result.value, currency));
      this.amountError.set(false);
      return true;
    }
    target.set(text);
    this.amountError.set(true);
    return false;
  }

  openSheet(sheet: 'fx' | 'fee'): void {
    if (this.locked()) {
      return;
    }
    if (sheet === 'fx' && !this.account()) {
      this.error.set('請先選擇帳戶');
      return;
    }
    this.sheet.set(sheet);
  }

  onFxClosed(): void {
    this.sheet.set(null);
    const fx = this.fx();
    if (fx?.original_amount) {
      this.amountExpr.set(fx.original_amount);
    }
  }

  focusName(): void {
    this.host.nativeElement.querySelector<HTMLInputElement>('.name-input')?.focus();
  }

  proposalText(proposal: FeeProposal): string {
    return formatMoney(-Math.abs(Number(proposal.amount)), this.accountCurrency());
  }

  // ---- save ---------------------------------------------------------------

  onSavePointerDown(): void {
    this.longPressed = false;
    this.clearPress();
    this.pressTimer = setTimeout(() => {
      this.pressTimer = null;
      this.longPressed = true;
      this.save(true);
    }, LONG_PRESS_MS);
  }

  onSavePointerUp(): void {
    this.clearPress();
  }

  onSaveClick(): void {
    if (this.longPressed) {
      this.longPressed = false;
      return;
    }
    this.save(false);
  }

  private clearPress(): void {
    if (this.pressTimer) {
      clearTimeout(this.pressTimer);
      this.pressTimer = null;
    }
  }

  onKeydown(event: KeyboardEvent): void {
    // Handled already (a nested control, the tag input), or an IME candidate being committed: not ours.
    if (event.defaultPrevented || isImeEnter(event)) {
      return;
    }
    if (event.key === 'Escape') {
      // Marked handled so the accounting layout's document-level Esc does not also close its sheet.
      event.preventDefault();
      if (this.sheet()) {
        this.sheet.set(null);
      } else {
        this.cancel();
      }
      return;
    }
    if (event.key !== 'Enter' || this.sheet()) {
      return;
    }
    const target = event.target as HTMLElement | null;
    if (
      target?.tagName === 'TEXTAREA' ||
      target?.tagName === 'BUTTON' ||
      target?.tagName === 'SUMMARY' ||
      target?.classList.contains('chip-input')
    ) {
      return;
    }
    event.preventDefault();
    if (target instanceof HTMLInputElement && target.classList.contains('amount-input')) {
      const committed = target.classList.contains('target-input')
        ? this.commitTargetInput(target.value)
        : this.commitAmountInput(target.value);
      if (!committed) {
        this.error.set('金額算式有誤');
        return;
      }
    }
    this.save(event.shiftKey);
  }

  private validate(): string | null {
    if (this.locked()) {
      return 'MOZE 匯入資料，切換後可編輯';
    }
    if (this.unsupported()) {
      return this.unsupported();
    }
    if (this.kind() === 'transfer') {
      // The transfer panel validates its own accounts and amounts (buildInput). An edited leg without its
      // counterpart has no group to PUT, and must never fall through to creating a second transfer.
      return this.entryId() !== null && !this.transferEdit() ? '找不到轉帳的另一筆，請在明細頁處理' : null;
    }
    if (!this.account()) {
      return '請選擇帳戶';
    }
    if (this.amountError()) {
      return '金額算式有誤';
    }
    if (this.isSystem()) {
      return this.target() === null ? '請輸入調整後餘額' : null;
    }
    const amount = this.amount();
    if (amount === null || amount <= 0) {
      return '請輸入金額';
    }
    const fx = this.fx();
    if (fx && !fx.use_online && this.convertedAmount() === null) {
      return '請輸入匯率或轉換後金額';
    }
    if (this.isParty() && !this.counterpartyName().trim()) {
      return '請輸入對象';
    }
    return null;
  }

  save(continuous: boolean): void {
    // No save until the record of the current navigation has arrived (a stale form must never be written).
    if (this.loading() || this.saving() || this.feeProposal()) {
      return;
    }
    // Edit whose record never arrived (failed load): never fall through to a create.
    if (this.loadFailed()) {
      this.error.set('記錄未載入，無法儲存');
      return;
    }
    const problem = this.validate();
    if (problem) {
      this.error.set(problem);
      return;
    }
    this.error.set(null);
    this.saving.set(true);
    // Captured now: the id set when the matching response was applied, not whatever the route says later.
    const targetId = this.entryId();
    const keepGoing = continuous && targetId === null;
    if (this.kind() === 'transfer') {
      const groupId = targetId === null ? null : (this.transferEdit()?.groupId ?? null);
      // null: the panel shows its own validation message.
      const request = this.transferPanel()?.submit(transferCommonFrom(this.sharedFields()), groupId) ?? null;
      this.write(request, () => this.finish(keepGoing));
      return;
    }
    if (this.isSystem()) {
      const request = this.accounting.createBalanceAdjustment({
        account_id: this.account()!.id,
        target_balance: String(this.target()),
        entry_date: this.entryDate(),
        entry_time: this.entryTime() || null,
        description: this.description().trim() || null,
      });
      this.write(request, () => this.finish(keepGoing));
      return;
    }
    const context = { entryId: targetId, groupId: this.groupId() };
    const counterparty: Observable<number | null> = this.isParty()
      ? resolveCounterpartyId(this.accounting, this.counterpartyName(), this.counterparties(), party =>
          this.counterparties.update(list => [...list, party]),
        )
      : of(null);
    const request = counterparty.pipe(
      map(counterpartyId => planEntrySave(this.buildInput(counterpartyId), this.members(), context)),
      switchMap(plan =>
        executeEntrySave(this.accounting, plan).pipe(
          // Only single-entry writes answer with an `EntryDetail` (and its `proposed_fee`).
          map(result => ({ plan, detail: plan.kind === 'create' || plan.kind === 'update' ? (result as EntryDetail) : null })),
        ),
      ),
    );
    this.write(request, ({ plan, detail }) => {
      const input = plan.kind === 'create' || plan.kind === 'update' ? plan.input : (plan.input.members[0] as EntryInput);
      rememberEntryUse(input, this.amount());
      if (detail?.proposed_fee && Number(detail.proposed_fee) > 0 && !input.fee) {
        this.feeProposal.set({ entryId: detail.id, amount: detail.proposed_fee, input, continuous: keepGoing });
        return;
      }
      this.finish(keepGoing);
    });
  }

  /** Runs one write (null: refused before sending): `saving` until it answers, then `done` or the error line. */
  private write<T>(request: Observable<T> | null, done: (result: T) => void): void {
    if (!request) {
      this.saving.set(false);
      return;
    }
    this.saving.set(true);
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => {
        this.saving.set(false);
        done(result);
      },
      error: (error: unknown) => {
        this.saving.set(false);
        this.error.set(writeErrorMessage(error));
      },
    });
  }

  /** The 名稱 / 商家 / 日期 / 時間 / 專案 / 標籤 / 備註 tiles, which a transfer shares with a single entry. */
  private sharedFields(): SharedFields {
    return {
      entryDate: this.entryDate(),
      entryTime: this.entryTime(),
      postedDate: this.postedDate(),
      name: this.name(),
      merchant: this.merchant(),
      description: this.description(),
      projectId: this.projectId(),
      tags: this.tags(),
    };
  }

  private buildInput(counterpartyId: number | null): EntryInput {
    const offered = new Set(this.offeredRules().map(rule => rule.id));
    return buildEntryInput({
      ...this.sharedFields(),
      kind: this.kind() as WritableEntryKind,
      account: this.account()!,
      amount: this.amount()!,
      fx: this.fx(),
      categoryId: this.category()?.id ?? null,
      invoiceNumber: this.invoiceNumber(),
      invoiceRandom: this.invoiceRandom(),
      fee: this.fee(),
      discount: this.discount(),
      ruleIds: this.accountDetail() ? this.ruleIds().filter(id => offered.has(id)) : this.ruleIds(),
      counterpartyId,
    });
  }

  addProposedFee(): void {
    const proposal = this.feeProposal();
    if (!proposal) {
      return;
    }
    const request = this.accounting.updateEntry(proposal.entryId, {
      ...proposal.input,
      fee: { amount: proposal.amount, name: FX_FEE_NAME },
    });
    this.write(request, () => {
      this.feeProposal.set(null);
      this.finish(proposal.continuous);
    });
  }

  skipProposedFee(): void {
    const proposal = this.feeProposal();
    this.feeProposal.set(null);
    this.finish(proposal?.continuous ?? false);
  }

  private finish(keepGoing: boolean): void {
    if (keepGoing) {
      this.continueEntry();
    } else {
      this.leave();
    }
  }

  /** 連續記帳: a fresh record with the same kind, account and date. */
  private continueEntry(): void {
    // A reset is part of the load sequence too: anything still in flight is cancelled and its answer dropped.
    this.load(null);
    const kind = this.kind();
    const accountId = this.accountId();
    const date = this.entryDate();
    this.resetFields();
    this.kind.set(kind);
    this.accountId.set(accountId);
    this.entryDate.set(date);
    this.entryTime.set(nowTime());
    this.ruleIds.set(this.basicRuleIds());
    this.savedFlash.set(true);
    if (this.flashTimer) {
      clearTimeout(this.flashTimer);
    }
    this.flashTimer = setTimeout(() => this.savedFlash.set(false), FLASH_MS);
  }

  cancel(): void {
    this.leave();
  }

  /** Back to the page the owner came from; to the timeline when the form was opened directly. */
  private leave(): void {
    // The router navigated before this page in this session: there is an in-app page to go back to.
    if (this.router.lastSuccessfulNavigation()?.previousNavigation) {
      this.location.back();
    } else {
      void this.router.navigateByUrl('/accounting');
    }
  }
}
