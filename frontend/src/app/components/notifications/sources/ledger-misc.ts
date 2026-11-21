import { shortDate } from '../../accounting/dates';
import { formatMoney } from '../../accounting/format';
import { ReconciliationAction } from '../../accounting/inbox/inbox.model';
import { importReviewCount } from '../../accounting/import-review-count';
import { Counterparty, ImportRun } from '../../../models/accounting.model';
import { NotifTask, NotifTrackingRow } from '../notif-task';

const pad = (n: number): string => String(n).padStart(2, '0');

/** `2026-10-24T09:05:00` → local `10/24 09:05`. */
function localStamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function importReviewTask(run: ImportRun | null): NotifTask | null {
  if (!run) return null;
  const n = importReviewCount(run);
  if (n <= 0) return null;
  return {
    id: `import:${run.id ?? run.started_at}`,
    source: 'ledger',
    from: 'importReview',
    tier: 'confirm',
    icon: 'pi-upload',
    title: `匯入紀錄 · ${n} 筆待確認`,
    when: localStamp(run.started_at),
    meta: run.file_name,
    amount: null,
    progress: null,
    primary: { label: '查看', route: '/accounting/accounts' },
    secondary: [],
    sortKey: run.started_at,
    fingerprint: `${n}`,
  };
}

interface Side {
  totals: Map<string, number>;
  names: string[];
}

function sideText(label: string, side: Side): string | null {
  if (side.names.length === 0) return null;
  const amounts = [...side.totals].map(([currency, total]) => formatMoney(Math.abs(total), currency)).join('、');
  const shown = side.names.slice(0, 2).join('、');
  const more = side.names.length > 2 ? ` 等 ${side.names.length - 2} 位` : '';
  return `${label} ${amounts}（${shown}${more}）`;
}

export function debtsRow(counterparties: readonly Counterparty[]): NotifTrackingRow | null {
  const people = counterparties.filter((c) => (c.open_count ?? 0) > 0);
  if (people.length === 0) return null;
  const owed: Side = { totals: new Map(), names: [] };
  const owing: Side = { totals: new Map(), names: [] };
  for (const person of people) {
    let isOwed = false;
    let isOwing = false;
    for (const { currency, amount } of person.open_amounts) {
      const value = Number(amount);
      if (value > 0) {
        owed.totals.set(currency, (owed.totals.get(currency) ?? 0) + value);
        isOwed = true;
      } else if (value < 0) {
        owing.totals.set(currency, (owing.totals.get(currency) ?? 0) + value);
        isOwing = true;
      }
    }
    if (isOwed) owed.names.push(person.name);
    if (isOwing) owing.names.push(person.name);
  }
  const meta = [sideText('應收', owed), sideText('應付', owing)].filter((s) => s !== null).join('· ');
  return {
    id: 'debts',
    title: `借還款 · ${people.length} 位`,
    meta,
    count: people.length,
    link: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'debts' } },
  };
}

export function upcomingRow(trackingBills: readonly NotifTask[]): NotifTrackingRow | null {
  const n = trackingBills.length;
  if (n === 0) return null;
  const parts = trackingBills.slice(0, 3).map((t) => `${t.title} ${shortDate(t.sortKey)}`);
  if (n > 3) parts.push(`等 ${n - 3} 件`);
  return {
    id: 'upcoming',
    title: `即將到來 · ${n} 件`,
    meta: parts.join(' · '),
    count: n,
    link: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'cards' } },
  };
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

/** P8. `now` is an ISO datetime. Returns e.g. `12 件`, `50+ 件`, or null for 0. */
export function handledText(
  actions: readonly ReconciliationAction[],
  now: string,
  limit: number,
): string | null {
  const since = new Date(now).getTime() - WEEK_MS;
  const n = actions.filter(
    (a) => a.reverted_by_action_id === null && new Date(a.created_at).getTime() >= since,
  ).length;
  if (n === 0) return null;
  return n >= limit ? `${limit}+ 件` : `${n} 件`;
}
