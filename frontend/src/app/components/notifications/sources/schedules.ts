import { ScheduleInstance } from '../../../models/accounting.model';
import { daysBetween, shortDate } from '../../accounting/dates';
import { formatSigned } from '../../accounting/format';
import { NotifLink, NotifTask, NotifTier } from '../notif-task';

const STEP_LINK: NotifLink = {
  label: '逐期處理',
  route: '/accounting/reminders',
  queryParams: { tab: 'pending' },
};

function byDue(a: ScheduleInstance, b: ScheduleInstance): number {
  return a.due_date < b.due_date ? -1 : a.due_date > b.due_date ? 1 : a.seq - b.seq;
}

/** Σ totals per currency (first-seen order), each signed, joined with 、. */
function sumTotals(instances: readonly ScheduleInstance[]): string | null {
  const sums = new Map<string, number>();
  for (const i of instances) {
    for (const t of i.totals) sums.set(t.currency, (sums.get(t.currency) ?? 0) + Number(t.amount));
  }
  const parts = [...sums].map(([currency, amount]) => formatSigned(amount, currency));
  return parts.length ? parts.join('、') : null;
}

/** One task per definition with a pending instance due ≤ today, or with only posted-partial instances. */
export function scheduleTasks(queue: readonly ScheduleInstance[], today: string): NotifTask[] {
  const groups = new Map<number, ScheduleInstance[]>();
  for (const i of queue) {
    const g = groups.get(i.definition_id);
    if (g) g.push(i);
    else groups.set(i.definition_id, [i]);
  }
  const tasks: NotifTask[] = [];
  for (const [definitionId, items] of groups) {
    const open = items.filter((i) => i.status === 'pending' && i.due_date <= today).sort(byDue);
    const partial = items.filter((i) => i.is_partial && !open.includes(i)).sort(byDue);
    const n = open.length;
    if (n === 0 && partial.length === 0) continue;
    const subject = n > 0 ? open : partial;
    const oldest = subject[0];
    const name = oldest.definition_name;
    const overdue = open.filter((i) => i.due_date < today).length;
    const tier: NotifTier = overdue > 0 ? 'overdue' : n > 0 ? 'today' : 'confirm';

    const title =
      n > 1
        ? `${name} · ${n} 期未入帳`
        : n === 1 && oldest.times !== null
          ? `${name} 第 ${oldest.seq} / ${oldest.times} 期`
          : name;
    const when =
      tier === 'overdue'
        ? n > 1
          ? `最早 ${shortDate(oldest.due_date)}`
          : `${shortDate(oldest.due_date)} · 逾期 ${daysBetween(oldest.due_date, today)} 天`
        : tier === 'today'
          ? '今天'
          : '金額未入滿';
    const primary: NotifTask['primary'] =
      overdue > 1
        ? { label: '全部入帳', command: { kind: 'catchUp', definitionId, name } }
        : n > 0
          ? { label: '入帳', command: { kind: 'postInstance', instanceId: oldest.id, name } }
          : STEP_LINK;

    tasks.push({
      id: `sched:${definitionId}`,
      source: 'ledger',
      from: 'schedules',
      tier,
      icon: oldest.category_icon ?? (oldest.kind === 'installment' ? '💳' : '🔁'),
      title,
      when,
      meta: oldest.lines[0]?.account_name ?? null,
      amount: sumTotals(subject),
      progress: null,
      primary,
      secondary: n > 0 ? [STEP_LINK] : [],
      sortKey: oldest.due_date,
      fingerprint: `${n}|${oldest.due_date}`,
    });
  }
  return tasks;
}
