import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, catchError, of, shareReplay, tap, throwError } from 'rxjs';

import {
  AccountDetail,
  AccountGroup,
  AccountGroupInput,
  AccountInput,
  AccountPeriodSummary,
  BusinessDays,
  AllEntriesQuery,
  BackupImportOptions,
  BalanceAdjustmentInput,
  CategoryInput,
  CategoryNode,
  Counterparty,
  CounterpartyInput,
  DailySummary,
  EntryDetail,
  EntryInput,
  EntryPage,
  EntryQuery,
  FxRateOut,
  ImportRun,
  LedgerAccount,
  MonthSummary,
  Preference,
  Project,
  ProjectInput,
  RefundInput,
  ScheduleCatchUpResult,
  ScheduleDefinition,
  ScheduleDefinitionDetail,
  ScheduleDefinitionInput,
  ScheduleDefinitionKind,
  ScheduleDefinitionUpdate,
  ScheduleInstance,
  ScheduleInstanceQuery,
  ScheduleInstanceUpdate,
  SchedulePostingMode,
  ScheduleRunReport,
  ScheduleStatus,
  SettleInput,
  SplitInput,
  SplitResult,
  TransferInput,
  DebtsSummary,
  ScheduleLoanInput,
  RewardReprice,
  RewardRule,
  RewardUsage,
  RewardRuleInput,
  RewardRuleOverview,
  RewardRuleOverviewQuery,
} from '../models/accounting.model';

/** Query object → params; skips null / undefined / '' and repeats array values (`account_id=1&account_id=2`). */
export function toParams(query: object): HttpParams {
  let params = new HttpParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') {
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        params = params.append(key, String(item));
      }
    } else {
      params = params.set(key, String(value));
    }
  }
  return params;
}

@Injectable({
  providedIn: 'root',
})
export class AccountingService {
  private http = inject(HttpClient);
  private apiUrl = '/api/accounting';
  private readonly changeCount = signal(0);
  private readonly accountChangeCount = signal(0);
  private readonly preferenceChangeCount = signal(0);
  private preference$: Observable<Preference> | null = null;

  /** Bumped after every successful write that adds, changes or removes entries; list pages reload on it. */
  readonly entriesChanged = this.changeCount.asReadonly();

  /** Bumped after every successful account / account-group write and a non-dry-run backup import; account lists reload on it. */
  readonly accountsChanged = this.accountChangeCount.asReadonly();

  /** Bumped after every successful preference save; mounted pages re-read `getPreference()` on it. */
  readonly preferenceChanged = this.preferenceChangeCount.asReadonly();

  /** Bumps `entriesChanged` after a write made through another service (the reconciliation inbox), so list pages reload. */
  markEntriesChanged(): void {
    this.changeCount.update(count => count + 1);
  }

  private bump<T>(source: Observable<T>): Observable<T> {
    return source.pipe(tap(() => this.changeCount.update(count => count + 1)));
  }

  private bumpAccounts<T>(source: Observable<T>): Observable<T> {
    return source.pipe(tap(() => this.accountChangeCount.update(count => count + 1)));
  }

  // ---- accounts ----------------------------------------------------------

  /** `asOf` (`YYYY-MM-DD`) computes balances with `posted_date ≤ asOf`; omitted, the server uses today. */
  getAccounts(includeArchived = false, asOf?: string): Observable<LedgerAccount[]> {
    let params = new HttpParams();
    if (includeArchived) {
      params = params.set('include_archived', 'true');
    }
    if (asOf) {
      params = params.set('as_of', asOf);
    }
    return this.http.get<LedgerAccount[]>(`${this.apiUrl}/accounts`, { params });
  }

  getAccount(id: number, asOf?: string): Observable<AccountDetail> {
    const params = asOf ? new HttpParams().set('as_of', asOf) : undefined;
    return this.http.get<AccountDetail>(`${this.apiUrl}/accounts/${id}`, { params });
  }

  getEntries(accountId: number, query: EntryQuery = {}): Observable<EntryPage> {
    return this.http.get<EntryPage>(`${this.apiUrl}/accounts/${accountId}/entries`, { params: toParams(query) });
  }

