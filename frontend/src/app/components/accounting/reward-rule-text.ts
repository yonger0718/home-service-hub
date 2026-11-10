import {
  RewardRule, RewardRuleInput, RewardRuleState, RewardUsage, RewardWindow, RoundingMode,
} from '../../models/accounting.model';
import { slashDate } from './dates';
import { decimalText, formatMoney, formatNumber } from './format';

export { ruleLabel } from './entry-form/entry-draft';

export const RULE_STATE_LABELS: Record<RewardRuleState, string> = {
  active: '生效中',
  disabled: '已停用',
  expired: '已過期',
  upcoming: '未開始',
};

export const REWARD_ROUNDING_LABELS: Record<RoundingMode, string> = {
  keep: '不取整',
  round: '四捨五入',
  floor: '無條件捨去',
  ceil: '無條件進位',
};

/** Disabled beats expired beats upcoming; `today` is `YYYY-MM-DD` and both bounds are inclusive. */
export function ruleState(rule: Pick<RewardRule, 'is_enabled' | 'starts_on' | 'ends_on'>, today: string): RewardRuleState {
  if (!rule.is_enabled) {
    return 'disabled';
  }
  if (rule.ends_on !== null && rule.ends_on < today) {
    return 'expired';
  }
  if (rule.starts_on !== null && rule.starts_on > today) {
    return 'upcoming';
  }
  return 'active';
}

export const REWARD_WINDOW_LABELS: Record<RewardWindow, string> = {
  statement_cycle: '帳單週期',
  calendar_month: '自然月',
};

/** How the reward is earned: 「每 NT$X 一單位」 for per_unit, otherwise the rule's rate / fixed amount. */
export function methodSummary(rule: Pick<RewardRule, 'method' | 'rate' | 'fixed_amount' | 'unit_spend'>): string {
  switch (rule.method) {
    case 'per_unit':
      return rule.unit_spend === null ? '每單位' : `每 NT$${decimalText(rule.unit_spend)} 一單位`;
    case 'fixed':
      return rule.fixed_amount === null ? '固定金額' : `固定 $${decimalText(rule.fixed_amount)}`;
    default:
      return rule.rate === null ? '比例' : `${decimalText(rule.rate)}%`;
  }
}

export function postingSummary(
  rule: Pick<RewardRule, 'posting' | 'post_month_offset' | 'post_day' | 'delay_days'>,
): string {
  switch (rule.posting) {
    case 'after_window':
      return `帳單結帳後第 ${rule.post_month_offset} 月 ${rule.post_day} 日`;
    case 'after_transaction':
      return `交易後 ${rule.delay_days} 天`;
    default:
      return '手動';
  }
}

export function periodText(rule: Pick<RewardRule, 'starts_on' | 'ends_on'>): string {
  const { starts_on: start, ends_on: end } = rule;
  if (start && end) {
    return `${slashDate(start)} – ${slashDate(end)}`;
  }
  if (start) {
    return `${slashDate(start)} 起`;
  }
  if (end) {
    return `至 ${slashDate(end)}`;
  }
  return '無期限';
}

/** A reward's unit (D1a): money, or the points account it pays into. Decided by the server on each usage row. */
export type RewardUnit = { money: true } | { money: false; name: string };

export function usageUnit(usage: Pick<RewardUsage, 'reward_is_money' | 'reward_account_name'>): RewardUnit {
  return usage.reward_is_money ? { money: true } : { money: false, name: usage.reward_account_name };
}

/** `$1,236` for money (TWD: the usage payload carries no currency), `120 測試里程` for points. */
export function rewardFigure(value: string | number, unit: RewardUnit): string {
  return unit.money ? formatMoney(value, 'TWD') : `${formatNumber(value, 'TWD')} ${unit.name}`;
}

/** The rate chip beside a rule's label: `3%`, `固定 $50`, `每 NT$100 一單位`. */
export function rateChip(rule: Pick<RewardRule, 'method' | 'rate' | 'fixed_amount' | 'unit_spend'>): string {
  return methodSummary(rule);
}

/** The rule's name without a trailing copy of its rate (`玩數位 3%` → `玩數位`), so the chip never repeats it. */
export function ruleTitle(rule: Pick<RewardRule, 'name' | 'method' | 'rate' | 'fixed_amount' | 'unit_spend'>): string {
  const chip = rateChip(rule);
  const name = rule.name.trim();
  for (const suffix of [` ${chip}`, ` (${chip})`, `(${chip})`, `（${chip}）`]) {
    if (name.endsWith(suffix) && name.length > suffix.length) {
      return name.slice(0, -suffix.length).trim();
    }
  }
  return name;
}

