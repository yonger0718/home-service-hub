import { LedgerEntry, RewardBatch } from '../../models/accounting.model';

/**
 * #304: engine reward rows fold per posting batch (same rule, window and entry_date). Display only: every row stays
 * its own entry; the batch's `count` / `total` come from the server and cover the whole batch, not the loaded page.
 */
export function batchKey(entry: Pick<LedgerEntry, 'reward_batch'>): string | null {
  const b = entry.reward_batch;
  return b ? `b${b.rule_id}|${b.window_start}|${b.window_end}|${b.entry_date}` : null;
}

/** Batches with at least two loaded members, members in list order; a lone member renders as a plain row. */
export function loadedBatches(entries: LedgerEntry[]): Map<string, LedgerEntry[]> {
  const members = new Map<string, LedgerEntry[]>();
  for (const entry of entries) {
    const key = batchKey(entry);
    if (key) {
      members.set(key, [...(members.get(key) ?? []), entry]);
    }
  }
  for (const [key, list] of members) {
    if (list.length < 2) {
      members.delete(key);
    }
  }
  return members;
}

function shortDate(iso: string): string {
  const [, month, day] = iso.split('-');
  return `${Number(month)}/${day}`;
}

/** `3 筆 · 10/07–11/06`: the whole batch's count and its window. */
export function batchSub(batch: RewardBatch): string {
  return `${batch.count} 筆 · ${shortDate(batch.window_start)}–${shortDate(batch.window_end)}`;
}

export type PassbookItem =
  | { kind: 'entry'; key: string; entry: LedgerEntry; balance: string | null }
  | { kind: 'batch'; key: string; batch: RewardBatch; members: LedgerEntry[]; balance: string | null };

/**
 * The passbook's rows newest first with batches folded at their newest member. Every balance shown is a real one: a
 * folded row shows the balance right after its newest member (the server's running balance there), a plain row its
 * own. When rules interleave in canonical order the gap between two folded rows can differ from a batch total; that
 * is the cost of never printing a balance the account did not have (#304 review). `fold` false (a filter that may
 * keep only some of a batch's rows) lists every row as it is.
 */
export function foldPassbook(entries: LedgerEntry[], fold = true): PassbookItem[] {
  const batches = fold ? loadedBatches(entries) : new Map<string, LedgerEntry[]>();
  const items: PassbookItem[] = [];
  const seen = new Set<string>();
  for (const entry of entries) {
    const key = batchKey(entry);
    const members = key ? batches.get(key) : undefined;
    if (key && members) {
      if (!seen.has(key)) {
        seen.add(key);
        items.push({ kind: 'batch', key, batch: entry.reward_batch!, members, balance: entry.running_balance });
      }
    } else {
      items.push({ kind: 'entry', key: `e${entry.id}`, entry, balance: entry.running_balance });
    }
  }
  return items;
}
