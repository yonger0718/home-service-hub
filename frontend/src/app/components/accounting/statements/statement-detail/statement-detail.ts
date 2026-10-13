import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { AccountPeriodSummary, LedgerEntry } from '../../../../models/accounting.model';
import { AccountingService } from '../../../../services/accounting.service';
import { LayoutModeService } from '../../../../services/layout-mode.service';
import { AccountingToastService } from '../../accounting-toast';
import { focusSheetField, isHandledKey, sheetKeyAction, trapFocus } from '../../accounting-ui';
import { DirtyAware, DirtyFormRegistry } from '../../dirty-form.service';
import { fieldErrors } from '../../http-errors';
import { formatMoney } from '../../format';
import { SkeletonComponent } from '../../skeleton/skeleton';
import { StatementDetail } from '../statement.model';
import {
  FailedCheck,
  HeaderComparison,
  SegmentRow,
  StatementSegments,
  actionErrorMessage,
  actionsAvailable,
  buildSegments,
  confirmBlockedReason,
  confirmRefusal,
  deltaWarns,
  headerComparison,
  matchRuleLabel,
  periodText,
  roleLabel,
  statementChips,
} from '../statement-view';
import { StatementsService } from '../statements.service';

type SegmentKey = 'matched' | 'statement' | 'ledger';

const ENTRY_LIMIT = 500;

