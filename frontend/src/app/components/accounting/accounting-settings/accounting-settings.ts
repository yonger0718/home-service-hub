import { ChangeDetectionStrategy, Component, ElementRef, OnDestroy, OnInit, afterNextRender, computed, effect, inject, signal, untracked, Injector } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Router, RouterLink } from '@angular/router';
import { Observable, Subject, catchError, concatMap, finalize, forkJoin, map, of, throwError } from 'rxjs';

import {
  AccountGroup,
  CategoryNode,
  Counterparty,
  ENTRY_KIND_LABELS,
  EntryKind,
  ImportRun,
  LedgerAccount,
  Preference,
  Project,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { injectToasts } from '../../../services/toast.service';
import { AutoPaySwitch, AutoPaySwitchChange } from '../billing/auto-pay';
import { InboxService } from '../inbox/inbox.service';
import { EINVOICE_SETTINGS_FRAGMENT, einvoiceErrorText, invoiceAmount } from '../inbox/einvoice-format';
import { EinvoiceDismissed, EinvoiceMute, EinvoicePreferences } from '../inbox/einvoice.model';
import { StepperComponent } from '../../ui/stepper/stepper';
import { EinvoiceService } from '../inbox/einvoice.service';
import { OwnerRule } from '../inbox/inbox.model';
import { isHandledKey } from '../accounting-ui';
import { shortDate, slashDate, todayIso } from '../dates';
import { formatMoney } from '../format';
import { writeErrorMessage } from '../http-errors';
import { ReportView, summarizeReport } from './import-report';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { SwitchComponent } from '../../ui/switch/switch';

export type CategoryTab = 'expense' | 'income' | 'transfer_out' | 'receivable' | 'payable';

export const CATEGORY_TABS: [CategoryTab, string][] = [
  ['expense', '支出'],
  ['income', '收入'],
  ['transfer_out', '轉帳'],
  ['receivable', '應收'],
  ['payable', '應付'],
];

const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];

function swap<T>(list: T[], index: number, delta: number): T[] | null {
  const target = index + delta;
  if (target < 0 || target >= list.length) {
    return null;
  }
  const copy = [...list];
  [copy[index], copy[target]] = [copy[target], copy[index]];
  return copy;
}

type EinvoicePrefField = 'date_window_days' | 'mute_prompt_after';

/** The API's bounds (`EinvoicePreferencesIn`). */
const PREF_BOUNDS: Record<EinvoicePrefField, [number, number]> = { date_window_days: [0, 7], mute_prompt_after: [1, 10] };
const DISMISSED_CAP = 100;
const DISMISSED_SHOWN = 3;

/** A server timestamp's local calendar day as `MM/DD`. */
function localDay(iso: string): string {
  return shortDate(todayIso(new Date(iso)));
}

@Component({
  selector: 'app-accounting-settings',
  standalone: true,
  imports: [PageHeaderComponent, RouterLink, SwitchComponent, StepperComponent, DatePipe, DecimalPipe],
  templateUrl: './accounting-settings.html',
  styleUrl: './accounting-settings.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(document:click)': 'onDocumentClick($event)' },
})
export class AccountingSettingsComponent implements OnInit, OnDestroy {
  private service = inject(AccountingService);
  private readonly router = inject(Router);
  private readonly inbox = inject(InboxService);
  private readonly einvoice = inject(EinvoiceService);

  readonly preference = signal<Preference | null>(null);
  readonly groups = signal<AccountGroup[]>([]);
  readonly categoryKind = signal<CategoryTab>('expense');
  readonly categories = signal<CategoryNode[]>([]);
  readonly projects = signal<Project[]>([]);
  readonly counterparties = signal<Counterparty[]>([]);
  readonly cpFilter = signal('');
  readonly rules = signal<OwnerRule[]>([]);
  private readonly accounts = signal<LedgerAccount[]>([]);
  /** id → name of every expense and income category, for the rule rows; independent of the 類別 tab. */
  private readonly ruleCategories = signal<Map<number, string>>(new Map());
  /** Rule ids whose DELETE is in flight; their 🗑 is disabled. */
  readonly deletingRules = signal<ReadonlySet<number>>(new Set());
  private ruleRequest = 0;
  readonly latest = signal<ImportRun | null>(null);
  readonly dataMessage = signal<string | null>(null);
  /** Row whose 刪除 was tapped once (`group:1`, `cat:11`, `project:4`, `cp:5`); one row at a time. */
  readonly confirming = signal<string | null>(null);

