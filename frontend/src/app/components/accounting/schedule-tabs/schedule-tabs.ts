import { ChangeDetectionStrategy, Component, computed, effect, input, model, untracked } from '@angular/core';

import { LedgerAccount, ScheduleIntervalUnit, SchedulePostingMode } from '../../../models/accounting.model';
import { accountLabel } from '../accounting-ui';
import { FormKind } from '../entry-form/entry-draft';
import { formatMoney, formatNumber } from '../format';
import { ScheduleRule, addMonthsIso, installmentFooter, recurringFooter, splitInstallment } from '../schedule-math';
import { SCHEDULE_TABS, ScheduleDraft, ScheduleEndMode, ScheduleTab, dayOf, ruleDayFor, tabsFor } from './schedule-draft';

const UNITS: readonly { unit: ScheduleIntervalUnit; label: string }[] = [
  { unit: 'day', label: '天' },
  { unit: 'week', label: '週' },
  { unit: 'month', label: '月' },
  { unit: 'year', label: '年' },
];

type CountField = 'every' | 'endTimes' | 'periods';

/** 每 N ≥ 1, N 次 ≥ 1, 期數 ≥ 2. */
const COUNT_MINIMUMS: Record<CountField, number> = { every: 1, endTimes: 1, periods: 2 };

