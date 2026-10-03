import { Location } from '@angular/common';
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
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable } from 'rxjs';

import { ENTRY_KIND_LABELS, EntryDetail, EntryKind, LedgerAccount, LedgerEntry } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { NO_ENTER_SAVE_TAGS, colorOf, fxConversionLine, iconOf, isHandledKey, leavePage } from '../accounting-ui';
import { amountString, parseAmountText } from '../amount-text';
import { shortDate, slashDate, todayIso } from '../dates';
import { formatMoney } from '../format';
import { writeErrorMessage } from '../http-errors';
import { LockBannerComponent } from '../lock-banner/lock-banner';

const FX_SOURCE_LABELS: Record<string, string> = {
  fx_api: '線上匯率',
  moze_backup: 'MOZE 匯率',
  manual: '手動',
};

export type DetailPanel = 'settle' | 'refund' | 'delete' | null;

export interface RelatedLine {
  id: number;
  label: string;
  amount: string;
  currency: string;
}

interface Chip {
  label: string;
  tag: boolean;
}

@Component({
  selector: 'app-entry-detail',
  standalone: true,
  imports: [RouterLink, LockBannerComponent],
  templateUrl: './entry-detail.html',
  styleUrl: './entry-detail.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onKeydown($event)' },
})
export class EntryDetailComponent implements OnInit {
  private service = inject(AccountingService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private location = inject(Location);
  private destroyRef = inject(DestroyRef);
  private layoutMode = inject(LayoutModeService);
  private host = inject<ElementRef<HTMLElement>>(ElementRef);
  private injector = inject(Injector);

  /** Entry id of the route (`:eid` under a passbook, else `:id`). */
  readonly routeEntryId = signal<number | null>(null);
  /** The passbook the detail was opened from (`accounts/:id/entries/:eid`), else null. */
  readonly passbookId = signal<number | null>(null);
  /** In the layout's 760–1023 px sheet the sheet's own ✕ closes it: no second close control here. */
  readonly inSheet = computed(() => this.layoutMode.mode() === 'sheet');
  readonly detail = signal<EntryDetail | null>(null);
  readonly accounts = signal<LedgerAccount[]>([]);
  readonly loadError = signal(false);
  /**
   * Set when the navigation that opened this entry asked ✕ to go to a fixed page: `state.closeTo === 'list'` (the
   * parent list; after a split member edit the previous history entry is the member's old, deleted id) or
   * `'reminders'` (opened from the reminder centre).
   */
  private closeTo: 'list' | 'reminders' | null = null;
  readonly panel = signal<DetailPanel>(null);
  readonly formAccountId = signal<number | null>(null);
  readonly formAmount = signal('');
  readonly formDate = signal(todayIso());
  readonly formNote = signal('');
  readonly formError = signal<string | null>(null);
  readonly busy = signal(false);

  readonly today = todayIso();
  readonly kindLabels = ENTRY_KIND_LABELS;
  readonly formatMoney = formatMoney;
  readonly slashDate = slashDate;

  readonly locked = computed(() => this.detail()?.locked ?? false);
  readonly children = computed(() => this.detail()?.children ?? []);
  readonly feeTotal = computed(() =>
    this.children().filter(child => child.kind === 'fee').reduce((sum, child) => sum + Math.abs(Number(child.amount)), 0),
  );
  readonly total = computed(() => {
    const detail = this.detail();
    if (!detail) {
      return 0;
    }
    return this.children().reduce((sum, child) => sum + Number(child.amount), Number(detail.amount));
  });
  readonly categoryPath = computed(() => {
    const detail = this.detail();
    if (!detail) {
      return '';
    }
    return (detail.category ?? this.kindLabels[detail.kind]).split('/').join(' › ');
  });
  readonly categoryLeaf = computed(() => {
    const detail = this.detail();
    return detail?.category?.split('/').pop() ?? (detail ? this.kindLabels[detail.kind] : '');
  });
  readonly fxLine = computed(() => {
    const detail = this.detail();
    const converted = detail ? fxConversionLine(detail) : null;
    if (!detail || converted === null) {
      return null;
    }
    const source = FX_SOURCE_LABELS[detail.fx_source ?? ''] ?? '';
    return source ? `${converted}（${source}）` : converted;
  });
  readonly iconOf = iconOf;
  readonly colorOf = colorOf;
  /** 應收 / 應付 kind, including the 收款 / 還款 rows that settle one (the grid shows 對象 for all of them). */
  readonly debtKind = computed(() => {
    const kind = this.detail()?.kind;
    return kind === 'receivable' || kind === 'payable';
  });
  /** An open debt that can be settled: not itself a 收款 / 還款 row (linked through `settles` or flagged `is_settlement`). */
  readonly isDebt = computed(() => {
    const detail = this.detail();
    return !!detail && this.debtKind() && !detail.settles && !detail.is_settlement;
  });
  /** 收款 / 還款 rows cannot be edited (`PUT` answers 422 `kind`), so 編輯 is disabled for them as well as while locked. */
  readonly canEdit = computed(() => {
    const detail = this.detail();
    return !!detail && !this.locked() && !detail.is_settlement;
  });
  readonly canSettle = computed(() => {
    const detail = this.detail();
    return this.isDebt() && !this.locked() && !detail?.is_settled && !detail?.is_closed && Number(detail?.open_amount ?? 0) > 0;
  });
  readonly refundable = computed(() => {
    const detail = this.detail();
    if (!detail) {
      return 0;
    }
    return Math.max(0, Math.abs(Number(detail.amount)) - Number(detail.refunded_amount ?? 0));
  });
  readonly refundKind = computed(() => {
    const detail = this.detail();
    return !!detail && (detail.kind === 'expense' || detail.kind === 'fee') && Number(detail.amount) < 0 && !detail.refunds;
  });
  readonly canRefund = computed(() => this.refundKind() && !this.locked() && this.refundable() > 0);
  readonly sameCurrencyAccounts = computed(() => {
    const detail = this.detail();
    return this.accounts().filter(account => !account.is_archived && account.currency === detail?.currency);
  });
  readonly siblings = computed(() => {
    const detail = this.detail();
    return (detail?.group_members ?? []).filter(member => member.id !== detail?.id);
  });
  readonly chips = computed<Chip[]>(() => {
    const detail = this.detail();
    if (!detail) {
      return [];
    }
    const rules = detail.rules?.length ? detail.rules.map(rule => rule.name) : detail.rule_names ?? [];
    return [...(detail.tags ?? []).map(tag => ({ label: `#${tag}`, tag: true })), ...rules.map(name => ({ label: name, tag: false }))];
  });
  readonly related = computed<RelatedLine[]>(() => {
    const detail = this.detail();
    if (!detail) {
      return [];
    }
    const line = (entry: LedgerEntry, label: string): RelatedLine => ({ id: entry.id, label, amount: entry.amount, currency: entry.currency });
    const lines: RelatedLine[] = [];
    for (const child of detail.children ?? []) {
      lines.push(line(child, [this.kindLabels[child.kind], child.name].filter(Boolean).join(' · ')));
    }
    if (detail.transfer_counterpart) {
      const other = detail.transfer_counterpart;
      lines.push(line(other, `${this.kindLabels[other.kind]} · ${other.account_name}`));
    }
    for (const sibling of this.siblings()) {
      lines.push(line(sibling, `拆帳 · ${sibling.name || this.kindLabels[sibling.kind]} · ${sibling.account_name}`));
    }
    if (detail.settles) {
      lines.push(line(detail.settles, `結清 · ${detail.settles.name || this.kindLabels[detail.settles.kind]}`));
    }
    const settleWord = detail.kind === 'payable' ? '還款' : '收款';
    for (const settlement of detail.settled_by ?? []) {
      lines.push(line(settlement, `${settleWord} · ${slashDate(settlement.entry_date)} · ${settlement.account_name}`));
    }
    if (detail.refunds) {
      lines.push(line(detail.refunds, `退款自 · ${detail.refunds.name || this.kindLabels[detail.refunds.kind]}`));
    }
    for (const refund of detail.refunded_by ?? []) {
      lines.push(line(refund, `退款 · ${slashDate(refund.entry_date)} · ${refund.account_name}`));
    }
    return lines;
  });
  readonly deleteNotes = computed(() => {
    const detail = this.detail();
    if (!detail) {
      return [];
    }
    const notes = (detail.children ?? []).map(child => `${this.kindLabels[child.kind]} ${formatMoney(child.amount, child.currency)}`);
    if (detail.transfer_counterpart) {
      const other = detail.transfer_counterpart;
      notes.push(`另一筆轉帳：${other.account_name} ${formatMoney(other.amount, other.currency)}`);
    }
    return notes;
  });
  readonly formMax = computed(() =>
    this.panel() === 'settle' ? Number(this.detail()?.open_amount ?? 0) : this.refundable(),
  );
  readonly settleLabel = computed(() => (this.detail()?.kind === 'payable' ? '新增還款' : '新增收款'));
  /**
   * A debt original once something came back (or MOZE closed it): `原始 $420 · 已收 $200 · 剩餘 $220` (已還 for a
   * payable; 已結清 in place of 剩餘 when closed). 已收 / 已還 is Σ of the linked settlements.
   */
  readonly breakdown = computed(() => {
    const detail = this.detail();
    if (!detail || detail.open_amount === null || (detail.kind !== 'receivable' && detail.kind !== 'payable')) {
      return null;
    }
    const back = Math.abs(detail.settled_by.reduce((sum, settlement) => sum + Number(settlement.amount), 0));
    if (back === 0 && !detail.is_closed) {
      return null;
    }
    const currency = detail.currency;
    const parts = [
      `原始 ${formatMoney(Math.abs(Number(detail.amount)), currency)}`,
      `${detail.kind === 'payable' ? '已還' : '已收'} ${formatMoney(back, currency)}`,
      detail.is_closed ? '已結清' : `剩餘 ${formatMoney(detail.open_amount, currency)}`,
    ];
    return parts.join(' · ');
  });

  ngOnInit(): void {
    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(params => {
      const passbook = params.get('eid') !== null;
      const id = Number(passbook ? params.get('eid') : params.get('id'));
      this.panel.set(null);
      this.busy.set(false);
      this.routeEntryId.set(id);
      this.passbookId.set(passbook ? Number(params.get('id')) : null);
      const state = this.router.currentNavigation()?.extras.state ?? (this.location.getState() as Record<string, unknown> | null);
      const closeTo = state?.['closeTo'];
      this.closeTo = closeTo === 'list' || closeTo === 'reminders' ? closeTo : null;
      // Another entry: drop the shown one at once, so nothing acts on it while the new one loads. A reload of the
      // same entry keeps it (and keeps it when that reload fails).
      if (this.detail()?.id !== id) {
        this.detail.set(null);
      }
      this.load(id);
    });
    this.service
      .getAccounts()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ next: accounts => this.accounts.set(accounts), error: () => undefined });
  }

  /** Bumped per `load()`; a response for an earlier load (fast navigation between entries) is dropped. */
  private loadSeq = 0;

  /** The list the detail belongs to: the reminder centre or the passbook it was opened from, else the timeline. */
  private parentUrl(): string {
    if (this.closeTo === 'reminders') {
      return '/accounting/reminders';
    }
    const passbook = this.passbookId();
    return passbook === null ? '/accounting' : `/accounting/accounts/${passbook}`;
  }

  /**
   * ✕ and after a delete: back to where the owner came from (in-app history), else the parent list. Opened with
   * `closeTo` it always goes to that page.
   */
  close(): void {
    if (this.closeTo !== null) {
      void this.router.navigateByUrl(this.parentUrl());
      return;
    }
    leavePage(this.router, this.location, this.parentUrl());
  }

  /** The entry shown, only while it is also the one in the URL; writes act on nothing else. */
  private routedDetail(): EntryDetail | null {
    const detail = this.detail();
    return detail && detail.id === this.routeEntryId() ? detail : null;
  }

  /** True while `id` is still the entry shown and the one in the URL (a write's answer may arrive later). */
  private stillShowing(id: number): boolean {
    return this.detail()?.id === id && this.routeEntryId() === id;
  }

  /**
   * Runs a settle / refund / delete for the entry shown now. The result is applied only while that entry is still
   * shown; the request is dropped when the page is destroyed.
   */
  private runWrite(request: Observable<unknown>, id: number, done: () => void): void {
    this.busy.set(true);
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => {
        if (!this.stillShowing(id)) {
          return;
        }
        this.busy.set(false);
        done();
      },
      error: err => {
        if (!this.stillShowing(id)) {
          return;
        }
        this.busy.set(false);
        this.formError.set(writeErrorMessage(err));
      },
    });
  }

  /** `afterWrite`: a reload after a successful write; when it fails the stale record is cleared, not shown. */
  load(id: number, afterWrite = false): void {
    const seq = ++this.loadSeq;
    this.loadError.set(false);
    this.service
      .getEntry(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: detail => {
          if (seq === this.loadSeq) {
            this.detail.set(detail);
          }
        },
        error: () => {
          if (seq === this.loadSeq) {
            if (afterWrite || this.detail()?.id !== id) {
              this.detail.set(null);
            }
            this.loadError.set(true);
          }
        },
      });
  }

  rewardWhen(reward: LedgerEntry): string {
    return reward.posted_date > this.today ? `將於 ${shortDate(reward.posted_date)} 入帳` : `${shortDate(reward.posted_date)} 入帳`;
  }

  kindLabel(kind: EntryKind): string {
    return this.kindLabels[kind];
  }

  edit(): void {
    const detail = this.detail();
    if (detail && this.canEdit()) {
      this.router.navigate(['/accounting/entries', detail.id, 'edit']);
    }
  }

  copy(): void {
    const detail = this.detail();
    if (detail) {
      this.router.navigate(['/accounting/entry'], { queryParams: { copy: detail.id } });
    }
  }

  openPanel(panel: Exclude<DetailPanel, null>): void {
    const detail = this.routedDetail();
    if (!detail || this.locked()) {
      return;
    }
    this.formError.set(null);
    this.formNote.set('');
    this.formDate.set(this.today);
    if (panel !== 'delete') {
      const accounts = this.sameCurrencyAccounts();
      const own = accounts.find(account => account.id === detail.account_id);
      this.formAccountId.set(own?.id ?? accounts[0]?.id ?? null);
      const max = panel === 'settle' ? Number(detail.open_amount ?? 0) : this.refundable();
      this.formAmount.set(max > 0 ? amountString(max, detail.currency) : '');
    }
    this.panel.set(panel);
    if (panel !== 'delete') {
      // Keys start inside the form, so Esc / ⏎ reach it.
      afterNextRender(() => this.host.nativeElement.querySelector<HTMLElement>('.form-amount')?.focus(), {
        injector: this.injector,
      });
    }
  }

  /**
   * Esc with an inline panel (收款 / 還款 / 退款 / 刪除) open closes that panel first and is marked handled, so the
   * layout's Esc does not also close the sheet. ⏎ in an inline-form field submits it (not on a select, a button or an
   * IME commit).
   */
  onKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event) || this.panel() === null) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      this.panel.set(null);
      return;
    }
    const target = event.target as HTMLElement | null;
    if (
      event.key === 'Enter' &&
      (this.panel() === 'settle' || this.panel() === 'refund') &&
      target?.closest('.inline-form') &&
      !NO_ENTER_SAVE_TAGS.has(target.tagName)
    ) {
      event.preventDefault();
      if (!this.busy()) {
        this.submitForm();
      }
    }
  }

  setFormAccount(value: string): void {
    this.formAccountId.set(Number(value));
  }

  submitForm(): void {
    const detail = this.routedDetail();
    const panel = this.panel();
    if (!detail || (panel !== 'settle' && panel !== 'refund')) {
      return;
    }
    const accountId = this.formAccountId();
    if (accountId === null) {
      this.formError.set('請選擇帳戶');
      return;
    }
    const value = parseAmountText(this.formAmount(), detail.currency);
    if (value === null) {
      this.formError.set('請輸入金額');
      return;
    }
    const max = this.formMax();
    if (value > max + 1e-9) {
      this.formError.set(`金額不可超過 ${formatMoney(max, detail.currency)}`);
      return;
    }
    const body = {
      account_id: accountId,
      amount: amountString(value, detail.currency),
      entry_date: this.formDate(),
      entry_time: null,
      description: this.formNote().trim() || null,
    };
    const id = detail.id;
    const request = panel === 'settle' ? this.service.settleEntry(id, body) : this.service.refundEntry(id, body);
    this.runWrite(request, id, () => {
      this.panel.set(null);
      this.load(id, true);
    });
  }

  confirmDelete(scope: 'entry' | 'group'): void {
    const detail = this.routedDetail();
    if (!detail || this.locked()) {
      return;
    }
    const id = detail.id;
    const request = scope === 'group' && detail.group ? this.service.deleteSplit(detail.group.id) : this.service.deleteEntry(id);
    this.runWrite(request, id, () => this.close());
  }
}
