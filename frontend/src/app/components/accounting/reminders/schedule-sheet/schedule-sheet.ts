import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { Router } from '@angular/router';
import { Observable } from 'rxjs';

import { ScheduleAmountScope, ScheduleDefinitionDetail, SchedulePostingMode } from '../../../../models/accounting.model';
import { AccountingService } from '../../../../services/accounting.service';
import { AccountingToastService, scheduleActionError } from '../../accounting-toast';
import { focusSheetField, sheetKeyAction } from '../../accounting-ui';
import { shortDate, todayIso } from '../../dates';
import { formatSigned } from '../../format';
import { ruleSummary } from '../../schedule-math';

type SheetConfirm = 'resume' | 'end' | 'delete' | null;

/** The manage sheet of one definition (spec "週期／分期 section"). */
@Component({
  selector: 'app-schedule-sheet',
  standalone: true,
  templateUrl: './schedule-sheet.html',
  styleUrls: ['../../sheet.scss', './schedule-sheet.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onKeydown($event)' },
})
export class ScheduleSheetComponent {
  private readonly accounting = inject(AccountingService);
  private readonly router = inject(Router);
  private readonly toast = inject(AccountingToastService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private readonly destroyed = signal(false);
  private requestId = 0;

  readonly definitionId = input.required<number>();
  readonly closed = output<void>();

  readonly definition = signal<ScheduleDefinitionDetail | null>(null);
  readonly loadFailed = signal(false);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly confirm = signal<SheetConfirm>(null);
  readonly today = signal(todayIso());
  /** 調整 (proposal decision 24): the pending period whose amounts are open, the draft, and the 套用範圍 question. */
  readonly editingId = signal<number | null>(null);
  readonly editAmounts = signal<string[]>([]);
  readonly askScope = signal(false);
  readonly editLabels = computed(() =>
    (this.definition()?.template.lines ?? []).map(line => (line.kind === 'interest' ? '利息' : '每期金額')),
  );

  readonly summary = computed(() => {
    const definition = this.definition();
    return definition ? ruleSummary(definition) : '';
  });
  private readonly pending = computed(() => (this.definition()?.instances ?? []).filter(item => item.status === 'pending'));
  readonly nextPeriods = computed(() =>
    this.pending()
      .slice(0, 3)
      .map(item => ({
        id: item.id,
        date: shortDate(item.due_date),
        amount: item.totals.map(total => formatSigned(total.amount, total.currency)).join(' · '),
      })),
  );
  /** Pending periods due today or earlier: 補入帳至今天, and the N of 略過期間的 N 期. */
  readonly overdue = computed(() => this.pending().filter(item => item.due_date <= this.today()).length);
  readonly pendingCount = computed(() => this.pending().length);
  readonly canDelete = computed(() => this.definition()?.posted_count === 0);

  constructor() {
    inject(DestroyRef).onDestroy(() => this.destroyed.set(true));
    // Focus the sheet as soon as it renders (before the load answers), so Esc closes it and page shortcuts stay quiet.
    focusSheetField(this.host.nativeElement, '.sheet', this.injector);
    effect(() => {
      const id = this.definitionId();
      untracked(() => this.load(id));
    });
  }

  /**
   * Keeps keyboard focus inside the sheet (so Esc and ⏎ reach `onKeydown`, not the page): once it has rendered, when
   * focus is outside it (first load, or a button that vanished after an action), the first button — else the sheet.
   */
  private keepFocusInside(): void {
    afterNextRender(
      () => {
        const sheet = this.host.nativeElement.querySelector<HTMLElement>('.sheet');
        if (sheet && !sheet.contains(document.activeElement)) {
          (sheet.querySelector<HTMLElement>('button:not([disabled])') ?? sheet).focus();
        }
      },
      { injector: this.injector },
    );
  }

  private load(id: number): void {
    const request = ++this.requestId;
    this.today.set(todayIso());
    this.accounting.getScheduleDefinition(id).subscribe({
      next: definition => {
        if (request === this.requestId && !this.destroyed()) {
          this.definition.set(definition);
          this.loadFailed.set(false);
          this.keepFocusInside();
        }
      },
      error: () => {
        if (request === this.requestId && !this.destroyed()) {
          this.loadFailed.set(true);
          this.keepFocusInside();
        }
      },
    });
  }

  /**
   * One action; on success the sheet reloads (or `after` runs), and the service bump reloads the reminder centre.
   * The request is not cancelled when the sheet closes (the write and its bump still land), but a destroyed sheet
   * ignores the answer: no emit, no reload, no focus work.
   */
  private act(request: Observable<unknown>, after?: () => void): void {
    this.busy.set(true);
    this.error.set(null);
    request.subscribe({
      next: () => {
        if (this.destroyed()) {
          return;
        }
        this.busy.set(false);
        this.confirm.set(null);
        if (after) {
          after();
        } else {
          this.load(this.definitionId());
        }
      },
      error: (error: unknown) => {
        if (this.destroyed()) {
          // import_running still shows the page toast; nothing else of a closed sheet is touched.
          scheduleActionError(error, this.toast);
          return;
        }
        this.busy.set(false);
        this.error.set(scheduleActionError(error, this.toast));
        this.keepFocusInside();
      },
    });
  }

  pause(): void {
    this.act(this.accounting.pauseSchedule(this.definitionId()));
  }

  /** 繼續: with overdue paused periods the owner chooses 略過期間的 N 期 (default) or 補入帳. */
  resume(backlog?: 'skip' | 'post'): void {
    if (backlog === undefined && this.overdue() > 0) {
      this.confirm.set('resume');
      focusSheetField(this.host.nativeElement, '.resume-skip', this.injector);
      return;
    }
    this.act(this.accounting.resumeSchedule(this.definitionId(), backlog ?? 'skip'));
  }

  setMode(mode: SchedulePostingMode): void {
    if (this.definition()?.posting_mode !== mode) {
      this.act(this.accounting.setScheduleMode(this.definitionId(), mode));
    }
  }

  catchUp(): void {
    this.act(this.accounting.catchUpSchedule(this.definitionId()));
  }

  edit(): void {
    const id = this.definitionId();
    this.closed.emit();
    void this.router.navigate(['/accounting/entry'], { queryParams: { schedule: id } });
  }

  end(): void {
    if (this.confirm() !== 'end') {
      this.confirm.set('end');
      return;
    }
    this.act(this.accounting.endSchedule(this.definitionId()));
  }

  remove(): void {
    if (this.confirm() !== 'delete') {
      this.confirm.set('delete');
      return;
    }
    this.act(this.accounting.deleteScheduleDefinition(this.definitionId()), () => this.closed.emit());
  }

  /** 調整 on a listed pending period: its amounts, one field per template line. */
  editPeriod(id: number): void {
    const period = this.pending().find(item => item.id === id);
    if (!period) {
      return;
    }
    this.confirm.set(null);
    this.error.set(null);
    this.askScope.set(false);
    this.editingId.set(id);
    this.editAmounts.set([...period.amounts]);
    focusSheetField(this.host.nativeElement, '.period-editor input', this.injector);
  }

  setEditAmount(index: number, value: string): void {
    this.editAmounts.update(amounts => amounts.map((amount, i) => (i === index ? value.trim() : amount)));
  }

  cancelEdit(): void {
    const id = this.editingId();
    this.askScope.set(false);
    this.editingId.set(null);
    if (id !== null) {
      focusSheetField(this.host.nativeElement, `.next-period[data-period-id="${id}"] .period-edit`, this.injector);
    }
  }

  /** 儲存: nothing changed → close without a request; a changed amount → ask 套用範圍 before anything is sent. */
  saveEdit(): void {
    if (this.checkedAmounts() === null) {
      return;
    }
    this.askScope.set(true);
    focusSheetField(this.host.nativeElement, '.scope-sheet .scope-this', this.injector);
  }

  /**
   * The draft amounts when they are well-formed and differ from the period's; else null, after showing
   * `金額格式不正確` (malformed) or closing the editor (unchanged, nothing sent).
   */
  private checkedAmounts(): string[] | null {
    const period = this.pending().find(item => item.id === this.editingId());
    if (!period) {
      return null;
    }
    const amounts = this.editAmounts();
    if (amounts.some(amount => !/^\d+(\.\d{1,4})?$/.test(amount))) {
      this.askScope.set(false);
      this.error.set('金額格式不正確');
      return null;
    }
    this.error.set(null);
    if (amounts.every((amount, index) => Number(amount) === Number(period.amounts[index]))) {
      this.cancelEdit();
      return null;
    }
    return amounts;
  }

  /**
   * 僅這一期 / 這一期與之後 / 全部週期 → `PUT …/instances/{id}` with `scope`; the sheet then reloads. The draft is
   * checked again (it may have changed while the question was open).
   */
  applyScope(scope: ScheduleAmountScope): void {
    const id = this.editingId();
    const amounts = id === null ? null : this.checkedAmounts();
    if (id === null || amounts === null) {
      return;
    }
    this.act(this.accounting.updateScheduleInstance(id, { amounts, scope }), () => {
      this.cancelEdit();
      this.load(this.definitionId());
    });
  }

  close(): void {
    this.closed.emit();
  }

  /**
   * Handled at the host so the page never sees the key. Esc closes, in order: the 套用範圍 question (nothing is saved),
   * the period editor, an open confirmation, the sheet. ⏎ in an amount field saves the period.
   */
  onKeydown(event: KeyboardEvent): void {
    const action = sheetKeyAction(event, this.host.nativeElement.querySelector('.sheet'));
    if (action === 'confirm') {
      if (this.editingId() !== null && !this.askScope()) {
        this.saveEdit();
      }
      return;
    }
    if (action !== 'close') {
      return;
    }
    if (this.askScope()) {
      this.askScope.set(false);
      focusSheetField(this.host.nativeElement, '.period-save', this.injector);
    } else if (this.editingId() !== null) {
      this.cancelEdit();
    } else if (this.confirm() !== null) {
      const open = this.confirm();
      this.confirm.set(null);
      // Focus back on the button that opened it (結束 / 刪除 already hold it).
      if (open === 'resume') {
        focusSheetField(this.host.nativeElement, '.sheet-resume', this.injector);
      }
    } else {
      this.closed.emit();
    }
  }
}
