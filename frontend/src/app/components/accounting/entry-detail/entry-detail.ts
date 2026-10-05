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

import {
  ENTRY_KIND_LABELS,
  EntryDetail,
  EntryKind,
  LedgerAccount,
  LedgerEntry,
  ScheduleDefinitionDetail,
  ScheduleLineKind,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingToastService, scheduleActionError } from '../accounting-toast';
import { NO_ENTER_SAVE_TAGS, colorOf, fxConversionLine, iconOf, isHandledKey, leavePage } from '../accounting-ui';
import { amountString, parseAmountText } from '../amount-text';
import { shortDate, slashDate, todayIso } from '../dates';
import { formatMoney } from '../format';
import { LockBannerComponent } from '../lock-banner/lock-banner';
import { schedulePill } from '../schedule-math';

const FX_SOURCE_LABELS: Record<string, string> = {
  fx_api: '線上匯率',
  moze_backup: 'MOZE 匯率',
  manual: '手動',
};

export type DetailPanel = 'settle' | 'refund' | 'delete' | 'scope' | 'repost' | null;

const LINE_LABELS: Record<ScheduleLineKind, string> = {
  expense: '支出', income: '收入', receivable: '應收款項', payable: '應付款項', transfer: '轉帳', repayment: '還款',
  collection: '收款', interest: '利息',
};
const FORM_KINDS: ReadonlySet<string> = new Set(['expense', 'income', 'receivable', 'payable']);

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
  private readonly toast = inject(AccountingToastService);
  /** The definition of the shown entry's period (scope choice, repost amounts); request-id guarded. */
  readonly scheduleDefinition = signal<ScheduleDefinitionDetail | null>(null);
  readonly repostAmounts = signal<string[]>([]);
  private scheduleRequest = 0;
  /** The control that opened the scope / repost panel; focus goes back to it when the panel closes. */
  private panelOpener: HTMLElement | null = null;

  readonly scheduleLink = computed(() => this.detail()?.schedule ?? null);
  readonly schedulePillText = computed(() => {
    const link = this.scheduleLink();
    return link ? schedulePill(link) : null;
  });
  /** `剩餘 −$275,001 · 已還 $24,999 · 下期 02/09` for a loan a schedule repays. */
  readonly loanLine = computed(() => {
    const detail = this.detail();
    const loan = detail?.loan_schedule;
    if (!detail || !loan || loan.remaining === null) {
      return null;
    }
    const back = detail.kind === 'receivable' ? '已收' : '已還'; // a collection loan (money lent) comes back
    const parts = [`剩餘 ${formatMoney(loan.remaining, detail.currency)}`, `${back} ${formatMoney(loan.repaid ?? 0, detail.currency)}`];
    if (loan.next_due_date) {
      parts.push(`下期 ${shortDate(loan.next_due_date)}`);
    }
    return parts.join(' · ');
  });
  readonly loanLinkText = computed(() => {
    const loan = this.detail()?.loan_schedule;
    if (!loan) {
      return null;
    }
    return `${loan.name} · 已入帳 ${loan.posted_count}${loan.times === null ? '' : ` / ${loan.times}`}`;
  });
  /** Kinds the entry form edits (a settlement is edited by repost instead). */
  readonly formEditable = computed(() => {
    const detail = this.detail();
    if (!detail) {
      return false;
    }
    if (detail.schedule && detail.group) {
      // A period's group member: the form saves it with PUT /splits/{id}, which a scheduled group refuses (409, D29).
      return false;
    }
    return (FORM_KINDS.has(detail.kind) && !detail.is_settlement) || detail.kind === 'transfer_out' || detail.kind === 'transfer_in';
  });
  readonly repostLines = computed(() =>
    (this.scheduleDefinition()?.template.lines ?? []).map((line, index) => ({ index, label: LINE_LABELS[line.kind], currency: line.currency })),
  );
  /** What deleting this entry does to its period (spec "Scheduled entry edit scope"). */
  readonly scheduleDeleteNote = computed(() => {
    const detail = this.detail();
    const link = detail?.schedule;
    if (!detail || !link) {
      return null;
    }
    const gone = new Set<number>([detail.id]);
    if (detail.transfer_counterpart) {
      gone.add(detail.transfer_counterpart.id);
    }
    const others = link.posted_entry_ids.filter(id => !gone.has(id));
    if (others.length === 0) {
      return link.acted_by === 'import' ? '此期將標示為略過' : '此期將回到待完成交易';
    }
    const named = (detail.group_members ?? [])
      .filter(member => others.includes(member.id))
      .map(member => `${this.kindLabels[member.kind]} ${formatMoney(member.amount, member.currency)}`);
    return `同期的 ${named.length > 0 ? named.join('、') : `其他 ${others.length} 筆`} 會保留，此期標示為部分入帳`;
  });

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
  /** 收款 / 還款 rows cannot be edited by the form (`PUT` answers 422 `kind`); a scheduled one is (編輯這一筆 reposts it). */
  readonly canEdit = computed(() => {
    const detail = this.detail();
    return !!detail && !this.locked() && (!detail.is_settlement || !!detail.schedule);
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
      this.panelOpener = null;
      // A definition read for the previous entry is dropped.
      this.scheduleRequest++;
      this.scheduleDefinition.set(null);
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
        this.formError.set(scheduleActionError(err, this.toast));
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
    if (!detail || !this.canEdit()) {
      return;
    }
    if (detail.schedule) {
      this.formError.set(null);
      this.openSchedulePanel('scope', '.scope-one');
      this.loadSchedule();
      return;
    }
    this.router.navigate(['/accounting/entries', detail.id, 'edit']);
  }

  /** Opens the scope / repost panel and moves focus into it; the opener gets focus back when it closes. */
  private openSchedulePanel(panel: 'scope' | 'repost', focus: string): void {
    if (this.panel() !== 'scope' && this.panel() !== 'repost') {
      const active = this.host.nativeElement.ownerDocument.activeElement;
      this.panelOpener = active instanceof HTMLElement && this.host.nativeElement.contains(active) ? active : null;
    }
    this.panel.set(panel);
    this.focusInPanel(focus);
  }

  private focusInPanel(selector: string): void {
    afterNextRender(() => this.host.nativeElement.querySelector<HTMLElement>(selector)?.focus(), { injector: this.injector });
  }

  /** Esc / 取消: closes the open panel and returns focus to the control that opened it. */
  closePanel(): void {
    this.panel.set(null);
    const opener = this.panelOpener;
    this.panelOpener = null;
    if (opener?.isConnected) {
      afterNextRender(() => opener.focus(), { injector: this.injector });
    }
  }

  /** The definition of the period (its lines and this period's amounts). */
  private loadSchedule(): void {
    const link = this.scheduleLink();
    if (!link) {
      return;
    }
    const request = ++this.scheduleRequest;
    this.scheduleDefinition.set(null);
    this.service
      .getScheduleDefinition(link.definition_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: definition => {
          if (request !== this.scheduleRequest) {
            return;
          }
          this.scheduleDefinition.set(definition);
          const instance = definition.instances.find(item => item.id === link.instance_id);
          this.repostAmounts.set(instance ? [...instance.amounts] : definition.template.lines.map(line => line.amount));
          if (this.panel() === 'repost') {
            this.focusInPanel('.repost-amount');
          }
        },
        error: () => {
          if (request === this.scheduleRequest) {
            this.formError.set('排程讀取失敗，請稍後再試。');
          }
        },
      });
  }

  /** The manage sheet of the schedule in 提醒中心 › 借還款追蹤. */
  openSchedule(definitionId: number): void {
    this.router.navigate(['/accounting/reminders'], { queryParams: { tab: 'debts', schedule: definitionId } });
  }

  /** 編輯這一筆: the entry form for kinds it edits, else the repost panel with the period's amounts. */
  editOne(): void {
    const detail = this.routedDetail();
    if (!detail) {
      return;
    }
    if (this.formEditable()) {
      this.router.navigate(['/accounting/entries', detail.id, 'edit']);
      return;
    }
    this.openSchedulePanel('repost', '.repost-amount');
  }

  /** 編輯整個排程: the entry form in definition mode (not for an imported definition before cutover). */
  editSchedule(): void {
    const link = this.scheduleLink();
    const definition = this.scheduleDefinition();
    if (!link || !definition || definition.locked) {
      return;
    }
    this.router.navigate(['/accounting/entry'], { queryParams: { schedule: link.definition_id } });
  }

  setRepostAmount(index: number, value: string): void {
    this.repostAmounts.update(list => list.map((amount, at) => (at === index ? value.trim() : amount)));
  }

  /** 重新入帳: the period's entries are replaced; the page follows the entry that took this one's place. */
  submitRepost(): void {
    const detail = this.routedDetail();
    const link = this.scheduleLink();
    if (!detail || !link || this.busy() || this.repostLines().length === 0) {
      return;
    }
    const id = detail.id;
    const position = Math.max(link.posted_entry_ids.indexOf(id), 0);
    this.busy.set(true);
    this.formError.set(null);
    this.service
      .repostScheduleInstance(link.instance_id, this.repostAmounts())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: instance => {
          if (!this.stillShowing(id)) {
            return;
          }
          this.busy.set(false);
          this.panel.set(null);
          this.panelOpener = null;
          const next = instance.posted_entry_ids[position] ?? instance.posted_entry_ids[0];
          if (next !== undefined) {
            this.router.navigate(['/accounting/entries', next], { replaceUrl: true });
          } else {
            this.load(id, true);
          }
        },
        error: (error: unknown) => {
          if (!this.stillShowing(id)) {
            return;
          }
          this.busy.set(false);
          this.formError.set(scheduleActionError(error, this.toast));
        },
      });
  }

  /** 重新入帳 on a partial period: the repost panel with the period's current amounts. */
  repostPartial(): void {
    if (!this.routedDetail() || this.locked()) {
      return;
    }
    this.formError.set(null);
    this.openSchedulePanel('repost', '.repost-amount');
    this.loadSchedule();
  }

  /** 保留部分: keep what is left of the period. */
  acceptPartial(): void {
    const detail = this.routedDetail();
    const link = this.scheduleLink();
    if (!detail || !link || this.locked()) {
      return;
    }
    const id = detail.id;
    this.formError.set(null);
    this.runWrite(this.service.acceptPartialScheduleInstance(link.instance_id), id, () => this.load(id, true));
  }

  copy(): void {
    const detail = this.detail();
    if (detail) {
      this.router.navigate(['/accounting/entry'], { queryParams: { copy: detail.id } });
    }
  }

  openPanel(panel: 'settle' | 'refund' | 'delete'): void {
    const detail = this.routedDetail();
    if (!detail || this.locked()) {
      return;
    }
    const active = this.host.nativeElement.ownerDocument.activeElement;
    this.panelOpener = active instanceof HTMLElement && this.host.nativeElement.contains(active) ? active : null;
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
      this.closePanel();
      return;
    }
    const target = event.target as HTMLElement | null;
    if (
      event.key === 'Enter' &&
      (this.panel() === 'settle' || this.panel() === 'refund' || this.panel() === 'repost') &&
      target?.closest('.inline-form') &&
      !NO_ENTER_SAVE_TAGS.has(target.tagName)
    ) {
      event.preventDefault();
      if (!this.busy()) {
        if (this.panel() === 'repost') {
          this.submitRepost();
        } else {
          this.submitForm();
        }
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
