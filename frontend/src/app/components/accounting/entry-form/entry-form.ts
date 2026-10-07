import { AccountPickerComponent } from '../account-picker/account-picker';
import { rememberRecentAccounts } from '../account-picker/recent-accounts';
import { Location, NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  OnInit,
  OnDestroy,
  computed,
  effect,
  inject,
  signal,
  untracked,
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
  filter,
  finalize,
  forkJoin,
  map,
  of,
  shareReplay,
  skip,
  switchMap,
  Subscription,
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
  RewardRule,
  ScheduleDefinition,
  SplitResult,
  defaultCategoryIcon,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { DirtyAware, DirtyFormRegistry } from '../dirty-form.service';
import { AccountingToastService } from '../accounting-toast';
import { NO_ENTER_SAVE_TAGS, accountLabel, isHandledKey, restoreOverlayFocus, trapFocus } from '../accounting-ui';
import { AmountKeypadComponent } from '../amount-keypad/amount-keypad';
import { evaluateAmount, prettyExpression, roundHalfAway } from '../amount-math';
import { Bubble, CategoryPickerComponent, categoryColor, categoryIcon } from '../category-picker/category-picker';
import { FeeSheetComponent } from '../fee-sheet/fee-sheet';
import { currencyDecimals, formatMoney, formatNumber } from '../format';
import { FxSheetComponent, FxValue } from '../fx-sheet/fx-sheet';
import { fieldErrors, writeErrorMessage } from '../http-errors';
import { AccountingShortcutsService } from '../keyboard-shortcuts';
import { LockBannerComponent } from '../lock-banner/lock-banner';
import { IMPORT_RUNNING_TOAST, isImportRunning } from '../schedule-math';
import { SCHEDULE_TABS, ScheduleDraft, ScheduleTab, defaultDraft, ruleDayFor, tabsFor } from '../schedule-tabs/schedule-draft';
import { ScheduleTabsComponent } from '../schedule-tabs/schedule-tabs';
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
  loadRelatedRows,
  rememberEntryUse,
  resolveCounterpartyId,
} from './entry-save';
import {
  FORM_CANNOT_REPRESENT,
  ScheduleFormError,
  buildDefinitionInput,
  definitionUpdateFrom,
  draftFromDefinition,
  formCanRepresent,
  shouldCatchUp,
} from './schedule-save';
import {
  ChildDraft,
  ChildView,
  Owner,
  PARENT_KEY,
  ParentDraft,
  SplitDraftStore,
  childView,
  draftSnapshot,
  newChild,
  newParent,
  normalizePosted,
} from './split-draft';
import { addAvailability, childFromDetail, splitFromDetails } from './split-load';
import { memberError, resultIds } from './split-result';
import { executeEntrySave, fullChild, planEntrySave } from './split-save';
import { CurrencyNet, accountAmount, dissolveNotices, netByCurrency, rewardEstimates } from './split-summary';
import { TransferEdit, transferCommonFrom, transferEditFrom } from './transfer-math';

export { NO_RELATED } from './entry-save';
export type { RelatedLoad } from './entry-save';

export const LONG_PRESS_MS = 600;

const FX_FEE_NAME = '國外交易手續費';
const FLASH_MS = 1500;

/**
 * The account / kind a child's account detail and category tree were loaded for. FX inputs and the date are not
 * part of it: the handlers that change them (sheet, 重新換算, account move, parent date) invalidate explicitly.
 */
function resourceDeps(child: ChildDraft): string {
  return `${child.accountId}|${child.kind}`;
}

/** `group_members[].protected_reason` in the owner's words. */
const PROTECTED_REASONS: Record<string, string> = {
  settlement: '收款／還款',
  refund: '退款',
  transfer: '轉帳',
  system: '系統記錄',
  settled_original: '已有收還款或退款',
  scheduled_loan: '排程還款中的借貸',
};

interface FeeProposal {
  entryId: number;
  amount: string;
  input: EntryInput;
  continuous: boolean;
}

/** R-F2 copy: the schedule exists; only its first catch-up is outstanding. */
export const CATCH_UP_RETRY_TEXT = '排程已建立，入帳未完成；按 ✓ 重試入帳';
export const CATCH_UP_FAILED_TOAST = '排程已建立；這一期入帳失敗，請到待完成交易處理';

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