/** `2026-11-06` → `11/6`. */
export function monthDay(iso: string): string {
  return `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
}

/** `2027-01-01` → `2027/1/1` (unlike `slashDate`, no zero padding). */
export function fullDate(iso: string): string {
  return `${iso.slice(0, 4)}/${monthDay(iso)}`;
}

/** The muted line in place of the cap bar on a non-active rule; null on an active one. */
export function mutedLine(rule: { state: RewardRuleState; starts_on: string | null }): string | null {
  switch (rule.state) {
    case 'disabled':
      return '停用中，不估算回饋';
    case 'expired':
      return '已結束，不再估算';
    case 'upcoming':
      return rule.starts_on ? `${fullDate(rule.starts_on)} 起才估算` : '尚未開始';
    default:
      return null;
  }
}

/** A usage row's window: `10/26–11/25`. */
export function windowText(usage: Pick<RewardUsage, 'window_start' | 'window_end'>): string {
  return `${monthDay(usage.window_start)}–${monthDay(usage.window_end)}`;
}

/** The writable fields of a loaded rule (drops id, account, moze_id, shared cap and ordering). */
export function ruleToInput(rule: RewardRule): RewardRuleInput {
  const decimal = (value: string | null): string | null => decimalText(value) || null;
  return {
    name: rule.name,
    method: rule.method,
    rate: decimal(rule.rate),
    fixed_amount: decimal(rule.fixed_amount),
    unit_spend: decimal(rule.unit_spend),
    window: rule.window,
    posting: rule.posting,
    delay_days: rule.delay_days,
    post_month_offset: rule.post_month_offset,
    post_day: rule.post_day,
    txn_rounding: rule.txn_rounding,
    total_rounding: rule.total_rounding,
    total_cap: decimal(rule.total_cap),
    is_basic: rule.is_basic,
    reward_account_id: rule.reward_account_id,
    reward_project_id: rule.reward_project_id,
    starts_on: rule.starts_on,
    ends_on: rule.ends_on,
    is_enabled: rule.is_enabled,
    description: rule.description,
  };
}

function conflictMessage(err: unknown): string | null {
  const e = err as { status?: number; error?: { message?: unknown; detail?: unknown } | null } | null;
  if (!e || e.status !== 409) {
    return null;
  }
  const text = e.error?.message ?? e.error?.detail;
  return typeof text === 'string' ? text : null;
}

export interface RuleReferences {
  entries: number;
  rewards: number;
  accruals: number;
}

/** A 409 `rule_referenced: {"entries": n, "rewards": m, "accruals": k}` → the counts (`accruals` 0 when an older API
 * omits it); anything else → null. */
export function parseReferenced(err: unknown): RuleReferences | null {
  const text = conflictMessage(err);
  const prefix = 'rule_referenced: ';
  if (text === null || !text.startsWith(prefix)) {
    return null;
  }
  try {
    const parsed = JSON.parse(text.slice(prefix.length)) as { entries?: unknown; rewards?: unknown; accruals?: unknown };
    const accruals = parsed.accruals === undefined ? 0 : parsed.accruals;
    return typeof parsed.entries === 'number' && typeof parsed.rewards === 'number' && typeof accruals === 'number'
      ? { entries: parsed.entries, rewards: parsed.rewards, accruals }
      : null;
  } catch {
    return null;
  }
}

/** `3 筆交易 / 2 筆回饋 / 1 筆預估回饋`, naming only the non-zero references (#294: no `0 筆交易 / 0 筆回饋`). */
export function referencedText(counts: RuleReferences): string {
  const parts: [number, string][] = [[counts.entries, '筆交易'], [counts.rewards, '筆回饋'], [counts.accruals, '筆預估回饋']];
  const named = parts.filter(([n]) => n > 0);
  return (named.length ? named : parts).map(([n, label]) => `${n} ${label}`).join(' / ');
}

/** A 409 `basic_rule_overlap: rule <id>` → the clashing rule's id; anything else → null. */
export function basicOverlapRuleId(err: unknown): number | null {
  const match = /^basic_rule_overlap: rule (\d+)/.exec(conflictMessage(err) ?? '');
  return match ? Number(match[1]) : null;
}