/** 進階 of the entry form: 單次 (projected content: 入帳日) / 週期 / 分期 (spec "Entry form schedule tabs"). */
@Component({
  selector: 'app-schedule-tabs',
  standalone: true,
  templateUrl: './schedule-tabs.html',
  styleUrl: './schedule-tabs.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ScheduleTabsComponent {
  readonly kind = input.required<FormKind>();
  readonly entryDate = input.required<string>();
  /** The amount tile (unsigned, account currency): 分期's 總額. */
  readonly amount = input<number | null>(null);
  readonly currency = input('TWD');
  readonly accounts = input<LedgerAccount[]>([]);
  /** The entry's account: 還款帳戶's default. */
  readonly accountId = input<number | null>(null);
  readonly disabled = input(false);
  /** 編輯整個排程: the definition's own tab only (no 單次). */
  readonly definitionMode = input(false);
  /** Editing an existing entry: 單次 only (tabsFor). */
  readonly editing = input(false);
  /** Server field errors (`fieldErrors()`), shown beside the field they name. */
  readonly errors = input<Record<string, string>>({});
  readonly draft = model.required<ScheduleDraft>();

  readonly units = UNITS;
  readonly accountLabel = accountLabel;
  readonly tabs = computed(() => {
    const allowed = tabsFor(this.kind(), { editing: this.editing() && !this.definitionMode() });
    return SCHEDULE_TABS.filter(option => allowed.includes(option.tab) && !(this.definitionMode() && option.tab === 'single'));
  });
  readonly rule = computed<ScheduleRule>(() => {
    const draft = this.draft();
    return { interval_unit: draft.unit, interval_n: draft.every, anchor_date: draft.start, day_of_month: draft.dayOfMonth };
  });
  readonly split = computed(() => {
    const amount = this.amount();
    const periods = this.draft().periods;
    return amount !== null && amount > 0 && periods >= 2 ? splitInstallment(amount, periods, this.currency()) : null;
  });
  readonly perPeriodText = computed(() => {
    const typed = this.draft().perPeriod;
    const split = this.split();
    return typed || (split ? formatNumber(split.perPeriod, this.currency()) : '');
  });
  readonly totalText = computed(() => {
    const amount = this.amount();
    return amount !== null && amount > 0 ? formatMoney(amount, this.currency()) : '—';
  });
  readonly repayOptions = computed(() =>
    this.accounts().filter(account => !account.is_archived || account.id === this.draft().repayAccountId),
  );
  readonly repayValue = computed(() => this.draft().repayAccountId ?? this.accountId());
  readonly footer = computed(() => {
    const draft = this.draft();
    if (draft.tab === 'recurring') {
      return recurringFooter(
        this.rule(),
        draft.endMode === 'times' ? draft.endTimes : null,
        draft.endMode === 'date' ? draft.endDate : null,
      );
    }
    if (draft.tab === 'installment') {
      const amount = this.amount();
      return amount !== null && amount > 0
        ? installmentFooter(amount, draft.periods, draft.firstDate, this.currency())
        : '輸入總額後顯示每期金額';
    }
    return '';
  });

  constructor() {
    // 起始日 and 首次還款日 follow the entry's 日期 until the owner edits them.
    effect(() => {
      const date = this.entryDate();
      untracked(() => {
        const draft = this.draft();
        const start = draft.startTouched ? draft.start : date;
        const firstDate = draft.firstTouched ? draft.firstDate : addMonthsIso(date, 1);
        if (start !== draft.start || firstDate !== draft.firstDate) {
          const moved = { ...draft, start, firstDate };
          this.draft.set({ ...moved, dayOfMonth: ruleDayFor(moved) });
        }
      });
    });
    // A tab the record type no longer offers (分期 after switching to 轉帳) falls back to the first offered one.
    effect(() => {
      const offered = this.tabs().map(option => option.tab);
      untracked(() => {
        const draft = this.draft();
        if (offered.length > 0 && !offered.includes(draft.tab)) {
          const switched = { ...draft, tab: offered[0] };
          this.draft.set({ ...switched, dayOfMonth: ruleDayFor(switched) });
        }
      });
    });
  }

  private patch(changes: Partial<ScheduleDraft>): void {
    this.draft.update(draft => ({ ...draft, ...changes }));
  }

  /** `patch`, then the rule day re-derived for the changed draft (`ruleDayFor`). */
  private realign(changes: Partial<ScheduleDraft>): void {
    this.draft.update(draft => {
      const next = { ...draft, ...changes };
      return { ...next, dayOfMonth: ruleDayFor(next) };
    });
  }

  error(...fields: string[]): string | null {
    const errors = this.errors();
    for (const field of fields) {
      if (errors[field]) {
        return errors[field];
      }
    }
    return null;
  }

  /** Switching tabs re-derives the rule day from the active tab's date (N1), unless it is a definition's stored day. */
  selectTab(tab: ScheduleTab): void {
    if (!this.disabled()) {
      this.realign({ tab });
    }
  }

  setEvery(value: string): void {
    this.typeCount('every', value);
  }

  /** A day / week rule has no rule day; a month / year one takes 起始日's day (N1: no stale day reaches the payload). */
  setUnit(value: string): void {
    const unit = value as ScheduleIntervalUnit;
    const monthly = unit === 'month' || unit === 'year';
    this.patch({ unit, dayHydrated: false, dayOfMonth: monthly ? dayOf(this.draft().start) : null });
  }

  /**
   * An explicit 起始日 also replaces the rule day (R3): a month / year rule takes the chosen date's day (a definition's
   * hydrated day_of_month, e.g. 31, would otherwise keep moving the first occurrence); a day / week rule has none.
   */
  setStart(value: string): void {
    if (value) {
      const monthly = this.draft().unit === 'month' || this.draft().unit === 'year';
      this.patch({ start: value, startTouched: true, dayOfMonth: monthly ? dayOf(value) : null, dayHydrated: false });
    }
  }

  setEndMode(value: string): void {
    this.patch({ endMode: value as ScheduleEndMode });
  }

  setEndTimes(value: string): void {
    this.typeCount('endTimes', value);
  }

  setEndDate(value: string): void {
    if (value) {
      this.patch({ endDate: value });
    }
  }

  setMode(mode: SchedulePostingMode): void {
    this.patch({ mode });
  }

  setPeriods(value: string): void {
    this.typeCount('periods', value);
  }

  /**
   * While typing, a count reaches the draft only once it is a valid whole number (≥ its minimum); clamping here would
   * rewrite the field mid-edit (期數 12 → Backspace → 1 → 2 → typing 8 gives 28). An invalid value is ignored.
   */
  private typeCount(field: CountField, value: string): void {
    const n = Number(value);
    if (value.trim() !== '' && Number.isInteger(n) && n >= COUNT_MINIMUMS[field]) {
      this.patch({ [field]: n });
    }
  }

  /** On leaving the field, an invalid or blank count is normalised (floored, raised to its minimum) and shown. */
  commitCount(field: CountField, element: HTMLInputElement): void {
    const n = Math.floor(Number(element.value));
    const value = element.value.trim() !== '' && Number.isFinite(n) ? Math.max(COUNT_MINIMUMS[field], n) : COUNT_MINIMUMS[field];
    this.patch({ [field]: value });
    // The bound [value] may not change (the draft already held it), so write the normalised value back directly.
    element.value = String(value);
  }

  /** An explicit 首次還款日 also replaces the rule day (R3): 分期 is monthly, so it takes the chosen date's day. */
  setFirst(value: string): void {
    if (value) {
      this.patch({ firstDate: value, firstTouched: true, dayOfMonth: dayOf(value), dayHydrated: false });
    }
  }

  setPerPeriod(value: string): void {
    this.patch({ perPeriod: value.trim() });
  }

  setInterest(value: string): void {
    this.patch({ interest: value.trim() });
  }

  setRepay(value: string): void {
    this.patch({ repayAccountId: value ? Number(value) : null });
  }
}
