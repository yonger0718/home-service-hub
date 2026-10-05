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
  /** Kept from an edited definition (an imported 31st); the form itself never sets it. */
  dayOfMonth: number | null;
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
