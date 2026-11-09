import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  OnInit,
  afterNextRender,
  computed,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, Observable, Subject, catchError, map, merge, of, switchMap, tap } from 'rxjs';

import {
  AccountDetail,
  AccountGroup,
  AccountInput,
  LedgerAccount,
  Project,
  RewardRule,
  RewardRuleState,
  RewardUsage,
  RoundingMode,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { ACCOUNT_LOGOS, accountLogo } from '../../../theme/account-logos';
import { AccountIconComponent } from '../account-icon/account-icon';
import { accountLabel, isHandledKey, restoreOverlayFocus } from '../accounting-ui';
import { DueRule, dueDate, periodLabel, statementPeriod } from '../cycle';
import { DirtyAware, DirtyFormRegistry } from '../dirty-form.service';
import { shortDate, slashDate, todayIso } from '../dates';
import { balanceNegative, balanceText, valuationHint } from '../account-balance';
import { decimalText, formatMoney } from '../format';
import { fieldErrors, writeErrorMessage } from '../http-errors';
import { AccountingToastService } from '../accounting-toast';
import { LOGO_ACCEPT, logoFileTooLarge, logoUploadErrorMessage } from '../logo-upload-errors';
import { RewardRuleSheetComponent } from '../reward-rule-sheet/reward-rule-sheet';
import {
  REWARD_WINDOW_LABELS, RULE_STATE_LABELS, basicOverlapRuleId, periodText, postingSummary, ruleLabel, ruleState, ruleToInput,
} from '../reward-rule-text';
import { SwitchComponent } from '../../ui/switch/switch';

export { decimalText } from '../format';

export const ROUNDING_LABELS: Record<RoundingMode, string> = {
  keep: '不處理',
  round: '四捨五入',
  floor: '無條件捨去',
  ceil: '無條件進位',
};

interface AccountForm {
  name: string;
  currency: string;
  opening_balance: string;
  group_id: number | null;
  icon: string;
  logo: string | null;
  color: string;
  note: string;
  sort_order: number;
  include_in_total: boolean;
  is_archived: boolean;
  is_credit: boolean;
  closing_day: string;
  due_rule: DueRule | '';
  due_value: string;
  credit_limit: string;
  combined_account_id: number | null;
  credit_sharing_id: string | null;
  auto_pay_account_id: number | null;
  fx_fee_on: boolean;
  fx_fee_pct: string;
  fx_fee_rounding: RoundingMode;
  fx_fee_refundable: boolean;
}

const EMPTY_FORM: AccountForm = {
  name: '', currency: 'TWD', opening_balance: '0', group_id: null, icon: '', logo: null, color: '', note: '', sort_order: 0,
  include_in_total: true, is_archived: false, is_credit: false, closing_day: '', due_rule: '', due_value: '',
  credit_limit: '', combined_account_id: null, credit_sharing_id: null, auto_pay_account_id: null,
  fx_fee_on: false, fx_fee_pct: '', fx_fee_rounding: 'keep', fx_fee_refundable: false,
};

function intOrNull(value: string): number | null {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

/** A whole number within [min, max], typed as text. */
function validDay(text: string, min: number, max: number): boolean {
  return /^\d+$/.test(text) && Number(text) >= min && Number(text) <= max;
}

function textOrNull(value: string): string | null {
  return value.trim() ? value.trim() : null;
}

/** Full `AccountInput` of a loaded account; omits `credit_sharing_members`, so saving it leaves the membership alone. */
export function accountToInput(account: AccountDetail): AccountInput {
  return {
    name: account.name,
    currency: account.currency,
    opening_balance: decimalText(account.opening_balance) || '0',
    is_archived: account.is_archived,
    group_id: account.group_id,
    icon: account.icon,
    logo: account.logo,
    color: account.color,
    note: account.note ?? null,
    sort_order: account.sort_order,
    include_in_total: account.include_in_total,
    is_credit: account.is_credit,
    closing_day: account.closing_day,
    due_rule: account.due_rule,
    due_value: account.due_value,
    credit_limit: account.credit_limit === null ? null : decimalText(account.credit_limit),
    combined_account_id: account.combined_account_id,
    credit_sharing_id: account.credit_sharing_id,
    auto_pay_account_id: account.auto_pay_account_id,
    fx_fee_pct: account.fx_fee_pct === null ? null : decimalText(account.fx_fee_pct),
    fx_fee_rounding: account.fx_fee_rounding,
    fx_fee_refundable: account.fx_fee_refundable,
  };
}

function formFrom(account: AccountDetail): AccountForm {
  return {
    name: account.name,
    currency: account.currency,
    opening_balance: decimalText(account.opening_balance) || '0',
    group_id: account.group_id,
    icon: account.icon ?? '',
    logo: account.logo ?? null,
    color: account.color ?? '',
    note: account.note ?? '',
    sort_order: account.sort_order,
    include_in_total: account.include_in_total,
    is_archived: account.is_archived,
    is_credit: account.is_credit,
    closing_day: account.closing_day === null ? '' : String(account.closing_day),
    due_rule: account.due_rule ?? '',
    due_value: account.due_value === null ? '' : String(account.due_value),
    credit_limit: decimalText(account.credit_limit),
    combined_account_id: account.combined_account_id,
    credit_sharing_id: account.credit_sharing_id,
    auto_pay_account_id: account.auto_pay_account_id,
    fx_fee_on: account.fx_fee_pct !== null,
    fx_fee_pct: decimalText(account.fx_fee_pct),
    fx_fee_rounding: (account.fx_fee_rounding ?? 'keep') as RoundingMode,
    fx_fee_refundable: account.fx_fee_refundable,
  };
}

/** `members`: the other cards sharing this account's limit; sent in full, the backend applies the diff (Task 16). */
function formToInput(form: AccountForm, members: number[]): AccountInput {
  const credit = form.is_credit;
  return {
    name: form.name.trim(),
    currency: form.currency.trim().toUpperCase(),
    opening_balance: form.opening_balance.trim() || '0',
    is_archived: form.is_archived,
    group_id: form.group_id,
    icon: textOrNull(form.icon),
    logo: form.logo,
    color: textOrNull(form.color),
    note: textOrNull(form.note),
    sort_order: form.sort_order,
    include_in_total: form.include_in_total,
    is_credit: credit,
    closing_day: intOrNull(form.closing_day),
    due_rule: credit && form.due_rule ? form.due_rule : null,
    due_value: credit && form.due_rule ? intOrNull(form.due_value) : null,
    credit_limit: credit ? textOrNull(form.credit_limit) : null,
    combined_account_id: credit ? form.combined_account_id : null,
    credit_sharing_id: credit ? form.credit_sharing_id : null,
    credit_sharing_members: credit ? [...members].sort((a, b) => a - b) : [],
    auto_pay_account_id: credit ? form.auto_pay_account_id : null,
    fx_fee_pct: form.fx_fee_on ? textOrNull(form.fx_fee_pct) : null,
    fx_fee_rounding: form.fx_fee_on ? form.fx_fee_rounding : null,
    fx_fee_refundable: form.fx_fee_on && form.fx_fee_refundable,
  };
}

function sameForm(a: AccountForm, b: AccountForm): boolean {
  return (Object.keys(a) as (keyof AccountForm)[]).every(key => a[key] === b[key]);
}

function sameMembers(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every(id => b.includes(id));
}

interface LoadedAccount {
  loadId: number;
  id: number | null;
  detail: AccountDetail | null;
}

@Component({
  selector: 'app-account-settings',
  standalone: true,
  imports: [RouterLink, AccountIconComponent, RewardRuleSheetComponent, SwitchComponent],
  templateUrl: './account-settings.html',
  styleUrl: './account-settings.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onKeydown($event)' },
})
export class AccountSettingsComponent implements OnInit, DirtyAware {
  private service = inject(AccountingService);
  private readonly toast = inject(AccountingToastService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  /** N, 記一筆 and the layout's ✕ / Esc / backdrop ask before unsaved input is dropped (spec §4.3). */
  readonly dirty = inject(DirtyFormRegistry);
  /** Focused element when 放棄未儲存的內容？ opened; 留下 returns focus there. */
  private discardOpener: HTMLElement | null = null;

  private destroyRef = inject(DestroyRef);
  private loadId = 0;
  /** resetFlag() requests; merged into the navigation sequence so a navigation cancels them. */
  private readonly resets = new Subject<number>();

  /** From `paramMap` (observable): at ≥ 760 px the pane reuses this component when only `:id` changes (Task 21). */
  readonly accountId = signal<number | null>(null);
  /** Id of the account whose response is applied to the form; writes target this id only. */
  readonly loadedId = signal<number | null>(null);
  readonly loading = signal(false);
  /** Re-read on every open (navigation), so a page kept open past midnight shows the current cycle. */
  readonly today = signal(todayIso());

  readonly detail = signal<AccountDetail | null>(null);
  readonly accounts = signal<LedgerAccount[]>([]);
  readonly groups = signal<AccountGroup[]>([]);
  readonly form = signal<AccountForm>(EMPTY_FORM);
  readonly sharing = signal<number[]>([]);
  /** The form and 額度共用 selection as loaded (or last saved): anything else is unsaved input. */
  private readonly baseline = signal<AccountForm>(EMPTY_FORM);
  private readonly sharingBaseline = signal<number[]>([]);
  readonly errors = signal<Record<string, string>>({});
  readonly message = signal<string | null>(null);
  readonly saving = signal(false);
  readonly confirmDelete = signal(false);

  protected readonly logos = ACCOUNT_LOGOS;
  readonly logoPickerOpen = signal(false);
  protected readonly LOGO_ACCEPT = LOGO_ACCEPT;
  /** Uploaded image hash. Patched by upload/remove only; apply() never runs for an image write (dirty form stays). */
  readonly logoHash = signal<string | null>(null);
  readonly uploadingLogo = signal(false);
  /** 無 for no logo; 未知 logo for a stored slug the bundled set does not know (the form still keeps and re-saves it). */
  readonly logoLabel = computed(() => {
    const slug = this.form().logo;
    return slug === null ? '無' : (accountLogo(slug)?.label ?? '未知 logo');
  });
  readonly logoState = computed(() => (this.logoHash() ? '已上傳的圖片' : this.form().logo ? `內建：${this.logoLabel()}` : '無'));

  readonly roundingOptions = Object.entries(ROUNDING_LABELS) as [RoundingMode, string][];
  readonly formatMoney = formatMoney;
  readonly balanceText = balanceText;
  readonly balanceNegative = balanceNegative;
  readonly valuationHint = valuationHint;

  readonly currencyLocked = computed(() => (this.detail()?.entry_count ?? 0) > 0);
  /** Edit mode whose account never loaded: nothing to save or delete (✓ and 刪除 stay disabled). */
  readonly loadFailed = computed(() => this.accountId() !== null && !this.loading() && this.loadedId() !== this.accountId());
  /** Errors found before sending: closing day 1–31, due value > 0 (a fixed due day also ≤ 31). */
  readonly localErrors = computed(() => {
    const form = this.form();
    const errors: Record<string, string> = {};
    const closing = form.closing_day.trim();
    if (closing && !validDay(closing, 1, 31)) {
      errors['closing_day'] = '結帳日須為 1–31';
    }
    const due = form.due_value.trim();
    if (form.is_credit && form.due_rule && due) {
      if (form.due_rule === 'fixed_day' && !validDay(due, 1, 31)) {
        errors['due_value'] = '繳款日須為 1–31';
      } else if (form.due_rule === 'days_after_closing' && !validDay(due, 1, 366)) {
        errors['due_value'] = '請輸入大於 0 的天數';
      }
    }
    return errors;
  });
  readonly closingDay = computed(() => intOrNull(this.form().closing_day));
  readonly period = computed(() => statementPeriod(this.closingDay(), this.today()));
  readonly cycleLabel = computed(() => {
    if (this.localErrors()['closing_day']) {
      return '';
    }
    return this.closingDay() === null ? '每月 1 日 – 月底' : periodLabel(this.period());
  });
  readonly dueLabel = computed(() => {
    const form = this.form();
    const value = intOrNull(form.due_value);
    if (!form.due_rule || value === null || this.localErrors()['closing_day'] || this.localErrors()['due_value']) {
      return '';
    }
    const due = dueDate(this.period().end, form.due_rule, value);
    const rule = form.due_rule === 'days_after_closing' ? `結帳日後 ${value} 天` : `每月 ${value} 日`;
    return due ? `${rule} → ${shortDate(due)}` : rule;
  });
  private readonly others = computed(() =>
    this.accounts().filter(account => account.id !== this.accountId() && !account.is_archived),
  );
  readonly creditOthers = computed(() => this.others().filter(account => account.is_credit));
  /** The saved value of an id field, when it points at an archived account (edit mode): kept selectable, labelled. */
  private archivedCurrent(id: number | null): LedgerAccount[] {
    if (this.accountId() === null || id === null) {
      return [];
    }
    const current = this.accounts().find(account => account.id === id && account.is_archived);
    return current ? [current] : [];
  }
  /** 主帳戶: other open credit accounts, plus the saved one when it is archived (as the entry form does). */
  readonly masterOptions = computed(() => [...this.creditOthers(), ...this.archivedCurrent(this.form().combined_account_id)]);
  /** 自動扣繳: other open non-credit accounts, plus the saved one when it is archived. */
  readonly autopayOptions = computed(() => [
    ...this.others().filter(account => !account.is_credit),
    ...this.archivedCurrent(this.form().auto_pay_account_id),
  ]);

  readonly accountLabel = accountLabel;
  /**
   * 額度共用 options: other non-archived credit accounts plus any saved member (an archived one too, so it can be
   * removed; it stays listed after being unchecked). New members can only be non-archived credit accounts.
   */
  readonly sharingOptions = computed(() => {
    const saved = this.detail()?.credit_sharing_members ?? [];
    return this.accounts().filter(
      account =>
        account.id !== this.accountId() &&
        (saved.includes(account.id) || (account.is_credit && !account.is_archived)),
    );
  });
  /** Current members from `AccountDetail.credit_sharing_members` (the saved state, not the pending selection). */
  readonly sharingCurrent = computed(() => {
    const self = this.loadedId();
    const names = (this.detail()?.credit_sharing_members ?? [])
      .filter(id => id !== self)
      .map(id => this.accounts().find(account => account.id === id)?.name ?? `#${id}`);
    return names.length ? `與 ${names.join('、')} 共用額度` : null;
  });
  /** 回饋規則 block (spec §6.1): rows in the order the API returns; edits save at once, so not part of isDirty(). */
  readonly rules = computed<RewardRule[]>(() => this.detail()?.reward_rules ?? []);
  /** `GET /reward-usage`: 本期 usage per in-date rule or shared-cap group; empty until loaded (or when it fails). */
  readonly usage = signal<RewardUsage[]>([]);
  /**
   * The usage line a rule row shows. A shared group appears once in `usage`, but its `rule_ids` may list members
   * the rules list does not show, so it goes on the first listed rule that is a member.
   */
  readonly usageByRule = computed(() => {
    const byRule = new Map<number, RewardUsage>();
    const ids = this.rules().map(rule => rule.id);
    for (const item of this.usage()) {
      const first = ids.find(id => item.rule_ids.includes(id));
      if (first !== undefined) {
        byRule.set(first, item);
      }
    }
    return byRule;
  });
  readonly projects = signal<Project[]>([]);
  readonly ruleSheet = signal<{ rule: RewardRule | null } | null>(null);
  /** The open editor sheet; its unsaved input counts as the page's (spec §6.1 dirty-form rules). */
  private readonly ruleSheetRef = viewChild(RewardRuleSheetComponent);
  /**
   * A route-leave prompt handed to the open rule sheet: the sheet (position: fixed, above the page) shows its own
   * 放棄未儲存的內容？ strip and this page's strip stays hidden; the sheet's 放棄 / 留下 resolve the pending close.
   */
  readonly sheetHoldsPrompt = signal(false);
  readonly ruleMessage = signal<string | null>(null);
  /** A 啟用 / 上移 / 下移 write is in flight: those controls wait so the next write starts from the saved order. */
  readonly ruleBusy = signal(false);
  /** `?rule=<id>` (from 回饋規則總覽): opens that rule once its account's detail is shown, then leaves the URL. */
  /** `?rule=<id>` to open that rule once loaded, or `'new'` for an empty sheet (the overview's ＋ links). */
  private pendingRuleId: number | 'new' | null = null;
  readonly ruleLabel = ruleLabel;
  readonly windowLabels = REWARD_WINDOW_LABELS;
  readonly postingSummary = postingSummary;
  readonly periodText = periodText;
  readonly ruleStateLabels = RULE_STATE_LABELS;

  ngOnInit(): void {
    this.dirty.register(this);
    this.destroyRef.onDestroy(() => this.dirty.unregister(this));
    const navigations = this.route.paramMap.pipe(
      map(params => {
        const idParam = params.get('id');
        return idParam === null ? null : Number(idParam);
      }),
      tap(id => {
        this.today.set(todayIso());
        this.accountId.set(id);
        // A save of the previous account may still be in flight; its answer is dropped (see save()).
        this.saving.set(false);
        this.loadedId.set(null);
        this.logoHash.set(null);
        this.uploadingLogo.set(false);
        this.detail.set(null);
        this.form.set(EMPTY_FORM);
        this.sharing.set([]);
        this.baseline.set(EMPTY_FORM);
        this.sharingBaseline.set([]);
        this.errors.set({});
        this.message.set(null);
        this.confirmDelete.set(false);
        this.ruleSheet.set(null);
        this.sheetHoldsPrompt.set(false);
        this.ruleMessage.set(null);
        this.ruleBusy.set(false);
      }),
      map(id => this.fetch(id)),
    );
    const resets = this.resets.pipe(map(id => this.resetAndReload(id)));
    merge(navigations, resets)
      .pipe(
        // One sequence: a navigation drops the previous account's load or reset chain; the loadId check in
        // apply() covers any response that still arrives.
        switchMap(load => load),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(loaded => this.apply(loaded));
    // Archived accounts too: an archived shared-limit member must be shown so it can be removed.
    this.service.getAccounts(true).subscribe(accounts => this.accounts.set(accounts));
    this.service.getAccountGroups().subscribe(groups => this.groups.set(groups));
    this.service.getProjects().subscribe(projects => this.projects.set(projects));
    this.route.queryParamMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(query => {
      const ruleParam = query.get('rule');
      if (ruleParam !== null) {
        this.pendingRuleId = ruleParam === 'new' ? 'new' : Number(ruleParam);
        this.openPendingRule();
      }
    });
  }

  /** Acts on `?rule=` once the shown account's detail is in: opens the rule if it is this account's (or an empty sheet
   * for `new`), then clears it. */
  private openPendingRule(): void {
    const detail = this.detail();
    if (this.pendingRuleId === null || detail === null || this.loadedId() !== this.accountId()) {
      return;
    }
    const pending = this.pendingRuleId;
    this.pendingRuleId = null;
    if (pending === 'new') {
      this.openRule(null);
    } else {
      const rule = detail.reward_rules.find(r => r.id === pending);
      if (rule) {
        this.openRule(rule);
      }
    }
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { rule: null },
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  /** Starts a load with a new loadId; a new account (`id === null`) completes at once with no detail. */
  private fetch(id: number | null): Observable<LoadedAccount> {
    const loadId = ++this.loadId;
    if (id === null) {
      this.loading.set(false);
      return of({ loadId, id, detail: null });
    }
    this.loading.set(true);
    return this.service.getAccount(id).pipe(
      map(detail => ({ loadId, id, detail })),
      catchError(err => {
        if (loadId === this.loadId) {
          this.loading.set(false);
          this.message.set(writeErrorMessage(err));
        }
        return EMPTY;
      }),
    );
  }

  /**
   * `resetSettingsFlag` then the refetch, tagged with the loadId of the account being shown; nothing is refetched or
   * applied once another navigation has started.
   */
  private resetAndReload(id: number): Observable<LoadedAccount> {
    const loadId = this.loadId;
    this.loading.set(true);
    return this.service.resetSettingsFlag(id).pipe(
      switchMap(() => (loadId === this.loadId ? this.service.getAccount(id) : EMPTY)),
      map(detail => ({ loadId, id, detail })),
      catchError(err => {
        if (loadId === this.loadId) {
          this.loading.set(false);
          this.message.set(writeErrorMessage(err));
        }
        return EMPTY;
      }),
    );
  }

  /** Applies only the latest load; captures the id that save/remove/resetFlag will target. */
  private apply(loaded: LoadedAccount): void {
    if (loaded.loadId !== this.loadId) {
      return;
    }
    this.loading.set(false);
    this.loadedId.set(loaded.id);
    this.detail.set(loaded.detail);
    this.logoHash.set(loaded.detail?.logo_hash ?? null);
    this.form.set(loaded.detail ? formFrom(loaded.detail) : EMPTY_FORM);
    this.sharing.set((loaded.detail?.credit_sharing_members ?? []).filter(id => id !== loaded.id));
    this.baseline.set(this.form());
    this.sharingBaseline.set(this.sharing());
    if (loaded.id !== null) {
      this.loadUsage(loaded.loadId, loaded.id, loaded.detail);
    }
    this.openPendingRule();
  }

  /** 本期 usage for the shown account; a failure only hides the lines. Dropped once another navigation has started. */
  private loadUsage(loadId: number, id: number, detail: AccountDetail | null): void {
    if (!detail || detail.reward_rules.length === 0) {
      this.usage.set([]);
      return;
    }
    this.service
      .getRewardUsage(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: usage => {
          if (loadId === this.loadId) {
            this.usage.set(usage);
          }
        },
        error: () => {
          if (loadId === this.loadId) {
            this.usage.set([]);
          }
        },
      });
  }

  /** 「本期 {used+estimated}／{cap}」, or 「本期 {used+estimated}」 when uncapped. */
  usageText(item: RewardUsage): string {
    const total = decimalText((Number(item.used) + Number(item.estimated)).toFixed(4));
    return item.cap === null ? `本期 ${total}` : `本期 ${total}／${decimalText(item.cap)}`;
  }

  usageWindow(item: RewardUsage): string {
    return `${slashDate(item.window_start)} – ${slashDate(item.window_end)}`;
  }

  /** The form, 額度共用, or an open rule sheet with unsaved input; the rule rows themselves save at once. */
  isDirty(): boolean {
    return (
      !sameForm(this.form(), this.baseline()) ||
      !sameMembers(this.sharing(), this.sharingBaseline()) ||
      (this.ruleSheet() !== null && (this.ruleSheetRef()?.isDirty() ?? false))
    );
  }

  showDiscardPrompt(): void {
    const sheet = this.ruleSheet() !== null ? this.ruleSheetRef() : undefined;
    if (sheet?.isDirty()) {
      if (sheet.busy()) {
        // The sheet ignores closing while a write is in flight; drop this leave attempt rather than hold it unseen.
        this.dirty.clearPending();
        return;
      }
      this.sheetHoldsPrompt.set(true);
      sheet.cancel();
      return;
    }
    if (!this.discardOpener) {
      this.discardOpener = this.host.nativeElement.ownerDocument.activeElement as HTMLElement | null;
    }
    afterNextRender(() => this.host.nativeElement.querySelector<HTMLElement>('.discard-stay')?.focus(), {
      injector: this.injector,
    });
  }

  restoreDiscardFocus(): void {
    const opener = this.discardOpener;
    this.discardOpener = null;
    restoreOverlayFocus(opener, this.host.nativeElement);
  }

  /** Esc on an open 放棄 strip is 留下; marked handled so the layout does not ask again. */
  onKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Escape' || isHandledKey(event) || !this.dirty.promptOpen()) {
      return;
    }
    event.preventDefault();
    this.dirty.cancelDiscard();
  }

  set<K extends keyof AccountForm>(key: K, value: AccountForm[K]): void {
    this.form.update(form => ({ ...form, [key]: value }));
  }

  pickLogo(slug: string | null): void {
    this.set('logo', slug);
    this.logoPickerOpen.set(false);
  }

  /** Immediate image write; patches only logoHash so the dirty form and its baseline are untouched (spec 6.3). */
  uploadLogo(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    const id = this.loadedId();
    if (!file || id === null) {
      return;
    }
    if (logoFileTooLarge(file)) {
      this.toast.show('圖片太大（上限 5 MB）');
      return;
    }
    this.uploadingLogo.set(true);
    this.service.uploadAccountLogo(id, file).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: detail => {
        if (id === this.loadedId()) {
          this.logoHash.set(detail.logo_hash ?? null);
        }
      },
      error: err => {
        if (id === this.loadedId()) {
          this.uploadingLogo.set(false);
          this.toast.show(logoUploadErrorMessage(err));
        }
      },
      complete: () => {
        if (id === this.loadedId()) {
          this.uploadingLogo.set(false);
        }
      },
    });
  }

  removeLogo(): void {
    const id = this.loadedId();
    if (id === null) {
      return;
    }
    this.uploadingLogo.set(true);
    this.service.deleteAccountLogo(id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        if (id === this.loadedId()) {
          this.logoHash.set(null);
        }
      },
      error: err => {
        if (id === this.loadedId()) {
          this.uploadingLogo.set(false);
          this.toast.show(logoUploadErrorMessage(err));
        }
      },
      complete: () => {
        if (id === this.loadedId()) {
          this.uploadingLogo.set(false);
        }
      },
    });
  }

  toggle(key: 'is_credit' | 'include_in_total' | 'is_archived' | 'fx_fee_on' | 'fx_fee_refundable'): void {
    this.set(key, !this.form()[key]);
  }

  setIdField(key: 'group_id' | 'combined_account_id' | 'auto_pay_account_id', value: string): void {
    this.set(key, value ? Number(value) : null);
  }

  toggleSharing(id: number, checked: boolean): void {
    this.sharing.update(list => (checked ? [...list.filter(x => x !== id), id] : list.filter(x => x !== id)));
  }

  error(field: string): string | null {
    return this.errors()[field] ?? this.localErrors()[field] ?? null;
  }

  ruleStateOf(rule: RewardRule): RewardRuleState {
    return ruleState(rule, this.today());
  }

  openRule(rule: RewardRule | null): void {
    this.ruleMessage.set(null);
    this.ruleSheet.set({ rule });
  }

  closeRuleSheet(): void {
    this.ruleSheet.set(null);
    if (!this.sheetHoldsPrompt()) {
      return;
    }
    // 放棄 on the sheet's strip answered a delegated route-leave prompt.
    this.sheetHoldsPrompt.set(false);
    if (this.isDirty()) {
      this.showDiscardPrompt(); // the page's own form is still dirty: ask here, now that the sheet is gone
    } else {
      this.dirty.confirmDiscard();
    }
  }

  /** 留下 on the sheet's strip: keep the draft and forget the delegated close (the sheet keeps its focus). */
  onRuleSheetStayed(): void {
    if (this.sheetHoldsPrompt()) {
      this.sheetHoldsPrompt.set(false);
      this.dirty.clearPending();
    }
  }

  onRuleSaved(): void {
    this.onRuleSheetStayed(); // saving answers a delegated leave prompt with 留下: the owner chose to keep working
    this.closeRuleSheet();
    this.reloadRules();
  }

  onRuleDeleted(): void {
    this.onRuleSheetStayed();
    this.closeRuleSheet();
    this.reloadRules();
  }

  /** 啟用 saves at once: the full rule as loaded with `is_enabled` flipped, then the account is re-read. */
  toggleRule(rule: RewardRule, enabled: boolean): void {
    const id = this.loadedId();
    if (id === null || this.ruleBusy()) {
      return;
    }
    this.ruleMessage.set(null);
    this.ruleBusy.set(true);
    const loadId = this.loadId;
    this.service
      .updateRewardRule(id, rule.id, { ...ruleToInput(rule), is_enabled: enabled })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => this.reloadRules(loadId),
        error: err => this.ruleWriteFailed(loadId, err),
      });
  }

  /** 上移 (-1) / 下移 (+1): swaps two neighbours and sends the whole order; the response is the new list. */
  moveRule(index: number, delta: number): void {
    const id = this.loadedId();
    const ids = this.rules().map(rule => rule.id);
    const target = index + delta;
    if (id === null || this.ruleBusy() || target < 0 || target >= ids.length) {
      return;
    }
    [ids[index], ids[target]] = [ids[target], ids[index]];
    this.ruleMessage.set(null);
    this.ruleBusy.set(true);
    const loadId = this.loadId;
    this.service
      .reorderRewardRules(id, ids)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: rules => {
          if (loadId !== this.loadId) {
            return;
          }
          this.ruleBusy.set(false);
          this.detail.update(detail => (detail ? { ...detail, reward_rules: rules } : detail));
        },
        error: err => this.ruleWriteFailed(loadId, err),
      });
  }

  private ruleWriteFailed(loadId: number, err: unknown): void {
    if (loadId !== this.loadId) {
      return;
    }
    this.ruleMessage.set(this.ruleErrorMessage(err));
    this.reloadRules(loadId);
  }

  /** A 409 `basic_rule_overlap` names the clashing rule like the sheet does; anything else is the generic message. */
  private ruleErrorMessage(err: unknown): string {
    const overlap = basicOverlapRuleId(err);
    if (overlap === null) {
      return writeErrorMessage(err);
    }
    const name = this.rules().find(rule => rule.id === overlap)?.name ?? `#${overlap}`;
    return `與「${name}」的基本回饋期間重疊`;
  }

  /**
   * Re-reads the shown account so the block (and the entry form's chips, which read the same `AccountDetail`) match
   * the server. Only the detail is replaced: the form and its baseline keep any unsaved input. Dropped once another
   * navigation has started.
   */
  private reloadRules(loadId = this.loadId): void {
    const id = this.loadedId();
    if (id === null || loadId !== this.loadId) {
      return;
    }
    this.ruleBusy.set(true);
    this.service
      .getAccount(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: detail => {
          if (loadId !== this.loadId) {
            return;
          }
          this.ruleBusy.set(false);
          this.detail.set(detail);
          this.loadUsage(loadId, id, detail);
        },
        error: err => {
          if (loadId !== this.loadId) {
            return;
          }
          this.ruleBusy.set(false);
          this.ruleMessage.set(this.ruleErrorMessage(err));
        },
      });
  }

  save(): void {
    if (this.loading() || this.saving() || this.loadFailed()) {
      return;
    }
    const routeId = this.accountId();
    const editingId = this.loadedId();
    if (routeId !== null && editingId !== routeId) {
      return; // the shown form does not belong to the account in the URL (load failed or pending)
    }
    this.errors.set({});
    this.message.set(null);
    const form = this.form();
    if (!form.name.trim()) {
      this.errors.set({ name: '請輸入名稱' });
      return;
    }
    if (Object.keys(this.localErrors()).length > 0) {
      return; // shown inline beside their fields
    }
    const input = formToInput(form, this.sharing());
    const request: Observable<AccountDetail> =
      editingId === null ? this.service.createAccount(input) : this.service.updateAccount(editingId, input);
    this.saving.set(true);
    // Applied only while the page still shows the account (or new-account form) that was saved.
    const still = () => this.accountId() === routeId && this.loadedId() === editingId;
    request
      .pipe(
        map(saved => editingId ?? saved.id),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: id => {
          if (!still()) {
            return;
          }
          this.saving.set(false);
          // Saved: nothing is left to discard while the passbook opens.
          this.baseline.set(form);
          this.sharingBaseline.set(this.sharing());
          this.dirty.clearPending();
          this.discardOpener = null;
          this.router.navigate(['/accounting/accounts', id]);
        },
        error: err => {
          if (!still()) {
            return;
          }
          this.saving.set(false);
          const fields = fieldErrors(err);
          this.errors.set(fields);
          if (Object.keys(fields).length === 0) {
            this.message.set(writeErrorMessage(err));
          }
        },
      });
  }

  remove(): void {
    const id = this.loadedId();
    if (id === null || this.loading() || this.loadFailed()) {
      return;
    }
    if (!this.confirmDelete()) {
      this.confirmDelete.set(true);
      return;
    }
    this.confirmDelete.set(false);
    this.service.deleteAccount(id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        if (this.loadedId() === id) {
          this.router.navigate(['/accounting/accounts']);
        }
      },
      error: err =>
        this.loadedId() === id &&
        this.message.set(
          (err as { status?: number })?.status === 409
            ? '此帳戶已有記錄，無法刪除；請改用「封存帳戶」。'
            : writeErrorMessage(err),
        ),
    });
  }

  resetFlag(): void {
    const id = this.loadedId();
    if (id === null || this.loading()) {
      return;
    }
    // Runs inside the navigation sequence (ngOnInit), never as a separate subscription.
    this.resets.next(id);
  }
}
