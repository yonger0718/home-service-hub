import { ScheduleIntervalUnit, SchedulePostingMode } from '../../../models/accounting.model';
import { FormKind } from '../entry-form/entry-draft';
import { addMonthsIso } from '../schedule-math';

/** 進階 tabs of the entry form (MOZE 進階設定). */
export type ScheduleTab = 'single' | 'recurring' | 'installment';
export type ScheduleEndMode = 'never' | 'times' | 'date';

/** The form state of the 週期 / 分期 tabs; the entry form turns it into a definition (schedule-save.ts). */
export interface ScheduleDraft {
  tab: ScheduleTab;
  unit: ScheduleIntervalUnit;
  every: number;
  /** 起始日 (週期); follows the entry date until the owner edits it. */
  start: string;
  /**
   * True once 起始日 was set deliberately. A consumer that hydrates a draft from an existing definition must set
   * `startTouched` and `firstTouched` to true, or the entry-date effect replaces the definition's anchor / first date.
   */
  startTouched: boolean;
  /**
   * The rule day of a month / year rule; null = the anchor's day. Hydrated from an edited definition (an imported
   * 31st), else the day of the active tab's date once a date was picked (`ruleDayFor`). Never sent for day / week.
   */
  dayOfMonth: number | null;
  /** True while `dayOfMonth` is the edited definition's stored value: no date or unit was changed since (N1). */
  dayHydrated: boolean;
  endMode: ScheduleEndMode;
  endTimes: number;
  endDate: string;
  mode: SchedulePostingMode;
  /** 期數 (分期). */
  periods: number;
  /** 首次還款日 (分期); one month after the entry date until edited. */
  firstDate: string;
  /** True once 首次還款日 was set deliberately; see `startTouched` (hydrating from a definition sets both). */
  firstTouched: boolean;
  /** 每期金額 as typed; '' = the automatic floor(總額 ÷ 期數). */
  perPeriod: string;
  /** 利息 per period as typed; '' = none. */
  interest: string;
  /** 還款帳戶 (應付款項); null = the entry's account. */
  repayAccountId: number | null;
}

export const SCHEDULE_TABS: readonly { tab: ScheduleTab; label: string }[] = [
  { tab: 'single', label: '單次' },
  { tab: 'recurring', label: '週期' },
  { tab: 'installment', label: '分期' },
];

/**
 * 分期 only for 支出 and 應付款項; 轉帳, 收入 and 應收款項 get 週期; 系統 (餘額調整) stays 單次. Editing an existing
 * entry (/entries/:id/edit, also 編輯這一筆 of a schedule entry) edits that one record: 單次 only.
 */
export function tabsFor(kind: FormKind, options: { editing?: boolean } = {}): ScheduleTab[] {
  if (kind === 'system' || options.editing) {
    return ['single'];
  }
  return kind === 'expense' || kind === 'payable' ? ['single', 'recurring', 'installment'] : ['single', 'recurring'];
}

export function defaultDraft(entryDate: string): ScheduleDraft {
  return {
    tab: 'single',
    unit: 'month',
    every: 1,
    start: entryDate,
    startTouched: false,
    dayOfMonth: null,
    dayHydrated: false,
    endMode: 'never',
    endTimes: 12,
    endDate: addMonthsIso(entryDate, 12),
    mode: 'auto',
    periods: 12,
    firstDate: addMonthsIso(entryDate, 1),
    firstTouched: false,
    perPeriod: '',
    interest: '',
    repayAccountId: null,
  };
}

/** The day of month of an ISO date (YYYY-MM-DD). */
export function dayOf(iso: string): number {
  return Number(iso.slice(8, 10));
}

function monthly(unit: ScheduleIntervalUnit): boolean {
  return unit === 'month' || unit === 'year';
}

/**
 * The rule day the draft should carry (N1): a hydrated definition's day stays until its date or unit is changed; else
 * a day / week rule has none, and a set rule day follows the active tab's date (週期 → 起始日, 分期 → 首次還款日,
 * monthly). A null rule day stays null: the server then takes the anchor's day, which is the same date.
 */
export function ruleDayFor(draft: ScheduleDraft): number | null {
  if (draft.dayHydrated || draft.dayOfMonth === null) {
    return draft.dayOfMonth;
  }
  if (draft.tab === 'installment') {
    return dayOf(draft.firstDate);
  }
  return monthly(draft.unit) ? dayOf(draft.start) : null;
}

/** `day_of_month` of a recurring payload: never for a day / week rule. */
export function recurringDayOfMonth(draft: ScheduleDraft): number | null {
  return monthly(draft.unit) ? draft.dayOfMonth : null;
}