  /** Period totals for the passbook header (`spend`, `income`, `rewards`, `net`, `end_balance`), filtered on `posted_date`. */
  getAccountSummary(accountId: number, dateFrom: string, dateTo: string): Observable<AccountPeriodSummary> {
    return this.http.get<AccountPeriodSummary>(`${this.apiUrl}/accounts/${accountId}/summary`, {
      params: { date_from: dateFrom, date_to: dateTo },
    });
  }

  /** Holidays and make-up workdays within [from, to] (`BusinessDaysOut`); weekends are implied. */
  getBusinessDays(from: string, to: string): Observable<BusinessDays> {
    return this.http.get<BusinessDays>(`${this.apiUrl}/calendar/business-days`, { params: { from, to } });
  }

  createAccount(input: AccountInput): Observable<AccountDetail> {
    return this.bumpAccounts(this.http.post<AccountDetail>(`${this.apiUrl}/accounts`, input));
  }

  updateAccount(id: number, input: AccountInput): Observable<AccountDetail> {
    return this.bumpAccounts(this.http.put<AccountDetail>(`${this.apiUrl}/accounts/${id}`, input));
  }

  deleteAccount(id: number): Observable<void> {
    return this.bumpAccounts(this.http.delete<void>(`${this.apiUrl}/accounts/${id}`));
  }

  /** Uploads the account's logo image (raw body); the server normalises it. */
  uploadAccountLogo(id: number, file: Blob): Observable<AccountDetail> {
    return this.bumpAccounts(
      this.http.put<AccountDetail>(`${this.apiUrl}/accounts/${id}/logo`, file, {
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
      }),
    );
  }

  deleteAccountLogo(id: number): Observable<void> {
    return this.bumpAccounts(this.http.delete<void>(`${this.apiUrl}/accounts/${id}/logo`));
  }

  /** Cache-busted URL of the uploaded logo; `hash` is `LedgerAccount.logo_hash`. */
  logoImageUrl(id: number, hash: string): string {
    return `${this.apiUrl}/accounts/${id}/logo.png?v=${encodeURIComponent(hash)}`;
  }

  /** Bumps `accountsChanged` so account lists reload (used by the logo store on an ETag mismatch). */
  refreshAccounts(): void {
    this.accountChangeCount.update(count => count + 1);
  }

  resetSettingsFlag(id: number): Observable<AccountDetail> {
    return this.bumpAccounts(this.http.post<AccountDetail>(`${this.apiUrl}/accounts/${id}/reset-settings-flag`, {}));
  }

  createRewardRule(accountId: number, input: RewardRuleInput): Observable<RewardRule> {
    return this.bumpAccounts(this.http.post<RewardRule>(`${this.apiUrl}/accounts/${accountId}/reward-rules`, input));
  }

  updateRewardRule(accountId: number, ruleId: number, input: RewardRuleInput): Observable<RewardRule> {
    return this.bumpAccounts(
      this.http.put<RewardRule>(`${this.apiUrl}/accounts/${accountId}/reward-rules/${ruleId}`, input),
    );
  }

  reorderRewardRules(accountId: number, ids: number[]): Observable<RewardRule[]> {
    return this.bumpAccounts(this.http.put<RewardRule[]>(`${this.apiUrl}/accounts/${accountId}/reward-rules/order`, { ids }));
  }

  deleteRewardRule(accountId: number, ruleId: number): Observable<void> {
    return this.bumpAccounts(this.http.delete<void>(`${this.apiUrl}/accounts/${accountId}/reward-rules/${ruleId}`));
  }

  getRewardUsage(accountId: number, date?: string): Observable<RewardUsage[]> {
    return this.http.get<RewardUsage[]>(`${this.apiUrl}/accounts/${accountId}/reward-usage`, {
      params: toParams(date ? { date } : {}),
    });
  }

  getRewardRuleOverview(query: RewardRuleOverviewQuery = {}): Observable<RewardRuleOverview> {
    return this.http.get<RewardRuleOverview>(`${this.apiUrl}/reward-rules`, { params: toParams(query) });
  }

  // ---- entries -----------------------------------------------------------

  getAllEntries(query: AllEntriesQuery = {}): Observable<EntryPage> {
    return this.http.get<EntryPage>(`${this.apiUrl}/entries`, { params: toParams(query) });
  }

  getMonthSummary(month: string): Observable<MonthSummary> {
    return this.http.get<MonthSummary>(`${this.apiUrl}/entries/summary`, { params: { month } });
  }

