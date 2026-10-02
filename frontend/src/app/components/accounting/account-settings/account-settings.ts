import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, Observable, Subject, catchError, map, merge, of, switchMap, tap } from 'rxjs';

import {
  AccountDetail,
  AccountGroup,
  AccountInput,
  LedgerAccount,
  RewardRule,
  RoundingMode,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { DueRule, dueDate, periodLabel, statementPeriod } from '../cycle';
import { shortDate, todayIso } from '../dates';
import { formatMoney } from '../format';
import { fieldErrors, writeErrorMessage } from '../http-errors';

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
  name: '', currency: 'TWD', opening_balance: '0', group_id: null, icon: '', color: '', note: '', sort_order: 0,
  include_in_total: true, is_archived: false, is_credit: false, closing_day: '', due_rule: '', due_value: '',
  credit_limit: '', combined_account_id: null, credit_sharing_id: null, auto_pay_account_id: null,
  fx_fee_on: false, fx_fee_pct: '', fx_fee_rounding: 'keep', fx_fee_refundable: false,
};

function decimalText(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') {
    return '';
  }
  return String(Number(value));
}

function intOrNull(value: string): number | null {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : null;
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

interface LoadedAccount {
  loadId: number;
  id: number | null;
  detail: AccountDetail | null;
}

@Component({
  selector: 'app-account-settings',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './account-settings.html',
  styleUrl: './account-settings.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountSettingsComponent implements OnInit {
  private service = inject(AccountingService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  private destroyRef = inject(DestroyRef);
  private loadId = 0;
  /** resetFlag() requests; merged into the navigation sequence so a navigation cancels them. */
  private readonly resets = new Subject<number>();

  /** From `paramMap` (observable): at ≥ 760 px the pane reuses this component when only `:id` changes (Task 21). */
  readonly accountId = signal<number | null>(null);
  /** Id of the account whose response is applied to the form; writes target this id only. */
  readonly loadedId = signal<number | null>(null);
  readonly loading = signal(false);
  readonly today = todayIso();

  readonly detail = signal<AccountDetail | null>(null);
  readonly accounts = signal<LedgerAccount[]>([]);
  readonly groups = signal<AccountGroup[]>([]);
  readonly form = signal<AccountForm>(EMPTY_FORM);
  readonly sharing = signal<number[]>([]);
  readonly errors = signal<Record<string, string>>({});
  readonly message = signal<string | null>(null);
  readonly saving = signal(false);
  readonly confirmDelete = signal(false);

  readonly roundingOptions = Object.entries(ROUNDING_LABELS) as [RoundingMode, string][];
  readonly formatMoney = formatMoney;

  readonly currencyLocked = computed(() => (this.detail()?.entry_count ?? 0) > 0);
  readonly closingDay = computed(() => intOrNull(this.form().closing_day));
  readonly period = computed(() => statementPeriod(this.closingDay(), this.today));
  readonly cycleLabel = computed(() => (this.closingDay() === null ? '每月 1 日 – 月底' : periodLabel(this.period())));
  readonly dueLabel = computed(() => {
    const form = this.form();
    const value = intOrNull(form.due_value);
    if (!form.due_rule || value === null) {
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

  accountLabel(account: LedgerAccount): string {
    return account.is_archived ? `${account.name}（已封存）` : account.name;
  }
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
  readonly rules = computed<RewardRule[]>(() => this.detail()?.reward_rules ?? []);

  ngOnInit(): void {
    const navigations = this.route.paramMap.pipe(
      map(params => {
        const idParam = params.get('id');
        return idParam === null ? null : Number(idParam);
      }),
      tap(id => {
        this.accountId.set(id);
        this.loadedId.set(null);
        this.detail.set(null);
        this.form.set(EMPTY_FORM);
        this.sharing.set([]);
        this.errors.set({});
        this.message.set(null);
        this.confirmDelete.set(false);
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
    this.form.set(loaded.detail ? formFrom(loaded.detail) : EMPTY_FORM);
    this.sharing.set((loaded.detail?.credit_sharing_members ?? []).filter(id => id !== loaded.id));
  }

  set<K extends keyof AccountForm>(key: K, value: AccountForm[K]): void {
    this.form.update(form => ({ ...form, [key]: value }));
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
    return this.errors()[field] ?? null;
  }

  ruleLabel(rule: RewardRule): string {
    const value = rule.method === 'percent' ? `${Number(rule.rate)}%` : formatMoney(rule.fixed_amount, this.form().currency);
    return `${rule.name} ${value}${rule.is_enabled ? '' : '（停用）'}`;
  }

  save(): void {
    if (this.loading() || this.saving()) {
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
    const input = formToInput(form, this.sharing());
    const request: Observable<AccountDetail> =
      editingId === null ? this.service.createAccount(input) : this.service.updateAccount(editingId, input);
    this.saving.set(true);
    request
      .pipe(map(saved => editingId ?? saved.id))
      .subscribe({
        next: id => {
          this.saving.set(false);
          this.router.navigate(['/accounting/accounts', id]);
        },
        error: err => {
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
    if (id === null || this.loading()) {
      return;
    }
    if (!this.confirmDelete()) {
      this.confirmDelete.set(true);
      return;
    }
    this.confirmDelete.set(false);
    this.service.deleteAccount(id).subscribe({
      next: () => this.router.navigate(['/accounting/accounts']),
      error: err =>
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