/** `/accounting/accounts/:id/statements/:sid`. Case rows are read-only; the action sheet is R3b. */
@Component({
  selector: 'app-statement-detail',
  standalone: true,
  imports: [RouterLink, SkeletonComponent],
  templateUrl: './statement-detail.html',
  styleUrls: ['../../entry-row.scss', '../../sheet.scss', './statement-detail.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onKeydown($event)' },
})
export class StatementDetailComponent implements DirtyAware {
  private readonly route = inject(ActivatedRoute);
  private readonly accounting = inject(AccountingService);
  private readonly statementsApi = inject(StatementsService);
  private readonly layoutMode = inject(LayoutModeService);
  private readonly toast = inject(AccountingToastService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  readonly dirty = inject(DirtyFormRegistry);

  private requestId = 0;

  readonly accountId = signal(0);
  readonly statementId = signal(0);
  readonly detail = signal<StatementDetail | null>(null);
  readonly summary = signal<AccountPeriodSummary | null>(null);
  readonly entries = signal<ReadonlyMap<number, LedgerEntry>>(new Map());
  readonly entriesTruncated = signal(false);
  readonly loading = signal(true);
  readonly loadError = signal(false);
  readonly summaryError = signal(false);
  readonly segment = signal<SegmentKey>('matched');
  readonly expanded = signal<string | null>(null);
  readonly busy = signal(false);
  readonly actionError = signal<string | null>(null);
  readonly refusal = signal<FailedCheck[] | null>(null);
  readonly forceOpen = signal(false);
  readonly note = signal('');
  readonly noteError = signal<string | null>(null);

  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  readonly chips = computed(() => {
    const detail = this.detail();
    return detail ? statementChips(detail, detail.stale_events_pending) : [];
  });
  readonly comparison = computed<HeaderComparison | null>(() => {
    const detail = this.detail();
    const summary = this.summary();
    return detail && summary ? headerComparison(detail, summary) : null;
  });
  readonly deltaWarns = computed(() => {
    const detail = this.detail();
    const comparison = this.comparison();
    return detail && comparison ? deltaWarns(comparison.delta, detail.currency) : false;
  });
  readonly segments = computed<StatementSegments | null>(() => {
    const detail = this.detail();
    return detail ? buildSegments(detail, this.entries()) : null;
  });
  readonly rows = computed(() => {
    const segments = this.segments();
    if (!segments) {
      return [];
    }
    if (this.segment() === 'statement') {
      return segments.statementOnly;
    }
    if (this.segment() === 'ledger') {
      return segments.ledgerOnly;
    }
    return segments.matched;
  });
  readonly showActions = computed(() => {
    const detail = this.detail();
    return detail ? actionsAvailable(detail) : false;
  });
  readonly confirmReason = computed(() => {
    const detail = this.detail();
    return detail ? confirmBlockedReason(detail) : null;
  });

  readonly periodText = periodText;
  readonly formatMoney = formatMoney;
  readonly roleLabel = roleLabel;
  readonly matchRuleLabel = matchRuleLabel;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.dirty.unregister(this));
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((params) => {
      const accountId = Number(params.get('id'));
      const statementId = Number(params.get('sid'));
      if (!accountId || !statementId) {
        return;
      }
      if (accountId === this.accountId() && statementId === this.statementId() && this.detail()) {
        return;
      }
      this.accountId.set(accountId);
      this.statementId.set(statementId);
      this.load();
    });
  }

  isDirty(): boolean {
    return this.note().trim().length > 0;
  }

  showDiscardPrompt(): void {
    this.host.nativeElement.querySelector<HTMLElement>('.discard-yes')?.focus();
  }

  restoreDiscardFocus(): void {
    this.host.nativeElement.querySelector<HTMLElement>('.force-note')?.focus();
  }

  retry(): void {
    this.load();
  }

  setSegment(key: SegmentKey): void {
    this.segment.set(key);
    this.expanded.set(null);
  }

  toggle(key: string): void {
    this.expanded.update((current) => (current === key ? null : key));
  }

  ruleText(row: SegmentRow): string {
    return row.matchRules.map((rule) => matchRuleLabel(rule)).join('、');
  }

  reconcile(): void {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    this.actionError.set(null);
    this.statementsApi.reconcile(this.accountId(), this.statementId()).subscribe({
      next: (result) => {
        this.busy.set(false);
        if (result.replayed) {
          this.toast.show('這次與上次相同，沒有重複執行');
        } else if (result.skipped) {
          this.toast.show(
            result.skipped === 'parse_review'
              ? '有待確認的變更，這次沒有重跑對帳'
              : `這次略過（${result.skipped}）`,
          );
        } else {
          this.toast.show('已重新對帳');
        }
        this.load();
      },
      error: (err) => {
        this.busy.set(false);
        this.actionError.set(actionErrorMessage(err));
      },
    });
  }

  confirm(): void {
    if (this.busy() || this.confirmReason()) {
      return;
    }
    this.postConfirm(false, null);
  }

  openForce(): void {
    this.note.set('');
    this.noteError.set(null);
    this.forceOpen.set(true);
    this.dirty.register(this);
    focusSheetField(this.host.nativeElement, '.force-note', this.injector);
  }

  requestCloseForce(): void {
    this.dirty.requestClose(() => this.closeForce());
  }

  submitForce(): void {
    const note = this.note().trim();
    if (!note) {
      this.noteError.set('強制確認需要填寫備註');
      return;
    }
    this.postConfirm(true, note);
  }

  onNote(value: string): void {
    this.note.set(value);
    this.noteError.set(null);
  }

  onKeydown(event: KeyboardEvent): void {
    if (!this.forceOpen() || isHandledKey(event)) {
      return;
    }
    if (this.dirty.promptOpen()) {
      if (event.key === 'Escape') {
        event.preventDefault();
        this.dirty.cancelDiscard();
      }
      return;
    }
    const sheet = this.sheet();
    if (!sheet) {
      return;
    }
    if (trapFocus(sheet, event)) {
      return;
    }
    const target = event.target as HTMLElement | null;
    if (event.key === 'Enter' && target?.tagName === 'TEXTAREA') {
      return;
    }
    const action = sheetKeyAction(event, sheet);
    if (action === 'close') {
      this.requestCloseForce();
    } else if (action === 'confirm') {
      this.submitForce();
    }
  }

  private postConfirm(force: boolean, note: string | null): void {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    this.actionError.set(null);
    this.refusal.set(null);
    this.statementsApi.confirm(this.accountId(), this.statementId(), { force, note }).subscribe({
      next: (result) => {
        this.busy.set(false);
        this.closeForce();
        if (result.replayed) {
          this.toast.show('這次與上次相同，沒有重複執行');
        } else {
          this.toast.show(result.forced ? '已強制確認' : '已確認');
        }
        this.load();
      },
      error: (err) => {
        this.busy.set(false);
        const checks = confirmRefusal(err);
        if (checks) {
          this.refusal.set(checks);
          this.closeForce();
          return;
        }
        const fields = fieldErrors(err);
        if (fields['note']?.includes('note_required')) {
          this.noteError.set('強制確認需要填寫備註');
          return;
        }
        this.actionError.set(actionErrorMessage(err));
      },
    });
  }

  private closeForce(): void {
    this.forceOpen.set(false);
    this.note.set('');
    this.dirty.unregister(this);
  }

  private sheet(): HTMLElement | null {
    return this.host.nativeElement.querySelector<HTMLElement>('.sheet');
  }

  private load(): void {
    const accountId = this.accountId();
    const statementId = this.statementId();
    const id = ++this.requestId;
    this.loading.set(true);
    this.loadError.set(false);
    this.summaryError.set(false);
    this.detail.set(null);
    this.summary.set(null);
    this.entries.set(new Map());
    this.entriesTruncated.set(false);
    this.expanded.set(null);
    this.refusal.set(null);
    this.actionError.set(null);
    this.statementsApi.get(accountId, statementId).subscribe({
      next: (detail) => {
        if (id !== this.requestId) {
          return;
        }
        this.detail.set(detail);
        this.loading.set(false);
        this.loadLedger(id, detail);
      },
      error: () => {
        if (id !== this.requestId) {
          return;
        }
        this.loading.set(false);
        this.loadError.set(true);
      },
    });
  }

  private loadLedger(id: number, detail: StatementDetail): void {
    this.accounting
      .getAccountSummary(detail.account_id, detail.period_start, detail.period_end)
      .subscribe({
        next: (summary) => {
          if (id === this.requestId) {
            this.summary.set(summary);
          }
        },
        error: () => {
          if (id === this.requestId) {
            this.summaryError.set(true);
          }
        },
      });
    this.accounting
      .getEntries(detail.account_id, {
        date_from: detail.period_start,
        date_to: detail.period_end,
        limit: ENTRY_LIMIT,
      })
      .subscribe({
        next: (page) => {
          if (id !== this.requestId) {
            return;
          }
          this.entries.set(new Map(page.items.map((entry) => [entry.id, entry])));
          this.entriesTruncated.set(page.total > page.items.length);
        },
        error: () => {
          if (id === this.requestId) {
            this.entries.set(new Map());
          }
        },
      });
  }
}