  getDailySummary(month: string): Observable<DailySummary> {
    return this.http.get<DailySummary>(`${this.apiUrl}/entries/summary/daily`, { params: { month } });
  }

  getEntry(id: number): Observable<EntryDetail> {
    return this.http.get<EntryDetail>(`${this.apiUrl}/entries/${id}`);
  }

  createEntry(input: EntryInput): Observable<EntryDetail> {
    return this.bump(this.http.post<EntryDetail>(`${this.apiUrl}/entries`, input));
  }

  updateEntry(id: number, input: EntryInput): Observable<EntryDetail> {
    return this.bump(this.http.put<EntryDetail>(`${this.apiUrl}/entries/${id}`, input));
  }

  /** Replace only the entry's reward rule links; the row and its fee / discount children stay as they are (#293). */
  updateEntryRewardRules(id: number, ruleIds: number[]): Observable<EntryDetail> {
    return this.bump(this.http.put<EntryDetail>(`${this.apiUrl}/entries/${id}/reward-rules`, { reward_rule_ids: ruleIds }));
  }

  /** A purchase's posted rewards: booked vs repriced on its current figures (#306). */
  getRewardReprice(id: number): Observable<RewardReprice> {
    return this.http.get<RewardReprice>(`${this.apiUrl}/entries/${id}/reward-reprice`);
  }

  /** 依新金額重算回饋 (`keep` false) or 保持原樣 (`keep` true); either clears the `reward_posted` flag (#306). */
  applyRewardReprice(id: number, keep: boolean): Observable<RewardReprice> {
    return this.bump(this.http.post<RewardReprice>(`${this.apiUrl}/entries/${id}/reward-reprice`, { keep }));
  }

  deleteEntry(id: number): Observable<void> {
    return this.bump(this.http.delete<void>(`${this.apiUrl}/entries/${id}`));
  }

  settleEntry(id: number, input: SettleInput): Observable<EntryDetail> {
    return this.bump(this.http.post<EntryDetail>(`${this.apiUrl}/entries/${id}/settle`, input));
  }

  refundEntry(id: number, input: RefundInput): Observable<EntryDetail> {
    return this.bump(this.http.post<EntryDetail>(`${this.apiUrl}/entries/${id}/refund`, input));
  }

  createBalanceAdjustment(input: BalanceAdjustmentInput): Observable<EntryDetail> {
    return this.bump(this.http.post<EntryDetail>(`${this.apiUrl}/balance-adjustments`, input));
  }

  createTransfer(input: TransferInput): Observable<{ transfer_group_id: string; out_entry_id: number; in_entry_id: number }> {
    return this.bump(
      this.http.post<{ transfer_group_id: string; out_entry_id: number; in_entry_id: number }>(`${this.apiUrl}/transfers`, input),
    );
  }

  updateTransfer(groupId: string, input: TransferInput): Observable<unknown> {
    return this.bump(this.http.put<unknown>(`${this.apiUrl}/transfers/${groupId}`, input));
  }

  createSplit(input: SplitInput): Observable<SplitResult> {
    return this.bump(this.http.post<SplitResult>(`${this.apiUrl}/splits`, input));
  }

  /** Upserts the members by id (keep / full / new / dropped); one member dissolves the split. */
  updateSplit(groupId: number, input: SplitInput): Observable<SplitResult> {
    return this.bump(this.http.put<SplitResult>(`${this.apiUrl}/splits/${groupId}`, input));
  }

  /** Converts an ungrouped entry into a split; the entry is the anchor and keeps its id. */
  convertEntryToSplit(entryId: number, input: SplitInput): Observable<SplitResult> {
    return this.bump(this.http.put<SplitResult>(`${this.apiUrl}/entries/${entryId}/split`, input));
  }

  deleteSplit(groupId: number): Observable<void> {
    return this.bump(this.http.delete<void>(`${this.apiUrl}/splits/${groupId}`));
  }

  // ---- settings ----------------------------------------------------------

  getDebts(): Observable<DebtsSummary> {
    return this.http.get<DebtsSummary>(`${this.apiUrl}/accounts/debts`);
  }

  getAccountGroups(): Observable<AccountGroup[]> {
    return this.http.get<AccountGroup[]>(`${this.apiUrl}/account-groups`);
  }

  createAccountGroup(input: AccountGroupInput): Observable<AccountGroup> {
    return this.bumpAccounts(this.http.post<AccountGroup>(`${this.apiUrl}/account-groups`, input));
  }