/** The schedule part of the unsaved-input key: dates that still follow the entry date, and the rule day, are derived. */
export function scheduleDraftKey(draft: ScheduleDraft): unknown {
  if (draft.tab === 'single') {
    return 'single';
  }
  const { start, firstDate, dayOfMonth: _day, dayHydrated: _hydrated, ...rest } = draft;
  return { ...rest, start: draft.startTouched ? start : null, firstDate: draft.firstTouched ? firstDate : null };
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
  imports: [AccountPickerComponent,
    NgTemplateOutlet,
    LockBannerComponent,
    CategoryPickerComponent,
    AmountKeypadComponent,
    FxSheetComponent,
    FeeSheetComponent,
    TransferPanelComponent,
    ScheduleTabsComponent,
  ],
  templateUrl: './entry-form.html',
  styleUrl: './entry-form.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onKeydown($event)' },
})
export class EntryFormComponent implements OnInit, OnDestroy, DirtyAware {
  private readonly accounting = inject(AccountingService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly location = inject(Location);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly registry = inject(DirtyFormRegistry);
  private readonly injector = inject(Injector);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly layoutMode = inject(LayoutModeService);

  readonly kinds = FORM_KINDS;
  readonly ruleLabel = ruleLabel;
  readonly formatMoney = formatMoney;

  // Reference data
  readonly ready = signal(false);
  readonly accounts = signal<LedgerAccount[]>([]);
  readonly projects = signal<Project[]>([]);
  readonly counterparties = signal<Counterparty[]>([]);
  readonly preference = signal<Preference>(DEFAULT_PREFERENCE);
  readonly categories = signal<CategoryNode[]>([]);
  readonly accountDetail = signal<AccountDetail | null>(null);

  // Form state
  /**
   * The children (one for a plain entry, 2–50 for a split), the parent fields and the selection (spec §2.1). The
   * tiles below are views of the selected child; asynchronous results are applied only through captured owners.
   */
  readonly drafts = new SplitDraftStore(newParent(todayIso(), nowTime()));
  readonly children = this.drafts.children;
  readonly selected = this.drafts.selected;
  readonly parent = this.drafts.parent;
  readonly isSplit = this.drafts.isSplit;
  /** The loaded split group id (upsert / dissolve target), when editing a split member. */
  readonly groupId = this.drafts.groupId;
  /** Split / group semantics apply: two or more children, or a loaded group (even reduced to one, until dissolved). */
  readonly childScope = computed(() => this.isSplit() || this.groupId() !== null);
  /** The parent bubble is selected: the group summary and parent fields replace the child tiles. */
  readonly parentMode = computed(() => this.selected() === PARENT_KEY);
  readonly current = computed(() => {
    const key = this.selected();
    return this.children().find(child => child.key === key) ?? null;
  });
  /** The selected child is server-protected: only its metadata is editable. */
  readonly protectedChild = computed(() => this.current()?.protected ?? false);
  /** Any protected child pins the shared dates (spec §2.5). */
  readonly dateLocked = computed(() => this.children().some(child => child.protected));
  /** A single entry the server would refuse to convert (settled, refunded, loan, schedule-posted): no ＋. */
  readonly convertBlockedReason = signal<string | null>(null);
  /** The tab of a single entry, including the transfer / system workflows that never become a child kind. */
  readonly singleKind = signal<FormKind>('expense');
  /** Update target; set only when the matching `GET /entries/{id}` response is applied (null for new records and copies). */
  readonly entryId = signal<number | null>(null);
  /** The route has `:id` (edit mode), known from navigation before the record arrives. */
  readonly editing = signal(false);
  /** True from `load()` of an edit / copy / refetch until the record AND its related rows (`loadRelated`) are applied. */
  readonly loading = signal(false);
  /** Related rows of the applied record (split members, transfer counterpart). */
  readonly related = signal<RelatedLoad>(NO_RELATED);
  /** Account of a transfer / definition being edited, kept selectable even when archived (children keep their own). */
  private readonly originalAccountId = signal<number | null>(null);
  readonly locked = signal(false);
  readonly unsupported = signal<string | null>(null);
  readonly amountExpr = childView(this.drafts, 'amountExpr', '', true);
  readonly accountId = childView(this.drafts, 'accountId', null, true);
  readonly name = childView(this.drafts, 'name', '');
  readonly projectId = childView(this.drafts, 'projectId', null);
  readonly description = childView(this.drafts, 'description', '');
  readonly tags = childView(this.drafts, 'tags', []);
  readonly counterpartyName = childView(this.drafts, 'counterpartyName', '', true);
  readonly fee = childView(this.drafts, 'fee', null, true);
  readonly discount = childView(this.drafts, 'discount', null, true);
  readonly fx = childView(this.drafts, 'fx', null, true);
  readonly ruleIds = childView(this.drafts, 'ruleIds', [], true);
  readonly rulesTouched = childView(this.drafts, 'rulesTouched', false, true);
  readonly invoiceNumber = computed(() => this.current()?.invoice.number ?? '');
  readonly invoiceRandom = computed(() => this.current()?.invoice.random ?? '');
  readonly merchant = this.parentView('merchant');
  readonly entryDate = this.parentView('entryDate');
  readonly entryTime = this.parentView('entryTime');
  readonly postedDate = this.parentView('postedDate');
  /** Selected child's kind in child scope (non-editable protected kinds lay out as `system`), else the single tab. */
  readonly kind: ChildView<FormKind> = Object.assign(
    () => {
      const child = this.current();
      return this.childScope() ? (child && isWritableKind(child.kind) ? child.kind : 'system') : this.singleKind();
    },
    {
      set: (value: FormKind) => {
        const child = this.drafts.current();
        const owner = this.drafts.capture();
        if (this.childScope()) {
          if (child && owner && !child.protected && isWritableKind(value)) {
            this.drafts.accept(owner, { kind: value });
          }
        } else {
          this.singleKind.set(value);
          if (owner && isWritableKind(value)) {
            this.drafts.accept(owner, { kind: value });
          }
        }
      },
      update: (fn: (value: FormKind) => FormKind) => this.kind.set(fn(this.kind())),
    },
  );
  readonly amountError = signal(false);
  readonly targetExpr = signal('');
  readonly sheet = signal<'fx' | 'fee' | null>(null);
  readonly saving = signal(false);
  readonly error = signal<string | null>(null);
  /** One-line notice of something the form changed by itself (FX reset after an account-currency change). */
  readonly notice = signal<string | null>(null);
  readonly savedFlash = signal(false);
  readonly feeProposal = signal<FeeProposal | null>(null);
  /** A split write answered without a usable key mapping: the server may have committed, so no retry is offered. */
  readonly saveResponseInvalid = signal(false);
  readonly deleteGroupPrompt = signal(false);
  readonly transferEdit = signal<TransferEdit | null>(null);
  readonly transferPanel = viewChild(TransferPanelComponent);
  readonly categoryPicker = viewChild(CategoryPickerComponent);
  private readonly toast = inject(AccountingToastService);
  /** 進階 單次 / 週期 / 分期 (Task 21); 單次 is the plain entry. */
  readonly scheduleDraft = signal<ScheduleDraft>(defaultDraft(todayIso()));
  /** 編輯整個排程: the definition being edited (`?schedule=<id>`), else null. */
  readonly scheduleId = signal<number | null>(null);
  /** An imported definition before cutover: shown read-only with the lock banner. */
  readonly definitionLocked = signal(false);
  /** A definition whose lines the form cannot rebuild losslessly (`formCanRepresent`): shown read-only. */
  readonly definitionReadOnly = signal(false);
  /** Server 422 field errors of the last save, shown beside their fields. */
  readonly fieldErrors = signal<Record<string, string>>({});
  /** 週期 / 分期 chosen; never while editing an entry (that edits the one record: 單次 only, final review F1). */
  readonly scheduling = computed(() => this.scheduleDraft().tab !== 'single' && !this.editing());
  /**
   * Serialised draft as the save would read it (`draftSnapshot`): identity, selection, offered rules, automatic rule
   * defaults and online quotes are left out; the single tab (a transfer / system choice is an edit) is kept.
   */
  private readonly formState = computed(() =>
    JSON.stringify([
      this.singleKind(),
      draftSnapshot(
        this.parent(),
        this.children(),
        scheduleDraftKey(this.scheduleDraft()),
        !this.childScope() && this.kind() === 'transfer' ? (this.transferPanel()?.draftKey() ?? null) : null,
        this.targetExpr(),
      ),
    ]),
  );
  /** Snapshot of `formState` once the record (or blank form) has rendered; null while loading. */
  private readonly baseline = signal<string | null>(null);
  /** The copied child whose first resource load still belongs to loading the copy (see `loadChildResources`). */
  private copyBaselineKey: string | null = null;
  /** The draft differs from what was loaded (spec §4.3). */
  readonly isDirty = computed(() => {
    const baseline = this.baseline();
    return baseline !== null && baseline !== this.formState();
  });

  readonly eventTabs = SCHEDULE_TABS;
  /** Enabled 事件類型 tabs: a definition keeps its own kind; otherwise `tabsFor` (editing → 單次 only). */
  readonly enabledEventTabs = computed<ScheduleTab[]>(() =>
    this.scheduleId() !== null ? [this.scheduleDraft().tab] : tabsFor(this.kind(), { editing: this.editing() }),
  );

  /** The payable a loan definition repays (definition mode). */
  private loanEntryId: number | null = null;
  /** Definition mode: the definition as loaded; a save keeps the fields the form does not expose (R1). */
  private loadedDefinition: ScheduleDefinition | null = null;
  private definitionRequest = 0;
  /** R-F2: a definition this form created whose catch-up has not completed; ✓ then retries the catch-up only. */
  readonly createdScheduleId = signal<number | null>(null);
  /** Definition mode: the definition of the current navigation has been applied (else ✓ has no target). */
  private readonly definitionLoaded = signal(false);

  /** Bumped by every `load()`; a response carrying an older value is dropped. */
  private loadId = 0;
  /** The one entry point of the load sequence (see the class comment). */
  private readonly loads = new Subject<EntryLoad | null>();
  /** Category trees by kind (presentation only: neither selection nor equal kinds identify an owner). */
  private readonly categoryLists = signal<ReadonlyMap<string, CategoryNode[]>>(new Map());
  /** Labels of loaded members' categories, for bubbles of kinds whose tree is not loaded (protected rows). */
  private readonly loadedCategories = signal<ReadonlyMap<number, { name: string; icon: string | null; color: string | null }>>(
    new Map(),
  );
  /** Each child's account detail, applied to the tiles when that child is selected again. */
  private readonly childAccountCache = new Map<string, AccountDetail | null>();
  /** Account settings read during this navigation, by account id. */
  private readonly accountDetails = new Map<number, AccountDetail>();
  /** The generation (and its dependency signature) whose resources were requested, per child key. */
  private readonly requestedGeneration = new Map<string, { generation: number; deps: string }>();
  /** The in-flight resource load per child key; a newer generation or removal cancels it (like a per-key switchMap). */
  private readonly childLoads = new Map<string, Subscription>();
  private readonly pendingDraftLoads = signal<Owner[]>([]);
  private readonly draftLoadErrors = signal<Record<string, string>>({});
  /** The child whose FX / fee sheet is open; the sheet's results apply to it whatever is selected meanwhile. */
  private sheetOwner: Owner | null = null;
  private deleteGroupOpener: HTMLElement | null = null;
  private pressTimer: ReturnType<typeof setTimeout> | null = null;
  private flashTimer: ReturnType<typeof setTimeout> | null = null;
  private longPressed = false;

  // Derived
  /**
   * Edit mode whose record failed to load, or definition mode whose definition failed to load: no update target, so
   * ✓ stays disabled and save() refuses.
   */
  readonly loadFailed = computed(
    () =>
      !this.loading() &&
      ((this.editing() && this.entryId() === null) || (this.scheduleId() !== null && !this.definitionLoaded())),
  );
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  readonly inSheet = computed(() => this.layoutMode.mode() === 'sheet');
  /** 餘額調整 (single only): a protected non-editable child lays out as `system` but never edits a balance. */
  readonly isSystem = computed(() => !this.childScope() && this.kind() === 'system');
  readonly isParty = computed(() => this.kind() === 'receivable' || this.kind() === 'payable');
  readonly categoryKind = computed(() => {
    const child = this.current();
    return this.childScope() ? (child && isWritableKind(child.kind) ? child.kind : null) : categoryKindFor(this.kind());
  });
  /** The selected child's category, resolved in its kind's tree. */
  readonly category = computed(() => findCategory(this.categories(), this.current()?.categoryId ?? null));
  /** Date the selected child's rules and quotes use: its own untouched loaded date, else the parent's. */
  readonly childDate = computed(() => {
    const child = this.current();
    const parent = this.parent();
    return child?.loaded && !parent.dateTouched && this.childScope() ? child.loaded.entryDate : parent.entryDate;
  });
  /** The protected child's real kind and reason, shown above its (disabled) financial fields. */
  readonly protectedText = computed(() => {
    const child = this.current();
    if (!child?.protected) {
      return null;
    }
    const reason = child.protectedReason ? (PROTECTED_REASONS[child.protectedReason] ?? child.protectedReason) : null;
    return reason ? `${ENTRY_KIND_LABELS[child.kind]}・${reason}` : ENTRY_KIND_LABELS[child.kind];
  });
  /** The protected child's kind is not an editable tab: one disabled tab with its real label instead. */
  readonly protectedKindLabel = computed(() => {
    const child = this.current();
    return child?.protected && !isWritableKind(child.kind) ? ENTRY_KIND_LABELS[child.kind] : null;
  });
  readonly draftLoadsPending = computed(() =>
    this.pendingDraftLoads().some(owner =>
      this.children().some(child => child.key === owner.key && child.generation === owner.generation),
    ),
  );
  readonly draftLoadsFailed = computed(() =>
    this.children().some(child => this.draftLoadErrors()[`${child.key}:${child.generation}`] !== undefined),
  );
  /** A child's account / category defaults are still loading or failed: saving now could omit them. */
  readonly resourcesBlocking = computed(() => this.childScope() && (this.draftLoadsPending() || this.draftLoadsFailed()));
  readonly addState = computed(() =>
    addAvailability({
      split: this.isSplit() || this.groupId() !== null,
      count: this.children().length,
      scheduleId: this.scheduleId(),
      eventTab: this.scheduleDraft().tab,
      editing: this.editing(),
      kind: this.kind(),
      convertBlockedReason: this.convertBlockedReason(),
      source: this.current()?.loaded?.source ?? 'manual',
      locked: this.locked(),
    }),
  );
  readonly removeReason = computed(() => {
    const child = this.current();
    return child ? this.drafts.removeReason(child.key) : null;
  });
  /** Parent fields the save will not keep: dropped on an unsaved split → single, or not copied by a dissolve. */
  readonly removalNotices = computed(() =>
    this.isSplit()
      ? []
      : this.groupId() !== null
        ? dissolveNotices(this.parent(), this.children()[0])
        : this.drafts.droppedNotices(),
  );
  readonly summary = computed(() => netByCurrency(this.children(), this.accounts()));
  readonly rewards = computed(() => rewardEstimates(this.children(), this.accounts()));
  readonly accountCount = computed(
    () => new Set(this.children().map(child => child.accountId).filter(id => id !== null)).size,
  );
  /** Untouched loaded members carry different dates / times / posting dates. */
  readonly mixedDates = computed(() => {
    if (this.parent().dateTouched) {
      return false;
    }
    const keys = new Set(
      this.children()
        .map(child => child.loaded)
        .filter(loaded => loaded !== null)
        .map(loaded => [loaded.entryDate, loaded.entryTime, normalizePosted(loaded.entryDate, loaded.postedDate)].join('|')),
    );
    return keys.size > 1;
  });
  readonly parentText = computed(() => {
    const lines = this.summary().map(line => this.netText(line));
    return `多類別 ${lines.join(' · ')} (${this.children().length})`;
  });
  readonly bubbles = computed<Bubble[]>(() => this.children().map(child => this.bubbleOf(child)));
  /** A plain entry shows its bubble (with ＋) only once it has a category, as the old selected strip did. */
  readonly stripBubbles = computed(() =>
    this.childScope() || this.current()?.categoryId != null ? this.bubbles() : [],
  );
  /** A protected child's stored, server-signed amount (read only). */
  readonly protectedAmount = computed(() => {
    const loaded = this.current()?.loaded;
    return loaded ? formatMoney(loaded.signedAmount, loaded.currency, { sign: true }) : '—';
  });
  /** The child's loaded merchant (a split member keeps its own; the group's is the parent's). */
  readonly childMerchant = computed(() => {
    const merchant = this.current()?.loaded?.merchant;
    return this.childScope() && merchant?.trim() ? merchant : null;
  });
  readonly title = computed(() => (this.scheduleId() !== null ? '編輯排程' : this.editing() ? '編輯記錄' : '新增記錄'));
  readonly account = computed(() => this.accounts().find(account => account.id === this.accountId()) ?? null);
  /**
   * Open accounts; in edit and definition mode also the record's / definition's own account when it is archived.
   * New records never offer archived ones.
   */
  readonly accountOptions = computed(() => {
    // A loaded child keeps only its own original account selectable; other modes the transfer / definition's.
    const original = this.current()?.loaded?.originalAccountId ?? this.originalAccountId();
    return this.accounts().filter(
      account => !account.is_archived || ((this.editing() || this.scheduleId() !== null) && account.id === original),
    );
  });
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
  /** Rules of the selected child's account valid on its date, plus the rules its stored row is still linked to. */
  readonly offeredRules = computed((): RewardRule[] => {
    const child = this.current();
    if (!child) {
      return [];
    }
    const own = rulesForDate(child.availableRules.filter(rule => rule.account_id === child.accountId), this.childDate());
    const attached =
      child.loaded?.originalAccountId === child.accountId
        ? child.availableRules.filter(
            rule => child.loaded!.attachedRuleIds.includes(rule.id) && !own.some(other => other.id === rule.id),
          )
        : [];
    return [...own, ...attached];
  });
  readonly quick = computed(() => {
    const category = this.category();
    return category ? quickAmounts(category.id) : [];
  });
  readonly currentBalance = computed(() => {
    const account = this.account();
    return account ? formatMoney(account.balance, account.currency) : '—';
  });
  /**
   * The owner fixed the converted amount (轉換成) and the original amount has changed since: the saved rate is the
   * effective one (converted / original), shown with 固定 and a one-tap 重新換算.
   */
  readonly fxFixed = computed(() => {
    const fx = this.fx();
    const amount = this.amount();
    return (
      !!fx && !fx.use_online && fx.manual === 'amount' && !!fx.amount && amount !== null && Number(fx.original_amount) !== amount
    );
  });
  readonly footer = computed(() => {
    // The parent shows its own summary; a protected child's amount is the server's.
    if (this.kind() === 'transfer' || this.parentMode() || this.protectedChild()) {
      return '';
    }
    const parts: string[] = [];
    const fx = this.fx();
    const amount = this.amount();
    const currency = this.accountCurrency();
    if (fx && amount !== null) {
      const converted = this.convertedAmount();
      // A fixed converted amount saves the effective rate, so that is the rate shown.
      const rate =
        !fx.use_online && fx.manual === 'amount' && converted !== null && amount > 0
          ? (converted / amount).toFixed(4)
          : fx.fx_rate
            ? String(Number(fx.fx_rate))
            : '?';
      const result = converted === null ? '?' : formatMoney(converted, currency);
      parts.push(`${formatMoney(amount, fx.original_currency)} × ${rate} = ${result}`);
    }
    const fee = this.fee();
    const discount = this.discount();
    if (fee || discount) {
      const total = this.signedConverted() - Number(fee?.amount ?? 0) + Number(discount?.amount ?? 0);
      if (fee) {
        parts.push(`手續費 ${this.feeText(fee)}`);
      }
      if (discount) {
        parts.push(`折扣 ${this.discountText(discount)}`);
      }
      parts.push(`總額 ${formatMoney(total, currency)}`);
    }
    if (parts.length) {
      return parts.join(' · ');
    }
    if (this.isSystem()) {
      return '儲存時以當下餘額計算調整差額';
    }
    // Last-use account / project defaults apply to a new plain entry only (a split child keeps its inherited account).
    return this.category() || this.childScope() ? '' : '先選類別，帳戶與專案會帶入上次使用的設定';
  });

  private readonly shortcutCommands = inject(AccountingShortcutsService)
    .entryCommands.pipe(takeUntilDestroyed())
    .subscribe(command => (command === 'cancel' ? this.cancel() : this.save(command === 'save-continue')));

  constructor() {
    // Per-child resource reconciler: every child generation not yet requested (selected or not) loads its account,
    // category tree and missing online quote once; removed keys drop their bookkeeping.
    effect(() => {
      const rows = this.children();
      untracked(() => {
        for (const child of rows) {
          const deps = resourceDeps(child);
          const requested = this.requestedGeneration.get(child.key);
          if (requested?.generation === child.generation) {
            if (requested.deps !== deps) {
              // A dependency changed through a plain field write: older answers are stale; the next run reloads.
              this.drafts.invalidate(child.key);
            }
            continue;
          }
          this.requestedGeneration.set(child.key, { generation: child.generation, deps });
          this.loadChildResources(child);
        }
        for (const key of [...this.requestedGeneration.keys()]) {
          if (!rows.some(child => child.key === key)) {
            this.requestedGeneration.delete(key);
            this.childAccountCache.delete(key);
            this.childLoads.get(key)?.unsubscribe();
            this.childLoads.delete(key);
          }
        }
      });
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
      .subscribe(([params, query]) => {
        const schedule = query.get('schedule');
        const target = this.start(params.get('id'), query.get('kind'), query.get('copy'), schedule);
        this.load(target);
        if (schedule !== null) {
          this.loadDefinition(Number(schedule));
        } else if (target === null) {
          this.markClean();
        }
      });

    this.destroyRef.onDestroy(() => {
      this.clearPress();
      if (this.flashTimer) {
        clearTimeout(this.flashTimer);
      }
    });
  }

  ngOnInit(): void {
    this.registry.register(this);
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

  ngOnDestroy(): void {
    this.registry.unregister(this);
  }

  private discardPromptActive = false;
  private discardOpener: HTMLElement | null = null;

  /** DirtyAware: the strip is rendered from `registry.promptOpen()`; 留下 takes the focus. */
  showDiscardPrompt(): void {
    if (!this.discardPromptActive) {
      this.discardOpener = this.host.nativeElement.ownerDocument.activeElement as HTMLElement | null;
      this.discardPromptActive = true;
    }
    afterNextRender(() => this.host.nativeElement.querySelector<HTMLElement>('.discard-stay')?.focus(), {
      injector: this.injector,
    });
  }

  restoreDiscardFocus(): void {
    const opener = this.discardOpener;
    this.clearDiscardFocus();
    restoreOverlayFocus(opener, this.host.nativeElement);
  }

  private clearDiscardFocus(): void {
    this.discardPromptActive = false;
    this.discardOpener = null;
  }

  /** Resets the form for a navigation; returns the record to load (edit or copy), or null for a blank record. */
  private start(id: string | null, kindParam: string | null, copyParam: string | null, scheduleParam: string | null = null): EntryTarget | null {
    this.baseline.set(null);
    this.clearDiscardFocus();
    this.registry.clearPending();
    this.accountDetails.clear();
    this.resetFields(id === null && isFormKind(kindParam) ? kindParam : 'expense');
    this.related.set(NO_RELATED);
    this.feeProposal.set(null);
    this.locked.set(false);
    this.unsupported.set(null);
    this.error.set(null);
    this.entryId.set(null);
    this.originalAccountId.set(null);
    this.editing.set(id !== null);
    ++this.definitionRequest;
    this.loanEntryId = null;
    this.loadedDefinition = null;
    this.definitionLocked.set(false);
    this.definitionReadOnly.set(false);
    this.definitionLoaded.set(false);
    this.createdScheduleId.set(null);
    this.scheduleId.set(scheduleParam !== null ? Number(scheduleParam) : null);
    if (id !== null) {
      return { entryId: Number(id), copy: false };
    }
    if (copyParam !== null) {
      return { entryId: Number(copyParam), copy: true };
    }
    this.accountId.set(this.accounts().find(account => !account.is_archived)?.id ?? null);
    return null;
  }

  /** Takes the clean snapshot after the next render, once child effects (schedule dates, transfer legs) settled. */
  private markClean(): void {
    this.clearDiscardFocus();
    this.registry.clearPending();
    this.baseline.set(null);
    this.transferPanel()?.markClean();
    afterNextRender(() => this.baseline.set(this.formState()), { injector: this.injector });
  }


  /** One fresh child (new key) and a new parent: every outstanding child callback and sheet owner is dropped. */
  private resetFields(kind: FormKind = 'expense'): void {
    this.notice.set(null);
    this.amountError.set(false);
    this.targetExpr.set('');
    this.sheet.set(null);
    this.sheetOwner = null;
    this.singleKind.set(kind);
    const child = newChild(isWritableKind(kind) ? kind : 'expense');
    this.children.set([child]);
    this.selected.set(child.key);
    this.parent.set(newParent(todayIso(), nowTime()));
    this.groupId.set(null);
    this.drafts.anchorId.set(null);
    this.drafts.droppedNotices.set([]);
    this.convertBlockedReason.set(null);
    this.saveResponseInvalid.set(false);
    this.deleteGroupPrompt.set(false);
    this.deleteGroupOpener = null;
    this.draftLoadErrors.set({});
    this.accountDetail.set(null);
    this.categories.set(this.categoryLists().get(child.kind) ?? []);
    this.transferEdit.set(null);
    this.transferPanel()?.reset();
    this.scheduleDraft.set(defaultDraft(todayIso()));
    this.fieldErrors.set({});
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
    const detail = loaded.detail;
    this.entryId.set(loaded.copy ? null : detail.id);
    this.related.set(loaded.related);
    this.rememberCategories([detail, ...loaded.related.members]);
    this.transferEdit.set(null);
    if (!loaded.copy && detail.group?.kind === 'split') {
      // A split member: every member becomes a child in group order, the opened one selected.
      let state: ReturnType<typeof splitFromDetails>;
      try {
        state = splitFromDetails(detail, loaded.related.members, this.preference().main_currency);
      } catch (error) {
        this.loading.set(false);
        this.error.set(error instanceof Error ? error.message : '記錄讀取失敗，請稍後再試。');
        return;
      }
      this.children.set(state.children);
      this.parent.set(state.parent);
      this.selected.set(state.selected);
      this.groupId.set(detail.group.id);
      this.drafts.anchorId.set(null);
      this.convertBlockedReason.set(null);
      this.singleKind.set(state.children.find(child => isWritableKind(child.kind))?.kind as FormKind ?? 'expense');
      this.showChildCaches();
      this.locked.set(detail.locked);
      this.unsupported.set(null);
      this.loading.set(false);
      this.markClean();
      return;
    }
    this.groupId.set(null);
    this.drafts.anchorId.set(null);
    if (isWritableKind(detail.kind)) {
      this.applySingle(detail, loaded.copy);
    } else {
      this.applyDetail(detail, loaded.copy);
      // A transfer copy is prefilled from both legs but saves as a new transfer (saveTransfer sends no group id then).
      this.transferEdit.set(transferEditFrom(detail, loaded.related.transfer));
    }
    this.loading.set(false);
    this.markClean();
  }

  /**
   * An editable single entry (or a copy) as one child. It stays editable whatever links it has; links the server would
   * refuse to convert only hide ＋ (`convertBlockedReason`), and the server stays authoritative at convert.
   */
  private applySingle(detail: EntryDetail, copy: boolean): void {
    const convertBlocked =
      detail.group !== null ||
      detail.is_settlement ||
      detail.transfer_group_id !== null ||
      detail.settled_by.length > 0 ||
      detail.refunded_by.length > 0 ||
      detail.loan_schedule != null ||
      detail.source === 'schedule' ||
      (detail.schedule?.posted_entry_ids.includes(detail.id) ?? false);
    this.convertBlockedReason.set(!copy && convertBlocked ? '此記錄不能拆帳' : null);
    let child = childFromDetail(detail, { protected: false, protected_reason: null }, this.preference().main_currency);
    if (copy) {
      // A copy is a new record: no id, no provenance, no invoice; it never converts its source.
      child = { ...child, id: null, loaded: null, invoice: { number: '', random: '' } };
    }
    this.children.set([child]);
    this.selected.set(child.key);
    this.copyBaselineKey = copy ? child.key : null;
    this.drafts.anchorId.set(copy ? null : detail.id);
    this.parent.set({
      name: '',
      merchant: detail.merchant ?? '',
      description: '',
      entryDate: copy ? todayIso() : detail.entry_date,
      entryTime: copy ? nowTime() : detail.entry_time,
      postedDate: copy ? null : normalizePosted(detail.entry_date, detail.posted_date),
      dateTouched: false,
    });
    this.singleKind.set(child.kind as FormKind);
    this.showChildCaches();
    this.locked.set(!copy && detail.locked);
    this.unsupported.set(null);
    if (copy && this.accounts().find(account => account.id === child.accountId)?.is_archived) {
      // A copy never lands on an archived account (the FX state follows the new currency).
      this.moveToAccount(this.accounts().find(account => !account.is_archived)?.id ?? null);
    }
  }

  /** A transfer leg (both legs reach the panel through `transferEdit`), or a kind the form cannot edit. */
  private applyDetail(detail: EntryDetail, copy: boolean): void {
    this.locked.set(!copy && detail.locked);
    if (!copy) {
      this.originalAccountId.set(detail.account_id);
    }
    const transfer = detail.kind === 'transfer_out' || detail.kind === 'transfer_in';
    if (!transfer) {
      this.unsupported.set(`${ENTRY_KIND_LABELS[detail.kind]}請在明細頁處理`);
      return;
    }
    this.name.set(detail.name ?? '');
    this.description.set(detail.description ?? '');
    this.tags.set([...detail.tags]);
    this.projectId.set(detail.project_id ?? this.projects().find(project => project.name === detail.project)?.id ?? null);
    this.parent.set({
      name: '',
      merchant: detail.merchant ?? '',
      description: '',
      entryDate: copy ? todayIso() : detail.entry_date,
      entryTime: copy ? nowTime() : (detail.entry_time ?? '').slice(0, 5),
      postedDate: copy || detail.posted_date === detail.entry_date ? null : detail.posted_date,
      dateTouched: false,
    });
    this.kind.set('transfer');
  }

  /** Category labels of loaded rows, for bubbles whose kind's tree is not loaded (e.g. a protected refund). */
  private rememberCategories(details: readonly EntryDetail[]): void {
    const known = new Map(this.loadedCategories());
    for (const detail of details) {
      if (detail.category_id !== null && detail.category) {
        known.set(detail.category_id, {
          name: detail.category.split('/').pop() ?? detail.category,
          icon: detail.category_icon,
          color: detail.category_color,
        });
      }
    }
    this.loadedCategories.set(known);
  }

  private loadCategories(kind: string | null): Observable<CategoryNode[]> {
    if (!kind) {
      return of([]);
    }
    const cached = this.categoryLists().get(kind);
    if (cached) {
      return of(cached);
    }
    return this.accounting.getCategories(kind).pipe(
      tap(list => this.cacheCategories(kind, list)),
      catchError(() => of([])),
    );
  }

  private cacheCategories(kind: string, list: CategoryNode[]): void {
    if (this.categoryLists().get(kind) !== list) {
      this.categoryLists.update(lists => new Map(lists).set(kind, list));
    }
  }

  /**
   * One generation of a child's asynchronous defaults: account detail (rules), category tree and a missing online
   * quote. The result applies only to the captured owner, selected or not; display caches follow the selection.
   */
  private loadChildResources(child: ChildDraft): void {
    const owner: Owner = { key: child.key, generation: child.generation };
    const parent = this.parent();
    const date = parent.dateTouched || !child.loaded || !this.childScope() ? parent.entryDate : child.loaded.entryDate;
    const fx = child.fx;
    const token = `${owner.key}:${owner.generation}`;
    this.pendingDraftLoads.update(rows => [...rows, owner]);
    this.draftLoadErrors.update(errors => {
      const next = { ...errors };
      delete next[token];
      return next;
    });
    this.childLoads.get(owner.key)?.unsubscribe();
    // An account's settings are read once per navigation (as the old per-account stream did); a kind / date / FX
    // change only re-derives the defaults. A failed read is not cached, so 重新載入子項設定 asks again.
    const accountId = child.accountId;
    const cached = accountId === null ? undefined : this.accountDetails.get(accountId);
    // A single transfer / 系統 tab shows no category grid for its (hidden) child.
    const wantsCategories = isWritableKind(child.kind) && (this.childScope() || isWritableKind(this.singleKind()));
    const load = forkJoin({
      account:
        accountId === null
          ? of(null)
          : cached
            ? of(cached)
            : this.accounting.getAccount(accountId).pipe(tap(detail => this.accountDetails.set(accountId, detail))),
      categories: wantsCategories ? this.loadCategories(child.kind) : of<CategoryNode[]>([]),
      // The quote is display only (the server converts online FX): a failed quote shows `?`, never blocks saving.
      quote:
        fx?.use_online && fx.fx_rate === null
          ? this.accounting.getFxRate(date, fx.original_currency, fx.account_currency).pipe(catchError(() => of(null)))
          : of(null),
    })
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() =>
          this.pendingDraftLoads.update(rows =>
            rows.filter(other => other.key !== owner.key || other.generation !== owner.generation),
          ),
        ),
      )
      .subscribe({
        next: result => {
          const current = this.children().find(row => row.key === owner.key && row.generation === owner.generation);
          if (!current) {
            return;
          }
          const accountRules = result.account?.reward_rules ?? [];
          const unchangedAccount = current.loaded?.originalAccountId === current.accountId;
          const attached = unchangedAccount
            ? current.availableRules.filter(rule => current.loaded!.attachedRuleIds.includes(rule.id))
            : [];
          const availableRules = [...new Map([...attached, ...accountRules].map(rule => [rule.id, rule])).values()];
          const eligible = rulesForDate(accountRules, date);
          const chosen = current.rulesTouched ? current.ruleIds : eligible.filter(rule => rule.is_basic).map(rule => rule.id);
          const allowed = new Set(eligible.map(rule => rule.id));
          if (unchangedAccount) {
            for (const id of current.loaded!.attachedRuleIds) {
              allowed.add(id);
            }
          }
          const patch: Partial<ChildDraft> = { availableRules, ruleIds: chosen.filter(id => allowed.has(id)) };
          if (result.quote && current.fx?.use_online) {
            patch.fx = { ...current.fx, fx_rate: String(Number(result.quote.rate)), rate_date: result.quote.date };
          }
          // A copy's late rule filtering is part of loading it, not an edit: an untouched copy stays clean.
          const keepClean = this.copyBaselineKey === owner.key && !this.isDirty();
          if (!this.drafts.accept(owner, patch)) {
            return;
          }
          if (this.copyBaselineKey === owner.key) {
            this.copyBaselineKey = null;
          }
          if (keepClean && this.baseline() !== null) {
            this.baseline.set(this.formState());
          }
          this.childAccountCache.set(owner.key, result.account);
          if (this.selected() === owner.key) {
            // `loadCategories` caches only a successful tree; a failed fetch (`[]`) never replaces a loaded one.
            this.categories.set(wantsCategories ? (this.categoryLists().get(child.kind) ?? []) : []);
            this.accountDetail.set(result.account);
          }
        },
        error: (error: unknown) => {
          if (this.children().some(row => row.key === owner.key && row.generation === owner.generation)) {
            this.draftLoadErrors.update(errors => ({ ...errors, [token]: writeErrorMessage(error) }));
          }
        },
      });
    if (!load.closed) {
      this.childLoads.set(owner.key, load);
    }
  }

  /** 重新載入子項設定: new generations for the failed children (their edits are kept). */
  retryChildResources(): void {
    const errors = this.draftLoadErrors();
    for (const child of this.children()) {
      if (errors[`${child.key}:${child.generation}`] !== undefined) {
        this.drafts.invalidate(child.key);
      }
    }
    this.error.set(null);
  }

  /** The tiles show the selected child's cached account / category data (cleared when it has none yet). */
  private showChildCaches(): void {
    const child = this.drafts.current();
    this.accountDetail.set(child ? (this.childAccountCache.get(child.key) ?? null) : null);
    this.categories.set(child && isWritableKind(child.kind) ? (this.categoryLists().get(child.kind) ?? []) : []);
  }

  private parentView<K extends keyof ParentDraft>(field: K): ChildView<ParentDraft[K]> {
    const view = (() => this.parent()[field]) as ChildView<ParentDraft[K]>;
    view.set = value => this.parent.update(parent => ({ ...parent, [field]: value }));
    view.update = fn => view.set(fn(view()));
    return view;
  }

  private netText(line: CurrencyNet): string {
    return `${line.currency} ${line.amount === null ? '—' : formatMoney(line.amount, line.currency, { sign: true })}`;
  }

  private bubbleOf(child: ChildDraft): Bubble {
    const account = this.accounts().find(candidate => candidate.id === child.accountId);
    const amount = accountAmount(child, account);
    const currency = account?.currency ?? child.loaded?.currency ?? this.preference().main_currency;
    const tree = this.categoryLists().get(child.kind) ?? [];
    const node = findCategory(tree, child.categoryId);
    const main = node ? (tree.find(other => other.children.some(sub => sub.id === node.id)) ?? null) : null;
    const loaded = child.categoryId === null ? undefined : this.loadedCategories().get(child.categoryId);
    const empty = child.categoryId === null;
    return {
      key: child.key,
      label: node?.name || loaded?.name || (empty && isWritableKind(child.kind) ? '未選類別' : ENTRY_KIND_LABELS[child.kind]),
      icon: node ? categoryIcon(node, main, child.kind) : (loaded?.icon ?? (empty ? '○' : defaultCategoryIcon(null, child.kind))),
      color: node ? categoryColor(node, main) : (loaded?.color ?? 'var(--app-surface-soft)'),
      amount: amount === null ? '—' : formatMoney(amount, currency, { sign: true }),
      empty: empty && isWritableKind(child.kind),
      protected: child.protected,
    };
  }

  // ---- field handlers -----------------------------------------------------

  /** While editing, the record type cannot change into 系統 or between a transfer and a single entry; in definition
   * mode it cannot change at all. */
  tabDisabled(kind: FormKind): boolean {
    if (this.childScope()) {
      // A child keeps an editable kind; a protected child's kind never changes.
      return this.parentMode() || this.protectedChild() || kind === 'transfer' || kind === 'system';
    }
    if (this.scheduleId() !== null) {
      return kind !== this.kind();
    }
    return this.editing() && (kind === 'system' || (kind === 'transfer') !== (this.kind() === 'transfer'));
  }

  readonly accountLabel = accountLabel;

  eventTabEnabled(tab: ScheduleTab): boolean {
    return this.enabledEventTabs().includes(tab);
  }

  /** As the old 進階 tabs did (rule day re-derived); schedule-only errors go with the old tab. */
  selectEventTab(tab: ScheduleTab): void {
    if (!this.eventTabEnabled(tab) || tab === this.scheduleDraft().tab) {
      return;
    }
    this.scheduleDraft.update(draft => {
      const next = { ...draft, tab };
      return { ...next, dayOfMonth: ruleDayFor(next) };
    });
    this.fieldErrors.set({});
    this.error.set(null);
  }

  /** ← / → move focus between enabled tabs; Space / ⏎ press the focused tab (native button). */
  onEventTabKeydown(event: KeyboardEvent): void {
    if ((event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') || isHandledKey(event)) {
      return;
    }
    event.preventDefault();
    const enabled = SCHEDULE_TABS.map(option => option.tab).filter(tab => this.eventTabEnabled(tab));
    if (enabled.length === 0) {
      return;
    }
    const current = ((event.target as HTMLElement).dataset['tab'] as ScheduleTab | undefined) ?? this.scheduleDraft().tab;
    const index = Math.max(0, enabled.indexOf(current));
    const next = enabled[(index + (event.key === 'ArrowRight' ? 1 : enabled.length - 1)) % enabled.length];
    this.host.nativeElement.querySelector<HTMLElement>(`.event-tab[data-tab="${next}"]`)?.focus();
  }

  selectKind(kind: FormKind): void {
    if (kind === this.kind() || this.tabDisabled(kind)) {
      return;
    }
    const owner = this.drafts.capture();
    if (owner && !this.flushChild(owner)) {
      return;
    }
    if (owner) {
      // The kind is a dependency of the child's category tree and defaults.
      this.drafts.invalidate(owner.key);
    }
    this.kind.set(kind);
    this.setCategory(null);
    this.error.set(null);
    if (!this.eventTabEnabled(this.scheduleDraft().tab)) {
      // The system branch has no schedule component to normalize its tab; use the same transition here.
      this.selectEventTab('single');
    }
    this.categories.set(isWritableKind(kind) ? (this.categoryLists().get(kind) ?? []) : []);
    if (this.childScope()) {
      afterNextRender(() => this.categoryPicker()?.reopen(), { injector: this.injector });
    }
  }

  /** The picker's selection: the selected child's category (never a protected child's). */
  setCategory(node: CategoryNode | null): void {
    const owner = this.drafts.capture();
    if (owner && !this.drafts.current()?.protected) {
      this.drafts.accept(owner, { categoryId: node?.id ?? null, pendingCategoryId: null });
    }
  }

  onCategoryPicked(node: CategoryNode): void {
    this.setCategory(node);
    this.error.set(null);
    // Last-use / category defaults belong to a new single record only; a split child keeps its inherited account.
    if (!this.isSplit() && this.groupId() === null && this.entryId() === null) {
      const usable = (id: number | null | undefined): id is number =>
        id !== null && id !== undefined && this.accounts().some(account => account.id === id && !account.is_archived);
      const last = readLastUse(node.id);
      if (usable(last?.account_id)) {
        this.moveToAccount(last!.account_id);
      } else if (usable(node.default_account_id)) {
        this.moveToAccount(node.default_account_id);
      }
      if (last) {
        this.projectId.set(last.project_id);
      } else if (node.default_project_id !== null) {
        this.projectId.set(node.default_project_id);
      }
    }
    afterNextRender(() => {
      const host = this.host.nativeElement;
      (host.querySelector<HTMLElement>('.amount-input') ?? host.querySelector<HTMLElement>('.amount-value'))?.focus();
    }, { injector: this.injector });
  }

  chooseAccount(id: number | null): void {
    this.moveToAccount(id);
  }

  /**
   * Every account change of the selected child goes through here. The FX conversion, fee and discount were typed
   * against the previous account's currency, so a currency change re-validates them: a conversion back into the
   * original currency is dropped (the amount stays the original amount); any other change resets it to the online
   * rate; fee and discount are cleared. A one-line notice says what changed. Rule selections of the old account are
   * cleared; a loaded child stays `rulesTouched` (no automatic basic rules over its stored choice). Other children
   * are never touched.
   */
  private moveToAccount(id: number | null): void {
    // Re-picking the same account is a no-op (it no longer resets a new record's rule choice).
    if (id === this.accountId()) {
      return;
    }
    const selected = this.drafts.current();
    if (!selected || selected.protected) {
      return;
    }
    const before = this.accountCurrency();
    // The account is a dependency of the child's rules and quote: older answers are now stale.
    this.drafts.invalidate(selected.key);
    this.drafts.accept(this.drafts.capture()!, { availableRules: [], ruleIds: [], rulesTouched: selected.loaded !== null });
    this.accountDetail.set(null);
    this.accountId.set(id);
    const after = this.accountCurrency();
    const fx = this.fx();
    const changes: string[] = [];
    if (fx && fx.account_currency !== after) {
      if (fx.original_currency === after) {
        this.fx.set(null);
        this.amountExpr.set(fx.original_amount);
      } else {
        this.fx.set({ ...fx, account_currency: after, use_online: true, manual: null, amount: null, fx_rate: null, rate_date: null });
        changes.push('已重設匯率');
      }
    }
    if ((before !== after || (fx && fx.account_currency !== after)) && (this.fee() || this.discount())) {
      this.fee.set(null);
      this.discount.set(null);
      changes.push('已清除手續費與折扣');
    }
    if (changes.length) {
      this.notice.set(`${changes.join('、')}（帳戶幣別變更）`);
    }
  }

  setProject(value: string): void {
    this.projectId.set(value ? Number(value) : null);
  }

  toggleRule(id: number): void {
    if (this.protectedChild()) {
      return;
    }
    this.rulesTouched.set(true);
    this.ruleIds.update(ids => (ids.includes(id) ? ids.filter(other => other !== id) : [...ids, id]));
  }

  onTagEnter(event: Event): void {
    if (isHandledKey(event as KeyboardEvent)) {
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

  setInvoice(field: 'number' | 'random', value: string): void {
    const child = this.drafts.current();
    const owner = this.drafts.capture();
    if (child && owner && !child.protected) {
      this.drafts.accept(owner, { invoice: { ...child.invoice, [field]: value } });
    }
  }

  setParentText(field: 'name' | 'merchant' | 'description', value: string): void {
    this.parent.update(parent => ({ ...parent, [field]: value }));
  }

  /**
   * Every user date / time / posting-date edit. Refused while a protected child pins the dates. Marks the parent dates
   * as the owner's (every full member then carries them) and restarts every child's date-dependent rules and quote.
   */
  setParentDate(field: 'entryDate' | 'entryTime' | 'postedDate', value: string): void {
    if (this.dateLocked()) {
      return;
    }
    this.parent.update(parent => ({ ...parent, [field]: field === 'entryDate' ? value : value || null, dateTouched: true }));
    for (const child of this.children()) {
      if (child.fx?.use_online) {
        this.drafts.accept({ key: child.key, generation: child.generation }, { fx: { ...child.fx, fx_rate: null, rate_date: null } });
      }
      this.drafts.invalidate(child.key);
    }
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

  private sheetOpener: HTMLElement | null = null;

  openSheet(sheet: 'fx' | 'fee', event?: Event): void {
    if (this.locked() || this.saving() || this.protectedChild()) {
      return;
    }
    if (sheet === 'fx' && !this.account()) {
      this.error.set('請先選擇帳戶');
      return;
    }
    this.sheetOpener = (event?.currentTarget ?? this.host.nativeElement.ownerDocument.activeElement) as HTMLElement | null;
    this.sheetOwner = this.drafts.capture();
    this.sheet.set(sheet);
  }

  /**
   * A sheet result, applied to the child the sheet was opened for. A changed currency pair or online / manual choice
   * is a dependency change: the child's generation moves on (the sheet keeps writing through its new owner).
   */
  applySheet(patch: Pick<Partial<ChildDraft>, 'fx' | 'fee' | 'discount'>): void {
    const owner = this.sheetOwner;
    if (!owner) {
      return;
    }
    const before = this.children().find(child => child.key === owner.key && child.generation === owner.generation);
    if (!before || !this.drafts.accept(owner, patch)) {
      return;
    }
    if ('fx' in patch) {
      const next = patch.fx ?? null;
      const previous = before.fx;
      if (
        next?.original_currency !== previous?.original_currency ||
        next?.account_currency !== previous?.account_currency ||
        next?.use_online !== previous?.use_online
      ) {
        this.drafts.invalidate(owner.key);
        this.sheetOwner = { key: owner.key, generation: owner.generation + 1 };
      }
    }
  }

  closeSheet(): void {
    this.sheet.set(null);
    this.sheetOwner = null;
    restoreOverlayFocus(this.sheetOpener, this.host.nativeElement);
    this.sheetOpener = null;
  }

  onFxClosed(): void {
    const owner = this.sheetOwner;
    const child = owner ? this.children().find(row => row.key === owner.key) : undefined;
    if (owner && child?.fx?.original_amount) {
      this.drafts.accept(owner, { amountExpr: child.fx.original_amount });
    }
    this.closeSheet();
  }

  /** 重新換算: drop the fixed converted amount; the server converts with the online rate (shown once fetched). */
  recomputeFx(): void {
    const fx = this.fx();
    const amount = this.amount();
    if (!fx || amount === null || this.locked() || this.protectedChild()) {
      return;
    }
    const owner = this.drafts.capture();
    if (!owner) {
      return;
    }
    const online: FxValue = { ...fx, original_amount: String(amount), use_online: true, manual: null, amount: null, fx_rate: null, rate_date: null };
    this.drafts.accept(owner, { fx: online });
    // The reconciler fetches the quote for this child's new generation, even if the selection changes now.
    this.drafts.invalidate(owner.key);
  }

  focusName(): void {
    this.host.nativeElement.querySelector<HTMLInputElement>('.name-input')?.focus();
  }

  /** Fee chip / footer amount in the account currency: `−$30`. */
  feeText(child: ChildInput): string {
    return formatMoney(-Math.abs(Number(child.amount)), this.accountCurrency());
  }

  /** Discount chip / footer amount in the account currency: `+$1,000`. */
  discountText(child: ChildInput): string {
    return formatMoney(Math.abs(Number(child.amount)), this.accountCurrency(), { sign: true });
  }

  proposalText(proposal: FeeProposal): string {
    return formatMoney(-Math.abs(Number(proposal.amount)), this.accountCurrency());
  }

  // ---- children -----------------------------------------------------------

  /**
   * Commits the owner's pending amount expression before its ownership ends (switch, add, kind change, save). A loaded
   * online-FX original is not re-rounded merely by switching. False (with the amount marked) when it does not parse.
   */
  private flushChild(owner: Owner): boolean {
    const child = this.children().find(row => row.key === owner.key);
    if (!child || child.protected || !child.amountExpr.trim()) {
      return true;
    }
    if (
      child.fx?.use_online &&
      child.loaded?.onlineFx?.amountExpr === child.amountExpr &&
      child.loaded.onlineFx.originalCurrency === child.fx.original_currency
    ) {
      return true;
    }
    const account = this.accounts().find(candidate => candidate.id === child.accountId);
    const value = evalOrNull(
      child.amountExpr,
      currencyDecimals(child.fx?.original_currency ?? account?.currency ?? this.preference().main_currency),
    );
    if (value === null) {
      this.amountError.set(true);
      return false;
    }
    return this.drafts.accept(owner, { amountExpr: String(value) });
  }

  /** A bubble (or the parent): the outgoing child's pending input is flushed first; its own caches are shown. */
  selectBubble(key: string): void {
    if (this.saving() || this.sheet() || key === this.selected()) {
      return;
    }
    if (this.drafts.select(key, owner => this.flushChild(owner))) {
      this.amountError.set(false);
      this.showChildCaches();
    }
  }

  /** ＋: flush the selected child, add one after it (inherited kind / account) and open the main category grid. */
  addChild(): void {
    const state = this.addState();
    if (!state.visible || state.disabled || this.saving() || this.sheet()) {
      return;
    }
    const owner = this.drafts.capture();
    if (owner && !this.flushChild(owner)) {
      return;
    }
    if (!this.drafts.add(this.accounts())) {
      return;
    }
    this.amountError.set(false);
    this.showChildCaches();
    afterNextRender(() => this.categoryPicker()?.reopen(), { injector: this.injector });
  }

  /** 移除此項: also for an unfinished / invalid draft (its expression is discarded, not flushed). */
  removeChild(): void {
    const child = this.drafts.current();
    if (!child || this.saving() || this.sheet() || this.drafts.removeReason(child.key)) {
      return;
    }
    this.drafts.remove(child.key);
    this.amountError.set(false);
    const survivor = this.drafts.current();
    if (!this.childScope() && survivor && isWritableKind(survivor.kind)) {
      // Back to a plain entry: the single tab follows the survivor's kind.
      this.singleKind.set(survivor.kind);
    }
    // The survivor's own tree and account settings, exactly as selecting its bubble shows them.
    this.showChildCaches();
  }

  openDeleteGroup(event: Event): void {
    if (this.groupId() === null || this.saving() || this.locked()) {
      return;
    }
    this.deleteGroupOpener = event.currentTarget as HTMLElement;
    this.deleteGroupPrompt.set(true);
    afterNextRender(() => this.host.nativeElement.querySelector<HTMLButtonElement>('.delete-group-cancel')?.focus(), {
      injector: this.injector,
    });
  }

  closeDeleteGroup(): void {
    this.deleteGroupPrompt.set(false);
    restoreOverlayFocus(this.deleteGroupOpener, this.host.nativeElement);
    this.deleteGroupOpener = null;
  }

  onDeleteGroupKey(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      this.closeDeleteGroup();
    } else if (event.key === 'Tab') {
      trapFocus(event.currentTarget as HTMLElement, event);
    }
  }

  /** 刪除整組: an explicit confirmation of `DELETE /splits/{id}` (the server refuses protected groups). */
  confirmDeleteGroup(): void {
    const id = this.groupId();
    if (id === null || this.saving() || this.locked()) {
      return;
    }
    this.write(this.accounting.deleteSplit(id), () => {
      this.deleteGroupPrompt.set(false);
      this.deleteGroupOpener = null;
      this.leave();
    });
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
    if (isHandledKey(event)) {
      return;
    }
    if (event.key === 'Escape') {
      // Marked handled so the accounting layout's document-level Esc does not also close its sheet.
      event.preventDefault();
      if (this.sheet()) {
        this.closeSheet();
      } else {
        this.cancel();
      }
      return;
    }
    if (event.key !== 'Enter' || this.sheet() || this.deleteGroupPrompt()) {
      return;
    }
    const target = event.target as HTMLElement | null;
    // The same exclusions as the layout's ⏎ shortcut (a <select> picks, a link / button / summary acts), plus the
    // tag input, which commits its tag.
    if (NO_ENTER_SAVE_TAGS.has(target?.tagName ?? '') || target?.classList.contains('chip-input')) {
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
    if (this.definitionLocked()) {
      return 'MOZE 匯入資料，切換後可編輯';
    }
    if (this.scheduling() && this.fx()) {
      return '排程不支援外幣，請先移除匯率';
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
    if (this.isParty() && !this.counterpartyName().trim() && this.loanEntryId === null) {
      return '請輸入對象';
    }
    return null;
  }

  save(continuous: boolean): void {
    // No save until the record of the current navigation has arrived (a stale form must never be written).
    if (this.loading() || this.saving() || this.feeProposal() || this.deleteGroupPrompt()) {
      return;
    }
    // Edit whose record never arrived (failed load): never fall through to a create.
    if (this.loadFailed()) {
      this.error.set('記錄未載入，無法儲存');
      return;
    }
    if (this.saveResponseInvalid()) {
      this.error.set('儲存回應缺少子項對應，請重新載入');
      return;
    }
    const created = this.createdScheduleId();
    if (created !== null) {
      // R-F2: the definition exists; ✓ retries only its catch-up, whatever the (disabled) fields now say.
      this.error.set(null);
      this.runCatchUp(created, continuous);
      return;
    }
    if (this.childScope()) {
      // Split / group: every child is validated and saved; the parent view never falls back to one child's fields.
      if (this.locked() || this.definitionLocked()) {
        this.error.set('MOZE 匯入資料，切換後可編輯');
        return;
      }
      if (this.unsupported()) {
        this.error.set(this.unsupported());
        return;
      }
      this.saveChildren(continuous);
      return;
    }
    const problem = this.validate();
    if (problem) {
      this.error.set(problem);
      return;
    }
    this.error.set(null);
    // An entry edit never becomes a schedule, whatever the draft says (final review F1).
    if (this.scheduleId() !== null || (this.scheduling() && !this.editing())) {
      this.saveSchedule(continuous && this.scheduleId() === null);
      return;
    }
    // Captured now: the id set when the matching response was applied, not whatever the route says later.
    const targetId = this.entryId();
    const keepGoing = continuous && targetId === null;
    if (this.kind() === 'transfer') {
      this.saving.set(true);
      const groupId = targetId === null ? null : (this.transferEdit()?.groupId ?? null);
      // null: the panel shows its own validation message.
      const panel = this.transferPanel();
      const submittedIds = [panel?.fromId(), panel?.toId()];
      const request = panel?.submit(transferCommonFrom(this.sharedFields()), groupId) ?? null;
      this.write(request, () => {
        rememberRecentAccounts(submittedIds);
        this.finish(keepGoing);
      });
      return;
    }
    if (this.isSystem()) {
      const submittedIds = [this.accountId()];
      const request = this.accounting.createBalanceAdjustment({
        account_id: this.account()!.id,
        target_balance: String(this.target()),
        entry_date: this.entryDate(),
        entry_time: this.entryTime() || null,
        description: this.description().trim() || null,
      });
      this.write(request, () => {
        rememberRecentAccounts(submittedIds);
        this.finish(keepGoing);
      });
      return;
    }
    this.saveChildren(continuous);
  }

  /**
   * The single-entry and split write (spec §2.7): the submitted children, parent, selection and load are captured
   * first; every editable child is validated (the first invalid one is selected); each typed counterparty is
   * resolved once per name; then the planner picks one of the five routes. A split answer maps ids by client key.
   * Errors keep the draft and its dirty state; nothing is retried automatically.
   */
  private saveChildren(continuous: boolean): void {
    if (this.resourcesBlocking()) {
      this.error.set(this.draftLoadsFailed() ? '子項設定讀取失敗，請重新載入子項設定' : '子項設定載入中，請稍候');
      return;
    }
    const owner = this.drafts.capture();
    if (owner && !this.flushChild(owner)) {
      this.error.set('金額算式有誤');
      return;
    }
    const submitted = structuredClone(this.children());
    const parent = structuredClone(this.parent());
    const selected = this.selected();
    const loadId = this.loadId;
    const context = { entryId: this.entryId(), groupId: this.groupId() };
    const owners = submitted.map(child => ({ key: child.key, generation: child.generation }));
    const keepGoing = continuous && context.entryId === null && context.groupId === null;
    const single = submitted.length === 1 && context.groupId === null;
    for (const child of submitted) {
      if (child.protected) {
        continue;
      }
      try {
        fullChild(child, parent, this.accounts(), child.counterpartyId, single);
        if ((child.kind === 'receivable' || child.kind === 'payable') && !child.counterpartyName.trim()) {
          throw new Error('請輸入對象');
        }
      } catch (error) {
        this.selectBubble(child.key);
        this.error.set(error instanceof Error ? error.message : '請檢查子項');
        return;
      }
    }
    this.saving.set(true);
    this.error.set(null);
    this.fieldErrors.set({});
    const ownersCurrent = () =>
      this.loadId === loadId &&
      owners.every(other => this.children().some(child => child.key === other.key && child.generation === other.generation));
    // One request per typed name in this submission (two children naming a new counterparty create it once).
    const partyRequests = new Map<string, Observable<number>>();
    const resolveName = (typed: string): Observable<number> => {
      const name = typed.trim();
      let request = partyRequests.get(name);
      if (!request) {
        request = resolveCounterpartyId(this.accounting, name, this.counterparties(), party => {
          if (ownersCurrent()) {
            this.counterparties.update(rows => (rows.some(other => other.id === party.id) ? rows : [...rows, party]));
          }
        }).pipe(shareReplay({ bufferSize: 1, refCount: true }));
        partyRequests.set(name, request);
      }
      return request;
    };
    const resolutions = submitted.map(child =>
      child.protected || (child.kind !== 'receivable' && child.kind !== 'payable')
        ? of([child.key, null] as const)
        : resolveName(child.counterpartyName).pipe(map(id => [child.key, id] as const)),
    );
    forkJoin(resolutions)
      .pipe(
        map(pairs => new Map(pairs.filter((pair): pair is readonly [string, number] => pair[1] !== null))),
        switchMap(parties => {
          if (!ownersCurrent()) {
            return EMPTY;
          }
          const plan = planEntrySave(submitted, parent, this.accounts(), parties, context);
          return executeEntrySave(this.accounting, plan).pipe(map(result => ({ plan, result })));
        }),
        takeUntilDestroyed(this.destroyRef),
        finalize(() => {
          if (this.loadId === loadId) {
            this.saving.set(false);
          }
        }),
      )
      .subscribe({
        next: ({ plan, result }) => {
          if (this.loadId !== loadId) {
            return;
          }
          this.saving.set(false);
          rememberRecentAccounts(submitted.map(child => child.accountId));
          if (plan.kind === 'create' || plan.kind === 'update') {
            const detail = result as EntryDetail;
            rememberEntryUse(plan.input, Number(plan.input.original_amount ?? plan.input.amount));
            this.markClean();
            if (detail.proposed_fee && Number(detail.proposed_fee) > 0 && !plan.input.fee) {
              this.feeProposal.set({ entryId: detail.id, amount: detail.proposed_fee, input: plan.input, continuous: keepGoing });
              return;
            }
            this.finish(keepGoing);
            return;
          }
          let ids: Map<string, number>;
          try {
            ids = resultIds(result as SplitResult, submitted);
          } catch (error) {
            // The server may have committed: no automatic retry (a second POST could duplicate the split).
            this.error.set(error instanceof Error ? error.message : '請重新載入');
            this.saveResponseInvalid.set(true);
            return;
          }
          this.children.update(rows => rows.map(child => ({ ...child, id: ids.get(child.key) ?? child.id })));
          this.groupId.set((result as SplitResult).group_id);
          this.drafts.droppedNotices.set([]);
          this.markClean();
          if (keepGoing) {
            this.continueEntry();
            return;
          }
          const key = selected === PARENT_KEY ? submitted[0].key : selected;
          // closeTo: the page before this one in history may be the edit page, so the detail's ✕ must not go back().
          void this.router.navigateByUrl(`/accounting/entries/${ids.get(key) ?? ids.get(submitted[0].key)}`, {
            replaceUrl: true,
            state: { closeTo: 'list' },
          });
        },
        error: (error: unknown) => {
          if (this.loadId !== loadId) {
            return;
          }
          this.saving.set(false);
          // 404 / 409 / 422 / network: the draft and its dirty state stay; nothing is resubmitted.
          const routed = memberError(error, submitted.map(child => child.key));
          if (routed) {
            this.selectBubble(routed.key);
            this.fieldErrors.set({ [routed.field]: routed.message });
            this.error.set(routed.message);
          } else if (error instanceof Error) {
            this.error.set(error.message);
          } else {
            this.error.set(writeErrorMessage(error));
          }
        },
      });
  }

  /** 重新載入 after an unmappable split answer: through the discard prompt when the draft is dirty. */
  reloadAfterSave(): void {
    this.registry.requestClose(() => {
      this.saveResponseInvalid.set(false);
      this.markClean();
      if (this.entryId() !== null) {
        this.reload();
      } else {
        void this.router.navigateByUrl('/accounting');
      }
    });
  }

  /** 編輯整個排程: the definition's fields into the form; an answer for an older navigation is dropped. */
  private loadDefinition(definitionId: number): void {
    const request = ++this.definitionRequest;
    this.loading.set(true);
    this.accounting
      .getScheduleDefinition(definitionId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: definition => {
          if (request === this.definitionRequest) {
            this.applyDefinition(definition);
            this.loading.set(false);
          }
        },
        error: () => {
          if (request === this.definitionRequest) {
            this.loading.set(false);
            this.error.set('排程讀取失敗，請稍後再試。');
          }
        },
      });
  }

  private applyDefinition(definition: ScheduleDefinition): void {
    const form = draftFromDefinition(definition);
    this.definitionLoaded.set(true);
    this.definitionLocked.set(definition.locked);
    if (!formCanRepresent(definition)) {
      // Saving would drop or change template lines: shown, never written (validate() refuses through unsupported).
      this.definitionReadOnly.set(true);
      this.unsupported.set(FORM_CANNOT_REPRESENT);
      this.error.set(FORM_CANNOT_REPRESENT);
    }
    this.originalAccountId.set(form.accountId);
    this.loanEntryId = form.loanEntryId;
    this.loadedDefinition = definition;
    this.kind.set(form.kind);
    this.scheduleDraft.set(form.draft);
    this.name.set(form.name);
    this.merchant.set(form.merchant);
    this.description.set(form.description);
    this.tags.set(form.tags);
    this.projectId.set(form.projectId);
    this.accountId.set(form.accountId);
    this.amountExpr.set(form.amount);
    this.counterpartyName.set(form.counterparty ?? '');
    const owner = this.drafts.capture();
    if (owner) {
      this.drafts.accept(owner, { categoryId: form.categoryId });
    }
    this.transferEdit.set(form.transfer);
    this.markClean();
  }

  /** 週期 / 分期: create (then catch-up when it starts today, 自動入帳) or, in definition mode, PUT the definition. */
  private saveSchedule(keepGoing: boolean): void {
    this.fieldErrors.set({});
    // R-F2: once a create has answered, save() routes ✓ to runCatchUp and never reaches here for that definition.
    const panel = this.transferPanel();
    const transfer = this.kind() === 'transfer' ? (panel?.buildInput(transferCommonFrom(this.sharedFields())) ?? null) : null;
    if (this.kind() === 'transfer' && !transfer) {
      return; // the panel shows its own message
    }
    // Remember the submitted accounts only at definition success, even before a catch-up completes.
    const recentAccountIds = this.kind() === 'transfer'
      ? [panel?.fromId(), panel?.toId()]
      : [this.accountId()];
    const repayId = this.scheduleDraft().repayAccountId;
    const counterparty: Observable<number | null> = this.isParty() && this.loanEntryId === null
      ? resolveCounterpartyId(this.accounting, this.counterpartyName(), this.counterparties(), party =>
          this.counterparties.update(list => [...list, party]),
        )
      : of(null);
    const definitionId = this.scheduleId();
    const request = counterparty.pipe(
      map(counterpartyId =>
        buildDefinitionInput({
          kind: this.kind(),
          draft: this.scheduleDraft(),
          name: this.name(),
          merchant: this.merchant(),
          description: this.description(),
          tags: this.tags(),
          projectId: this.projectId(),
          categoryId: this.current()?.categoryId ?? null,
          categoryName: this.category()?.name ?? null,
          account: this.account(),
          amount: this.amount(),
          counterpartyId,
          transfer,
          transferFrom: panel?.from() ?? null,
          transferTo: panel?.to() ?? null,
          repayAccount: repayId === null ? null : (this.accounts().find(account => account.id === repayId) ?? null),
          entryDate: this.entryDate(),
          loanEntryId: this.loanEntryId,
        }, { original: definitionId !== null ? this.loadedDefinition : null }),
      ),
      switchMap(input =>
        definitionId !== null
          ? this.accounting.updateScheduleDefinition(definitionId, definitionUpdateFrom(input)).pipe(map(() => null))
          : this.accounting
              .createScheduleDefinition(input)
              .pipe(map(created => (shouldCatchUp(input, todayIso()) ? created.id : null))),
      ),
    );
    this.saving.set(true);
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: catchUpId => {
        rememberRecentAccounts(recentAccountIds);
        this.saving.set(false);
        this.markClean();
        if (catchUpId !== null) {
          this.createdScheduleId.set(catchUpId); // creation is complete from here on (R-F2)
          this.runCatchUp(catchUpId, keepGoing);
          return;
        }
        this.finish(keepGoing);
      },
      error: (error: unknown) => {
        this.saving.set(false);
        if (error instanceof ScheduleFormError) {
          this.fieldErrors.set({ [error.field]: error.message });
          this.error.set(error.message);
          return;
        }
        if (isImportRunning(error)) {
          this.toast.show(IMPORT_RUNNING_TOAST);
          return;
        }
        this.fieldErrors.set(fieldErrors(error));
        this.error.set(writeErrorMessage(error));
      },
    });
  }

  /** 補入帳 right after a create that starts today (自動入帳), and its retry (R-F2). A 200 with `failed` is surfaced in
   *  the toast, never discarded; an error keeps `createdScheduleId`, so ✓ retries this call only. */
  private runCatchUp(definitionId: number, keepGoing: boolean): void {
    this.saving.set(true);
    this.accounting
      .catchUpSchedule(definitionId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: result => {
          this.saving.set(false);
          this.createdScheduleId.set(null);
          if (result.failed !== null) {
            this.toast.show(CATCH_UP_FAILED_TOAST);
          }
          this.finish(keepGoing);
        },
        error: (error: unknown) => {
          this.saving.set(false);
          if (isImportRunning(error)) {
            this.toast.show(IMPORT_RUNNING_TOAST);
          }
          this.error.set(CATCH_UP_RETRY_TEXT);
        },
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
        this.markClean();
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
      entryTime: this.entryTime() ?? '',
      postedDate: this.postedDate() ?? '',
      name: this.name(),
      merchant: this.merchant(),
      description: this.description(),
      projectId: this.projectId(),
      tags: this.tags(),
    };
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
    this.markClean();
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
    const child = this.drafts.current() ?? this.children()[0];
    const kind: FormKind = this.childScope()
      ? ((child && isWritableKind(child.kind) ? child.kind : this.children().find(row => isWritableKind(row.kind))?.kind) as FormKind | undefined) ?? 'expense'
      : this.kind();
    const accountId = child?.accountId ?? null;
    const date = this.entryDate();
    this.resetFields(kind);
    this.accountId.set(accountId);
    this.parent.update(parent => ({ ...parent, entryDate: date }));
    this.savedFlash.set(true);
    if (this.flashTimer) {
      clearTimeout(this.flashTimer);
    }
    this.flashTimer = setTimeout(() => this.savedFlash.set(false), FLASH_MS);
    this.markClean();
  }

  /** ✕, Esc and the cancel shortcut: a dirty draft first asks 放棄未儲存的內容？ (DirtyFormRegistry). */
  cancel(): void {
    this.registry.requestClose(() => this.doCancel());
  }

  private doCancel(): void {
    this.markClean();
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
