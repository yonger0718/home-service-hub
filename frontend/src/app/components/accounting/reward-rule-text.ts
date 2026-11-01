import { RewardRule, RewardRuleInput, RewardRuleState, RoundingMode } from '../../models/accounting.model';
import { slashDate } from './dates';
import { decimalText } from './format';

export { ruleLabel } from './entry-form/entry-draft';

export const RULE_STATE_LABELS: Record<RewardRuleState, string> = {
  active: '啟用中',
  disabled: '已停用',
  expired: '已到期',
  upcoming: '尚未開始',
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

/** The writable fields of a loaded rule (drops id, account, moze_id, shared cap, window and ordering). */
export function ruleToInput(rule: RewardRule): RewardRuleInput {
  const decimal = (value: string | null): string | null => decimalText(value) || null;
  return {
    name: rule.name,
    method: rule.method,
    rate: decimal(rule.rate),
    fixed_amount: decimal(rule.fixed_amount),
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

/** A 409 `rule_referenced: {"entries": n, "rewards": m}` → the counts; anything else → null. */
export function parseReferenced(err: unknown): { entries: number; rewards: number } | null {
  const text = conflictMessage(err);
  const prefix = 'rule_referenced: ';
  if (text === null || !text.startsWith(prefix)) {
    return null;
  }
  try {
    const parsed = JSON.parse(text.slice(prefix.length)) as { entries?: unknown; rewards?: unknown };
    return typeof parsed.entries === 'number' && typeof parsed.rewards === 'number'
      ? { entries: parsed.entries, rewards: parsed.rewards }
      : null;
  } catch {
    return null;
  }
}

/** A 409 `basic_rule_overlap: rule <id>` → the clashing rule's id; anything else → null. */
export function basicOverlapRuleId(err: unknown): number | null {
  const match = /^basic_rule_overlap: rule (\d+)/.exec(conflictMessage(err) ?? '');
  return match ? Number(match[1]) : null;
}