  updateAccountGroup(id: number, input: AccountGroupInput): Observable<AccountGroup> {
    return this.bumpAccounts(this.http.put<AccountGroup>(`${this.apiUrl}/account-groups/${id}`, input));
  }

  deleteAccountGroup(id: number): Observable<void> {
    return this.bumpAccounts(this.http.delete<void>(`${this.apiUrl}/account-groups/${id}`));
  }

  reorderAccountGroups(ids: number[]): Observable<unknown> {
    return this.bumpAccounts(this.http.put<unknown>(`${this.apiUrl}/account-groups/order`, { ids }));
  }

  getCategories(kind: string): Observable<CategoryNode[]> {
    return this.http.get<CategoryNode[]>(`${this.apiUrl}/categories`, { params: { kind } });
  }

  createCategory(input: CategoryInput): Observable<CategoryNode> {
    return this.http.post<CategoryNode>(`${this.apiUrl}/categories`, input);
  }

  updateCategory(id: number, input: CategoryInput): Observable<CategoryNode> {
    return this.http.put<CategoryNode>(`${this.apiUrl}/categories/${id}`, input);
  }

  deleteCategory(id: number): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/categories/${id}`);
  }

  reorderCategories(ids: number[]): Observable<unknown> {
    return this.http.put<unknown>(`${this.apiUrl}/categories/order`, { ids });
  }

  getProjects(): Observable<Project[]> {
    return this.http.get<Project[]>(`${this.apiUrl}/projects`);
  }

  createProject(input: ProjectInput): Observable<Project> {
    return this.http.post<Project>(`${this.apiUrl}/projects`, input);
  }

  updateProject(id: number, input: ProjectInput): Observable<Project> {
    return this.http.put<Project>(`${this.apiUrl}/projects/${id}`, input);
  }

  deleteProject(id: number): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/projects/${id}`);
  }

  getCounterparties(): Observable<Counterparty[]> {
    return this.http.get<Counterparty[]>(`${this.apiUrl}/counterparties`);
  }

  createCounterparty(input: CounterpartyInput): Observable<Counterparty> {
    return this.http.post<Counterparty>(`${this.apiUrl}/counterparties`, input);
  }

  updateCounterparty(id: number, input: CounterpartyInput): Observable<Counterparty> {
    return this.bump(this.http.put<Counterparty>(`${this.apiUrl}/counterparties/${id}`, input));
  }

  deleteCounterparty(id: number): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/counterparties/${id}`);
  }

  /** Read once per session (design D27); a failed read is retried on the next call. */
  getPreference(): Observable<Preference> {
    if (!this.preference$) {
      this.preference$ = this.http.get<Preference>(`${this.apiUrl}/preference`).pipe(
        catchError((error: unknown) => {
          this.preference$ = null;
          return throwError(() => error);
        }),
        shareReplay(1),
      );
    }
    return this.preference$;
  }

  updatePreference(input: Preference): Observable<Preference> {
    return this.http.put<Preference>(`${this.apiUrl}/preference`, input).pipe(
      tap(saved => {
        this.preference$ = of(saved);
        this.preferenceChangeCount.update(count => count + 1);
      }),
    );
  }

  getFxRate(date: string, base: string, quote: string): Observable<FxRateOut> {
    return this.http.get<FxRateOut>(`${this.apiUrl}/fx-rate`, { params: { date, base, quote } });
  }

  // ---- schedules (週期 / 分期 / 待完成交易) ------------------------------------------------------------------
  // Every write bumps entriesChanged: posting writes entries, and the reminder centre and the 🔔 reload on it.

  getScheduleDefinitions(query: { status?: ScheduleStatus; kind?: ScheduleDefinitionKind } = {}): Observable<ScheduleDefinition[]> {
    return this.http.get<ScheduleDefinition[]>(`${this.apiUrl}/schedules/definitions`, { params: toParams(query) });
  }

  getScheduleDefinition(id: number): Observable<ScheduleDefinitionDetail> {
    return this.http.get<ScheduleDefinitionDetail>(`${this.apiUrl}/schedules/definitions/${id}`);
  }

  createScheduleDefinition(input: ScheduleDefinitionInput): Observable<ScheduleDefinition> {
    return this.bump(this.http.post<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions`, input));
  }

  updateScheduleDefinition(id: number, input: ScheduleDefinitionUpdate): Observable<ScheduleDefinition> {
    return this.bump(this.http.put<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions/${id}`, input));
  }

  attachScheduleLoan(id: number, input: ScheduleLoanInput): Observable<ScheduleDefinitionDetail> {
    return this.bump(this.http.post<ScheduleDefinitionDetail>(`${this.apiUrl}/schedules/definitions/${id}/loan`, input));
  }

  deleteScheduleDefinition(id: number): Observable<void> {
    return this.bump(this.http.delete<void>(`${this.apiUrl}/schedules/definitions/${id}`));
  }

  pauseSchedule(id: number): Observable<ScheduleDefinition> {
    return this.bump(this.http.post<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions/${id}/pause`, {}));
  }

  resumeSchedule(id: number, backlog: 'skip' | 'post' = 'skip'): Observable<ScheduleDefinition> {
    return this.bump(this.http.post<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions/${id}/resume`, { backlog }));
  }

  endSchedule(id: number): Observable<ScheduleDefinition> {
    return this.bump(this.http.post<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions/${id}/end`, {}));
  }

  setScheduleMode(id: number, mode: SchedulePostingMode): Observable<ScheduleDefinition> {
    return this.bump(this.http.put<ScheduleDefinition>(`${this.apiUrl}/schedules/definitions/${id}/mode`, { posting_mode: mode }));
  }

  catchUpSchedule(id: number): Observable<ScheduleCatchUpResult> {
    return this.bump(this.http.post<ScheduleCatchUpResult>(`${this.apiUrl}/schedules/definitions/${id}/catch-up`, {}));
  }

  getScheduleInstances(query: ScheduleInstanceQuery = {}): Observable<ScheduleInstance[]> {
    return this.http.get<ScheduleInstance[]>(`${this.apiUrl}/schedules/instances`, { params: toParams(query) });
  }

  updateScheduleInstance(id: number, body: ScheduleInstanceUpdate): Observable<ScheduleInstance> {
    return this.bump(this.http.put<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}`, body));
  }

  postScheduleInstance(id: number): Observable<ScheduleInstance> {
    return this.bump(this.http.post<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}/post`, {}));
  }

  skipScheduleInstance(id: number): Observable<ScheduleInstance> {
    return this.bump(this.http.post<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}/skip`, {}));
  }

  reopenScheduleInstance(id: number): Observable<ScheduleInstance> {
    return this.bump(this.http.post<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}/reopen`, {}));
  }

  repostScheduleInstance(id: number, amounts: string[]): Observable<ScheduleInstance> {
    return this.bump(this.http.post<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}/repost`, { amounts }));
  }

  acceptPartialScheduleInstance(id: number): Observable<ScheduleInstance> {
    return this.bump(this.http.post<ScheduleInstance>(`${this.apiUrl}/schedules/instances/${id}/accept-partial`, {}));
  }

  runSchedulesNow(): Observable<ScheduleRunReport> {
    return this.bump(this.http.post<ScheduleRunReport>(`${this.apiUrl}/schedules/run-now`, {}));
  }


  // ---- imports -----------------------------------------------------------

  /** Upload a MOZE backup zip. Defaults to a strict dry run; pass `dryRun: false` to import. */
  importBackup(file: File, options: BackupImportOptions = {}): Observable<ImportRun> {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('renames', options.renames ?? '');
    let params = new HttpParams()
      .set('dry_run', String(options.dryRun ?? true))
      .set('strict', String(options.strict ?? true));
    if (options.allowFxOutliers) {
      params = params.set('allow_fx_outliers', 'true');
    }
    return this.http.post<ImportRun>(`${this.apiUrl}/imports/moze-backup`, form, { params }).pipe(
      tap(run => {
        if (run.status === 'succeeded') {
          this.changeCount.update(count => count + 1);
          this.accountChangeCount.update(count => count + 1);
        }
      }),
    );
  }

  /** Latest import run, or null when no import has run yet (HTTP 404). */
  getLatestImport(): Observable<ImportRun | null> {
    return this.http.get<ImportRun>(`${this.apiUrl}/imports/latest`).pipe(
      catchError((error: HttpErrorResponse) =>
        error.status === 404 ? of(null) : throwError(() => error),
      ),
    );
  }
}
