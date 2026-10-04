import { ScheduleInstance } from '../../../models/accounting.model';
import { shortDate } from '../dates';
import { formatMoney, formatSigned } from '../format';

/** Pure 待完成交易 rows (spec "待完成交易 tab"); the server decides what is in the queue. */

export interface QueueLine {
  text: string;
  amount: string;
  tone: 'out' | 'in' | 'neutral';
}

export interface QueueRow {
  id: number;
  definitionId: number;
  icon: string;
  color: string;
  title: string;
  lines: QueueLine[];
  totalText: string;
  dueText: string;
  /** `已逾期 N 天`, `今天`, or null for a later date. */
  overdueText: string | null;
  overdue: boolean;
  today: boolean;
  error: string | null;
  partial: boolean;
  offerCatchUp: boolean;
  /** Unsigned, aligned with the template: the body of 重新入帳 (`repost`). */
  amounts: string[];
}

export interface QueueGroups {
  due: QueueRow[];
  upcoming: QueueRow[];
}

/** `房租 #4/12`, `Netflix #25` (unlimited). */
export function queueTitle(item: Pick<ScheduleInstance, 'definition_name' | 'seq' | 'times'>): string {
  return `${item.definition_name} #${item.seq}${item.times === null ? '' : `/${item.times}`}`;
}

function lineOf(line: ScheduleInstance['lines'][number]): QueueLine {
  const text = line.to_account_name ? `${line.account_name ?? ''} → ${line.to_account_name}` : (line.account_name ?? '');
  if (line.kind === 'transfer') {
    return { text, amount: formatMoney(Math.abs(Number(line.amount)), line.currency), tone: 'neutral' };
  }
  const amount = Number(line.amount);
  return { text, amount: formatSigned(amount, line.currency), tone: amount < 0 ? 'out' : amount > 0 ? 'in' : 'neutral' };
}

export function queueRows(items: ScheduleInstance[], today: string): QueueGroups {
  const overdueCount = new Map<number, number>();
  for (const item of items) {
    if (item.status === 'pending' && item.due_date < today) {
      overdueCount.set(item.definition_id, (overdueCount.get(item.definition_id) ?? 0) + 1);
    }
  }
  const offered = new Set<number>();
  const groups: QueueGroups = { due: [], upcoming: [] };
  for (const item of items) {
    const pending = item.status === 'pending';
    const overdue = pending && item.due_date < today;
    const offerCatchUp = overdue && (overdueCount.get(item.definition_id) ?? 0) > 1 && !offered.has(item.definition_id);
    if (offerCatchUp) {
      offered.add(item.definition_id);
    }
    const row: QueueRow = {
      id: item.id,
      definitionId: item.definition_id,
      icon: item.category_icon ?? (item.kind === 'installment' ? '💳' : '🔁'),
      color: item.category_color ?? 'var(--app-surface-soft)',
      title: queueTitle(item),
      lines: item.lines.map(lineOf),
      totalText: item.totals.map(total => formatSigned(total.amount, total.currency)).join(' · '),
      dueText: shortDate(item.due_date),
      overdueText: overdue ? `已逾期 ${item.overdue_days} 天` : pending && item.due_date === today ? '今天' : null,
      overdue,
      today: pending && item.due_date === today,
      error: item.last_error,
      partial: item.is_partial,
      offerCatchUp,
      amounts: [...item.amounts],
    };
    (item.is_partial || item.due_date <= today ? groups.due : groups.upcoming).push(row);
  }
  return groups;
}