  readonly newGroup = signal('');
  readonly newCategory = signal('');
  /** Typed text of each main category's 新增子類別 field; Partial, as most rows have none yet. */
  readonly newSub = signal<Partial<Record<number, string>>>({});
  readonly newProject = signal('');
  readonly newCounterparty = signal('');

  readonly file = signal<File | null>(null);
  readonly dryRunFile = signal<File | null>(null);
  readonly report = signal<ReportView | null>(null);
  readonly reportMode = signal<'dry' | 'real' | null>(null);
  readonly importing = signal(false);
  readonly importError = signal<string | null>(null);

  readonly categoryTabs = CATEGORY_TABS;
  readonly weekdays = WEEKDAYS;
  readonly formatMoney = formatMoney;
  readonly slashDate = slashDate;
  readonly invoiceAmount = invoiceAmount;
  readonly einvoiceAnchor = EINVOICE_SETTINGS_FRAGMENT;

  /** 對帳 › 自動扣繳: the stored switch and start date; null until read (and when reconciliation is off: 404). */
  readonly autoPay = signal<AutoPaySwitch | null>(null);
  readonly autoPayError = signal(false);
  readonly autoPaySaving = signal(false);
  readonly autoPayMessage = signal<string | null>(null);
  /** Switched on while no start date is stored: nothing is saved until a date is chosen. */
  readonly autoPayDraftOn = signal(false);
  readonly autoPayOn = computed(() => !!this.autoPay()?.enabled || this.autoPayDraftOn());
  readonly autoPayNeedsDate = computed(() => this.autoPayDraftOn() && !this.autoPay()?.enabled_from);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);

  /** 發票 block: null until read; the block shows only when `enabled`. */
  readonly einvoicePrefs = signal<EinvoicePreferences | null>(null);
  readonly einvoiceError = signal(false);
  readonly mutes = signal<EinvoiceMute[] | null>(null);
  readonly mutesError = signal(false);
  readonly dismissed = signal<EinvoiceDismissed[] | null>(null);
  readonly dismissedError = signal(false);
  readonly einvoiceMessage = signal<string | null>(null);
  /** Stepper values shown while their PATCH is pending (D12); cleared once the server answers with the last one. */
  readonly prefDraft = signal<Partial<Record<EinvoicePrefField, number>>>({});
  private readonly prefInFlight = new Set<EinvoicePrefField>();
  /** A stepper whose PATCH failed: the value it tried, for 重試; shown right under that row. */
  readonly prefFailed = signal<Partial<Record<EinvoicePrefField, number>>>({});
  private readonly toasts = injectToasts();
  /** The API lists the newest 100 dismissed invoices. */
  readonly dismissedCap = DISMISSED_CAP;
  /** 已標成不用記帳: newest three until 看全部 is tapped. */
  readonly dismissedAll = signal(false);
  readonly shownDismissed = computed(() => {
    const list = this.dismissed() ?? [];
    return this.dismissedAll() ? list : list.slice(0, DISMISSED_SHOWN);
  });
  readonly einvoiceStatus = computed(() => {
    const prefs = this.einvoicePrefs();
    if (!prefs) {
      return '';
    }
    const last = prefs.last_import_at;
    return last ? `最近匯入 ${localDay(last)} · 已自動連結 ${prefs.auto_linked_count} 張` : '還沒有匯入';
  });

  readonly visibleCounterparties = computed(() => {
    const filter = this.cpFilter().trim().toLowerCase();
    return filter ? this.counterparties().filter(cp => cp.name.toLowerCase().includes(filter)) : this.counterparties();
  });
  readonly subCount = computed(() => this.categories().reduce((sum, node) => sum + (node.children?.length ?? 0), 0));
  readonly canImport = computed(() => !!this.file() && this.file() === this.dryRunFile() && !this.importing());

  /** The preference as last confirmed by the server; a failed save reverts to it. */
  private savedPreference: Preference | null = null;
  /** Saves not answered yet; the server's answer is applied only once none is left (no flicker between toggles). */
  private pendingPreferenceSaves = 0;
  private readonly preferenceSaves = new Subject<Preference>();

  constructor() {
    // Reads `changed` so every inbox write (including this block's own toggle and delete) re-reads the rules.
    effect(() => {
      this.inbox.changed();
      untracked(() => this.loadRules());
    });
    // One PUT at a time, in order: each body is the optimistic state at its toggle, so quick toggles all persist.
    // Not tied to the page's lifetime: leaving the page completes the subject (ngOnDestroy), so the PUT in flight
    // and the queued ones still run, and the subscription ends after the last one.
    this.preferenceSaves
      .pipe(
        concatMap(next =>
          this.service.updatePreference(next).pipe(
            map(saved => ({ saved, error: null as unknown })),
            catchError((error: unknown) => of({ saved: null, error })),
          ),
        ),
      )
      .subscribe(({ saved, error }) => {
        this.pendingPreferenceSaves--;
        if (saved) {
          this.savedPreference = saved;
          if (this.pendingPreferenceSaves === 0) {
            this.preference.set(saved);
          }
          return;
        }
        this.preference.set(this.savedPreference);
        this.dataMessage.set(writeErrorMessage(error));
      });
  }

  ngOnDestroy(): void {
    this.preferenceSaves.complete();
  }

  ngOnInit(): void {
    this.service.getPreference().subscribe(preference => {
      this.savedPreference = preference;
      this.preference.set(preference);
    });
    this.loadGroups();
    this.service.getAccounts(true).subscribe(accounts => this.accounts.set(accounts));
    this.loadCategories();
    forkJoin([this.service.getCategories('expense'), this.service.getCategories('income')]).subscribe(([expense, income]) => {
      const names = new Map<number, string>();
      const add = (nodes: CategoryNode[]): void => {
        for (const node of nodes) {
          names.set(node.id, node.name);
          add(node.children ?? []);
        }
      };
      add(expense);
      add(income);
      this.ruleCategories.set(names);
    });
    this.loadProjects();
    this.loadCounterparties();
    this.loadImportState();
    this.loadAutoPay();
    this.loadEinvoice();
  }

  private loadAutoPay(): void {
    this.service.getAutoPaySwitch().subscribe({
      next: autoPay => this.autoPay.set(autoPay),
      // 404: the reconciliation feature flag is off, so there is no 對帳 block (not a failed read).
      error: (err: unknown) => this.autoPayError.set(!(err instanceof HttpErrorResponse && err.status === 404)),
    });
  }

  /** On with a stored start date saves at once; on without one waits for the date (focus moves there); off saves. */
  toggleAutoPay(): void {
    const stored = this.autoPay();
    if (!stored || this.autoPaySaving()) return;
    this.autoPayMessage.set(null);
    if (this.autoPayDraftOn()) {
      this.autoPayDraftOn.set(false);
    } else if (stored.enabled) {
      this.saveAutoPay({ enabled: false });
    } else if (stored.enabled_from) {
      this.saveAutoPay({ enabled: true });
    } else {
      this.autoPayDraftOn.set(true);
      afterNextRender(() => this.host.nativeElement.querySelector<HTMLInputElement>('.autopay-from')?.focus(), { injector: this.injector });
    }
  }

  setAutoPayFrom(input: HTMLInputElement): void {
    const stored = this.autoPay();
    if (!stored || this.autoPaySaving()) return;
    this.autoPayMessage.set(null);
    const value = input.value || null;
    if (value === stored.enabled_from && !this.autoPayDraftOn()) {
      return; // the stored date again: nothing to save
    }
    if (value === null && stored.enabled) {
      input.value = stored.enabled_from ?? '';
      this.autoPayMessage.set('自動扣繳開啟時需要起始日');
      return;
    }
    this.saveAutoPay(this.autoPayDraftOn() && value ? { enabled: true, enabled_from: value } : { enabled_from: value }, input);
  }

  /** `input`: the date field to put back to the stored date when the save fails. */
  private saveAutoPay(change: AutoPaySwitchChange, input?: HTMLInputElement): void {
    this.autoPaySaving.set(true);
    this.service.setAutoPay(change).subscribe({
      next: saved => {
        this.autoPay.set({ ...this.autoPay(), ...saved });
        this.autoPayDraftOn.set(false);
        this.autoPaySaving.set(false);
      },
      error: () => {
        // The stored state stays (the [value] binding did not change, so the field is put back by hand).
        if (input) input.value = this.autoPay()?.enabled_from ?? '';
        this.autoPaySaving.set(false);
        this.autoPayMessage.set('自動扣繳設定儲存失敗，請稍後再試');
      },
    });
  }

  private loadEinvoice(): void {
    this.einvoice.preferences().subscribe({
      next: prefs => {
        this.einvoicePrefs.set(prefs);
        if (prefs.enabled) {
          this.loadMutes();
          this.loadDismissed();
          this.scrollToEinvoice();
        }
      },
      // 404: the reconciliation feature flag is off, so there is no 發票 block (not a failed read).
      error: (err: unknown) => this.einvoiceError.set(!(err instanceof HttpErrorResponse && err.status === 404)),
    });
  }

  /** A link to `#einvoice-settings` (有發票沒有帳's footer) lands on the section once it renders. */
  private scrollToEinvoice(): void {
    if (this.router.parseUrl(this.router.url).fragment !== EINVOICE_SETTINGS_FRAGMENT) {
      return;
    }
    afterNextRender(() => this.host.nativeElement.querySelector(`#${EINVOICE_SETTINGS_FRAGMENT}`)?.scrollIntoView?.({ block: 'start' }), {
      injector: this.injector,
    });
  }

  private loadMutes(): void {
    this.einvoice.mutes().subscribe({
      next: list => {
        this.mutes.set(list);
        this.mutesError.set(false);
      },
      error: () => this.mutesError.set(true),
    });
  }

  private loadDismissed(): void {
    this.einvoice.dismissed().subscribe({
      next: list => {
        this.dismissed.set(list);
        this.dismissedError.set(false);
      },
      error: () => this.dismissedError.set(true),
    });
  }

  toggleRemindUnmatched(prefs: EinvoicePreferences): void {
    this.einvoiceMessage.set(null);
    this.einvoice.patchPreferences({ remind_unmatched: !prefs.remind_unmatched }).subscribe({
      next: (saved: EinvoicePreferences) => this.mergePrefs(saved, 'remind_unmatched'),
      error: (err: unknown) => this.einvoiceMessage.set(einvoiceErrorText(err)),
    });
  }

  prefValue(prefs: EinvoicePreferences, field: EinvoicePrefField): number {
    return this.prefDraft()[field] ?? prefs[field];
  }

  /** A stepper change: shown at once; one PATCH per field in flight, re-sent with the last value when it returns. */
  setPref(field: EinvoicePrefField, value: number | null): void {
    const [min, max] = PREF_BOUNDS[field];
    if (value === null || !Number.isInteger(value) || value < min || value > max) {
      return;
    }
    this.prefDraft.update(draft => ({ ...draft, [field]: value }));
    this.clearFailed(field);
    if (!this.prefInFlight.has(field)) {
      this.sendPref(field);
    }
  }

  private sendPref(field: EinvoicePrefField): void {
    const value = this.prefDraft()[field];
    const prefs = this.einvoicePrefs();
    if (value === undefined || !prefs) {
      return;
    }
    if (value === prefs[field]) {
      this.clearDraft(field);
      return;
    }
    this.prefInFlight.add(field);
    this.einvoiceMessage.set(null);
    this.einvoice.patchPreferences({ [field]: value }).subscribe({
      next: (saved: EinvoicePreferences) => {
        this.prefInFlight.delete(field);
        this.mergePrefs(saved, field);
        if (this.prefDraft()[field] === saved[field]) {
          this.clearDraft(field);
        } else {
          this.sendPref(field);
        }
      },
      error: () => {
        // The shown value goes back to the server's; the row says so and 重試 sends the newest value it tried.
        const tried = this.prefDraft()[field] ?? value;
        this.prefInFlight.delete(field);
        this.clearDraft(field);
        this.prefFailed.update(failed => ({ ...failed, [field]: tried }));
      },
    });
  }

  /**
   * Takes the saved field only, so one field's late answer cannot undo another's. The read-only import status is not
   * versioned by `version`; it is taken from an answer no older than what is shown, so a late answer cannot roll it back.
   */
  private mergePrefs(saved: EinvoicePreferences, field: EinvoicePrefField | 'remind_unmatched'): void {
    this.einvoicePrefs.update(current => {
      if (!current) return current;
      const newest = saved.version >= current.version;
      return {
        ...current,
        [field]: saved[field],
        ...(newest ? { last_import_at: saved.last_import_at, auto_linked_count: saved.auto_linked_count } : {}),
        version: Math.max(current.version, saved.version),
      };
    });
  }

  /** `沒存到，已改回 ±3 天` / `沒存到，已改回 3 次`: the value now shown. */
  prefFailedText(prefs: EinvoicePreferences, field: EinvoicePrefField): string {
    return field === 'date_window_days' ? `沒存到，已改回 ±${prefs[field]} 天` : `沒存到，已改回 ${prefs[field]} 次`;
  }

  retryPref(field: EinvoicePrefField): void {
    const tried = this.prefFailed()[field];
    if (tried !== undefined) {
      this.setPref(field, tried);
    }
  }

  private clearFailed(field: EinvoicePrefField): void {
    this.prefFailed.update(failed => {
      const next = { ...failed };
      delete next[field];
      return next;
    });
  }

  private clearDraft(field: EinvoicePrefField): void {
    this.prefDraft.update(draft => {
      const next = { ...draft };
      delete next[field];
      return next;
    });
  }

  /** `不提醒的商家 · 2`; the bare label while the list is loading or failed. */
  countLabel(label: string, list: readonly unknown[] | null): string {
    return list ? `${label} · ${list.length}` : label;
  }

  muteMeta(mute: EinvoiceMute): string {
    const since = `${localDay(mute.created_at)} 起不提醒`;
    return mute.seller_name ? `統編 ${mute.seller_tax_id} · ${since}` : since;
  }

  dismissedMeta(invoice: EinvoiceDismissed): string {
    const items = invoice.items.slice(0, 2).map(item => item.name).join('、');
    const who = invoice.dismiss_reason === 'runbook' ? '規則自動標記' : '你標的';
    return [shortDate(invoice.issue_date), items, who].filter(Boolean).join(' · ');
  }

  /** The mute row's title: the seller name, else `統編 X`. */
  muteName(mute: EinvoiceMute): string {
    return mute.seller_name || `統編 ${mute.seller_tax_id}`;
  }

  /** 恢復提醒: one tap; the toast's 復原 mutes the seller again (as F2/F3). */
  unmute(mute: EinvoiceMute): void {
    const name = this.muteName(mute);
    this.einvoiceMessage.set(null);
    this.einvoice.deleteMute(mute.seller_tax_id).subscribe({
      next: () => {
        this.loadMutes();
        this.toasts.done(`已恢復提醒：${name}`, { undo: () => this.undo(this.einvoice.createMute(mute.seller_tax_id), `已復原：${name} 不提醒`, () => this.loadMutes()) });
      },
      error: err => this.einvoiceMessage.set(einvoiceErrorText(err)),
    });
  }

  /** 放回: back to 沒對到 (one tap); the toast's 復原 marks it 不用記帳 again. */
  restoreInvoice(invoice: EinvoiceDismissed): void {
    const what = `${invoice.seller_name} ${invoiceAmount(invoice.total_amount)}`;
    this.einvoiceMessage.set(null);
    this.einvoice.undismiss(invoice.id).subscribe({
      next: () => {
        this.loadDismissed();
        this.toasts.done(`已放回：${what}`, { undo: () => this.undo(this.einvoice.dismiss(invoice.id, false, { ifUnmatched: true }), `已復原：${what}`, () => this.loadDismissed()) });
      },
      error: err => this.einvoiceMessage.set(einvoiceErrorText(err)),
    });
  }

  private undo(call: Observable<unknown>, done: string, reload: () => void): void {
    call.subscribe({
      next: () => {
        reload();
        this.toasts.done(done);
      },
      error: (err: unknown) => this.toasts.error(`復原沒成功：${einvoiceErrorText(err).replace(/^沒成功[：，]/, '')}`),
    });
  }

  private loadGroups(): void {
    this.service.getAccountGroups().subscribe(groups => this.groups.set(groups));
  }

  private categoryRequest = 0;

  /** Only the response for the latest request is applied, so a quick tab switch never shows another kind's tree. */
  private loadCategories(): void {
    const request = ++this.categoryRequest;
    this.service.getCategories(this.categoryKind()).subscribe(tree => {
      if (request === this.categoryRequest) {
        this.categories.set(tree);
      }
    });
  }

  private loadProjects(): void {
    this.service.getProjects().subscribe(projects => this.projects.set(projects));
  }

  private loadCounterparties(): void {
    this.service.getCounterparties().subscribe(list => this.counterparties.set(list));
  }

  /** 週期／分期 moved to 提醒中心 › 借還款追蹤 (spec "Schedule list moved to 提醒中心"). */
  openSchedules(): void {
    void this.router.navigate(['/accounting/reminders'], { queryParams: { tab: 'debts' }, fragment: 'schedules' });
  }

  private loadImportState(): void {
    this.service.getLatestImport().subscribe({ next: run => this.latest.set(run), error: () => this.latest.set(null) });
  }

  /** Runs a data-list write, then reloads; 409 shows `conflict`, anything else the server message. */
  private write(request: Observable<unknown>, reload: () => void, conflict: string): void {
    this.dataMessage.set(null);
    request.subscribe({
      next: () => reload(),
      error: err => this.dataMessage.set((err as { status?: number })?.status === 409 ? conflict : writeErrorMessage(err)),
    });
  }

  /** ⏎ in an add field; skips handled events and ⏎ that commits an IME candidate (Zhuyin: `isComposing` / keyCode 229). */
  enterPressed(event: KeyboardEvent): boolean {
    if (isHandledKey(event)) {
      return false;
    }
    event.preventDefault();
    return true;
  }

  /** Two-tap delete: the first tap arms the row, a second tap on the same row returns true. */
  confirmTap(key: string): boolean {
    if (this.confirming() === key) {
      this.confirming.set(null);
      return true;
    }
    this.confirming.set(key);
    return false;
  }

  /** A tap anywhere except a 刪除 button disarms the pending delete. */
  onDocumentClick(event: Event): void {
    const target = event.target as Element | null;
    if (this.confirming() !== null && !target?.closest?.('.row-delete')) {
      this.confirming.set(null);
    }
  }

  // 對帳規則
  private loadRules(): void {
    const request = ++this.ruleRequest;
    this.inbox.rules().subscribe({
      next: rules => {
        if (request === this.ruleRequest) {
          this.rules.set(rules);
        }
      },
      error: () => undefined, // keep the list that is shown
    });
  }

  private categoryName(id: number): string {
    return this.ruleCategories().get(id) ?? `#${id}`;
  }

  ruleText(rule: OwnerRule): string {
    const account = this.accounts().find(item => item.id === rule.account_id);
    const amount = rule.amount === null ? '任何金額' : formatMoney(rule.amount, account?.currency ?? 'TWD', { sign: true });
    const categoryId = rule.params['category_id'];
    const answer =
      rule.action === 'record'
        ? `記為 ${typeof categoryId === 'number' ? this.categoryName(categoryId) : '#' + String(categoryId)}`
        : rule.action === 'dismiss'
          ? '忽略'
          : rule.action === 'accept_bank_amount'
            ? '用銀行的數'
            : rule.action;
    const used = rule.last_applied_at ? ` · ${slashDate(rule.last_applied_at)}` : '';
    return `${account?.name ?? '#' + rule.account_id} · 「${rule.merchant_pattern}」 ${amount} · ${answer} · 用過 ${rule.applied_count} 次${used}`;
  }

  // Both writes bump `InboxService.changed`, which the constructor's effect answers with a re-read.
  toggleRule(rule: OwnerRule): void {
    this.write(this.inbox.patchRule(rule.id, !rule.enabled), () => undefined, '無法更新規則');
  }

  deleteRule(rule: OwnerRule): void {
    const mark = (on: boolean): void =>
      this.deletingRules.update(current => {
        const next = new Set(current);
        if (on) {
          next.add(rule.id);
        } else {
          next.delete(rule.id);
        }
        return next;
      });
    mark(true);
    this.write(this.inbox.deleteRule(rule.id).pipe(finalize(() => mark(false))), () => undefined, '無法刪除規則');
  }

  // 帳戶分組
  renameGroup(group: AccountGroup, name: string): void {
    if (name.trim() && name.trim() !== group.name) {
      this.write(this.service.updateAccountGroup(group.id, { name: name.trim(), sort_order: group.sort_order }), () => this.loadGroups(), '名稱重複');
    }
  }

  moveGroup(index: number, delta: number): void {
    const reordered = swap(this.groups(), index, delta);
    if (reordered) {
      this.write(this.service.reorderAccountGroups(reordered.map(group => group.id)), () => this.loadGroups(), '無法排序');
    }
  }

  deleteGroup(group: AccountGroup): void {
    this.write(this.service.deleteAccountGroup(group.id), () => this.loadGroups(), `「${group.name}」仍有帳戶，無法刪除`);
  }

  addGroup(): void {
    const name = this.newGroup().trim();
    if (name) {
      this.newGroup.set('');
      this.write(this.service.createAccountGroup({ name, sort_order: this.groups().length }), () => this.loadGroups(), '名稱重複');
    }
  }

  // 類別
  setCategoryKind(kind: CategoryTab): void {
    this.categoryKind.set(kind);
    this.loadCategories();
  }

  saveCategory(node: CategoryNode, patch: Partial<Pick<CategoryNode, 'name' | 'icon' | 'color' | 'is_hidden'>>): void {
    const body = {
      kind: node.kind,
      parent_id: node.parent_id,
      name: node.name,
      icon: node.icon,
      color: node.color,
      sort_order: node.sort_order,
      is_hidden: node.is_hidden,
      ...patch,
    };
    body.name = body.name.trim();
    if (body.name === '') {
      return;
    }
    this.write(this.service.updateCategory(node.id, body), () => this.loadCategories(), '同層已有相同名稱');
  }

  moveCategory(siblings: CategoryNode[], index: number, delta: number): void {
    const reordered = swap(siblings, index, delta);
    if (reordered) {
      this.write(this.service.reorderCategories(reordered.map(node => node.id)), () => this.loadCategories(), '無法排序');
    }
  }

  deleteCategory(node: CategoryNode): void {
    this.write(this.service.deleteCategory(node.id), () => this.loadCategories(), `「${node.name}」仍有記錄使用，無法刪除；可改為隱藏`);
  }

  addCategory(parent: CategoryNode | null): void {
    const name = (parent ? this.newSub()[parent.id] ?? '' : this.newCategory()).trim();
    if (!name) {
      return;
    }
    if (parent) {
      this.newSub.update(current => ({ ...current, [parent.id]: '' }));
    } else {
      this.newCategory.set('');
    }
    const siblings = parent ? parent.children ?? [] : this.categories();
    this.write(
      this.service.createCategory({
        kind: this.categoryKind(),
        parent_id: parent?.id ?? null,
        name,
        icon: null,
        color: null,
        sort_order: siblings.length,
        is_hidden: false,
      }),
      () => this.loadCategories(),
      '同層已有相同名稱',
    );
  }

  setNewSub(parentId: number, value: string): void {
    this.newSub.update(current => ({ ...current, [parentId]: value }));
  }

  // 專案
  saveProject(project: Project, patch: Partial<Pick<Project, 'name' | 'is_archived' | 'sort_order' | 'exclude_from_reports'>>): void {
    // 不計入報表 (add-accounting-reports) rides along once the API reports it, so an older server never sees the field.
    const exclude = project.exclude_from_reports === undefined ? {} : { exclude_from_reports: project.exclude_from_reports };
    const body = { name: project.name, is_archived: project.is_archived, sort_order: project.sort_order, ...exclude, ...patch };
    body.name = body.name.trim();
    if (body.name) {
      this.write(this.service.updateProject(project.id, body), () => this.loadProjects(), '名稱重複');
    }
  }

  moveProject(index: number, delta: number): void {
    const list = this.projects();
    const other = list[index + delta];
    const current = list[index];
    if (!other || !current) {
      return;
    }
    const [a, b] = current.sort_order === other.sort_order ? [index + delta, index] : [other.sort_order, current.sort_order];
    // Every field rides along (the API writes the whole project), so a reorder keeps 不計入報表 exactly as saveProject does.
    const put = (project: Project, sortOrder: number) =>
      this.service.updateProject(project.id, {
        name: project.name,
        is_archived: project.is_archived,
        sort_order: sortOrder,
        ...(project.exclude_from_reports === undefined ? {} : { exclude_from_reports: project.exclude_from_reports }),
      });
    // No batch endpoint for projects: two PUTs in order, and a failed second one puts the first back, so the pair is
    // never half-swapped. Once the first PUT has committed, every failure re-reads the list (revert or not), so it
    // shows what the server has; a failed first PUT changed nothing.
    const swapped = put(current, a).pipe(
      concatMap(() =>
        put(other, b).pipe(
          catchError((error: unknown) =>
            put(current, current.sort_order).pipe(
              catchError(() => of(null)),
              concatMap(() => {
                this.loadProjects();
                return throwError(() => error);
              }),
            ),
          ),
        ),
      ),
    );
    this.write(swapped, () => this.loadProjects(), '無法排序');
  }

  deleteProject(project: Project): void {
    this.write(this.service.deleteProject(project.id), () => this.loadProjects(), `「${project.name}」仍有記錄使用，無法刪除；可改為封存`);
  }

  addProject(): void {
    const name = this.newProject().trim();
    if (name) {
      this.newProject.set('');
      this.write(this.service.createProject({ name, is_archived: false, sort_order: this.projects().length }), () => this.loadProjects(), '名稱重複');
    }
  }

  // 對象
  renameCounterparty(counterparty: Counterparty, name: string): void {
    if (name.trim() && name.trim() !== counterparty.name) {
      this.write(this.service.updateCounterparty(counterparty.id, { name: name.trim() }), () => this.loadCounterparties(), '名稱重複');
    }
  }

  deleteCounterparty(counterparty: Counterparty): void {
    this.write(this.service.deleteCounterparty(counterparty.id), () => this.loadCounterparties(), `「${counterparty.name}」仍有記錄使用，無法刪除`);
  }

  addCounterparty(): void {
    const name = this.newCounterparty().trim();
    if (name) {
      this.newCounterparty.set('');
      this.write(this.service.createCounterparty({ name }), () => this.loadCounterparties(), '名稱重複');
    }
  }

  openAmounts(counterparty: Counterparty): string {
    return counterparty.open_amounts.map(item => formatMoney(item.amount, item.currency)).join(' · ');
  }

  // 顯示
  /** Applied at once (optimistic) and queued; a failed save reverts to the last confirmed preference. */
  setPreference<K extends keyof Preference>(key: K, value: Preference[K]): void {
    const current = this.preference();
    if (!current) {
      return;
    }
    const next = { ...current, [key]: value };
    this.dataMessage.set(null);
    this.preference.set(next);
    this.pendingPreferenceSaves++;
    this.preferenceSaves.next(next);
  }

  // 匯入
  onFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.file.set(input.files?.[0] ?? null);
    this.dryRunFile.set(null);
    this.report.set(null);
    this.reportMode.set(null);
    this.importError.set(null);
  }

  dryRun(): void {
    const file = this.file();
    if (!file) {
      return;
    }
    this.runImport(file, true);
  }

  realRun(): void {
    const file = this.file();
    if (!file || !this.canImport()) {
      return;
    }
    this.runImport(file, false);
  }

  private runImport(file: File, dryRun: boolean): void {
    this.importing.set(true);
    this.importError.set(null);
    this.service.importBackup(file, { dryRun, strict: true }).subscribe({
      next: report => {
        this.importing.set(false);
        if (dryRun && this.file() !== file) {
          return; // another file was chosen while the dry run was running; its result no longer applies
        }
        this.report.set(summarizeReport(report));
        this.reportMode.set(dryRun ? 'dry' : 'real');
        if (dryRun) {
          this.dryRunFile.set(file);
        } else {
          this.dryRunFile.set(null);
          this.loadImportState();
        }
      },
      error: err => {
        this.importing.set(false);
        this.importError.set(writeErrorMessage(err));
        // The previous run's report no longer describes this file's state; a failed 試算 also no longer clears 匯入.
        this.report.set(null);
        this.reportMode.set(null);
        if (dryRun) {
          this.dryRunFile.set(null);
        }
      },
    });
  }

  kindLabel(kind: string): string {
    return ENTRY_KIND_LABELS[kind as EntryKind] ?? kind;
  }
}
