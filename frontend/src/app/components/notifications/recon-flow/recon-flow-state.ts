import { isAskQuestion } from '../../accounting/inbox/inbox-view';
import { InboxItem } from '../../accounting/inbox/inbox.model';
import { flowEntry } from './recon-flow-view';

export interface FlowState {
  /** Case ids in first-seen order: the opening snapshot, then any case that appeared later (a revert). */
  readonly order: readonly number[];
  /** Still-open ask items of the statement from the latest read, by case id. */
  readonly open: ReadonlyMap<number, InboxItem>;
  readonly skipped: ReadonlySet<number>;
  /** Case id on screen; null in the end state. */
  readonly cursor: number | null;
}

export function askItems(items: readonly InboxItem[], statementId: number): InboxItem[] {
  return items.filter((i) => !i.folded && isAskQuestion(i.question) && i.statement.id === statementId);
}

function toMap(items: readonly InboxItem[]): Map<number, InboxItem> {
  return new Map(items.map((i) => [i.case_id, i]));
}

/** Case ids hidden behind an earlier step: a `ledger_only` item whose entry id equals `flowEntry()` of an open non-ledger_only item. Maps follower to leader. */
export function followers(state: FlowState): ReadonlyMap<number, number> {
  const leaderByEntry = new Map<number, number>();
  for (const id of state.order) {
    const item = state.open.get(id);
    if (!item || item.question === 'ledger_only') continue;
    const entry = flowEntry(item);
    if (entry && !leaderByEntry.has(entry.id)) leaderByEntry.set(entry.id, id);
  }
  const result = new Map<number, number>();
  for (const id of state.order) {
    const item = state.open.get(id);
    if (!item || item.question !== 'ledger_only') continue;
    const leader = leaderByEntry.get(flowEntry(item)?.id ?? NaN);
    if (leader !== undefined) result.set(id, leader);
  }
  return result;
}

export function steps(state: FlowState): number[] {
  const hidden = followers(state);
  return state.order.filter((id) => state.open.has(id) && !hidden.has(id));
}

/**
 * The opening snapshot. Each follower is placed directly after its leader (the server sorts by day: a ledger_only
 * item's day is its entry date, often before the leader's bank line date), so after 不是同一筆 the still-open
 * follower is the next question, not one behind the cursor. Stable otherwise.
 */
export function startFlow(items: readonly InboxItem[]): FlowState {
  const open = toMap(items);
  const state: FlowState = {
    order: leadersFirst(items.map((i) => i.case_id), open),
    open,
    skipped: new Set(),
    cursor: null,
  };
  return { ...state, cursor: steps(state)[0] ?? null };
}

/** `ids` with each follower moved directly after its leader (followers among `ids` only); stable otherwise. */
function leadersFirst(ids: readonly number[], open: ReadonlyMap<number, InboxItem>): number[] {
  const leaderOf = followers({ order: ids, open, skipped: new Set(), cursor: null });
  const order: number[] = [];
  for (const id of ids) {
    if (leaderOf.has(id)) continue;
    order.push(id);
    for (const [follower, leader] of leaderOf) {
      if (leader === id) order.push(follower);
    }
  }
  return order;
}

/**
 * What a question is about, stable across case ids: its bank line, else its ledger entry. A 復原 followed by the
 * statement reconcile brings an answered question back under a NEW case id (the server does not recognise the
 * reopened case and writes a fresh one), so the flow finds it again by this key.
 */
export function itemKey(item: InboxItem): string | null {
  if (item.line) return `line:${item.line.id}`;
  if (item.entry) return `entry:${item.entry.id}`;
  return null;
}

/** An applied answer as the flow remembers it for 復原: its place in `order`, and the key of each case it closed. */
export interface SavedAnswer {
  readonly position: number;
  /** Old case id → `itemKey`: the answered item and the followers that rode along. */
  readonly keys: ReadonlyMap<number, string>;
}

/**
 * After the read that follows a 復原: the open items whose key matches a saved one are the questions that came back.
 * They replace the old ids (an old id is dropped only when its key came back under another id), at the answer's old
 * place, leader before follower, and the cursor goes to the first of them. Nothing came back → `state` unchanged.
 */
export function restore(state: FlowState, saved: SavedAnswer): FlowState {
  const wanted = new Set(saved.keys.values());
  const keyOf = (id: number): string | null => {
    const item = state.open.get(id);
    return item ? itemKey(item) : null;
  };
  const returned = state.order.filter((id) => {
    const key = keyOf(id);
    return key !== null && wanted.has(key);
  });
  if (returned.length === 0) return state;
  const back = new Set(returned.map(keyOf));
  const dropped = new Set(
    [...saved.keys].filter(([id, key]) => !state.open.has(id) && back.has(key)).map(([id]) => id),
  );
  const moving = new Set([...returned, ...dropped]);
  const rest = state.order.filter((id) => !moving.has(id));
  const at = state.order.slice(0, saved.position).filter((id) => !moving.has(id)).length;
  const group = leadersFirst(returned, state.open);
  const order = [...rest.slice(0, at), ...group, ...rest.slice(at)];
  const skipped = new Set([...state.skipped].filter((id) => !moving.has(id)));
  const next: FlowState = { ...state, order, skipped };
  const cursor = group.find((id) => steps(next).includes(id)) ?? state.cursor;
  return { ...next, cursor };
}

export function afterRead(state: FlowState, items: readonly InboxItem[]): FlowState {
  const open = toMap(items);
  const known = new Set(state.order);
  const order = [...state.order, ...items.map((i) => i.case_id).filter((id) => !known.has(id))];
  const skipped = new Set([...state.skipped].filter((id) => open.has(id)));
  return { order, open, skipped, cursor: state.cursor };
}

/**
 * The next open, unskipped step after the cursor's place; past the last one, wrap to the first open unskipped step
 * before it. The end state only when none is left.
 */
export function advance(state: FlowState): FlowState {
  if (state.cursor === null) return state;
  const at = state.order.indexOf(state.cursor);
  const candidates = steps(state).filter((id) => id !== state.cursor && !state.skipped.has(id));
  const next = candidates.find((id) => state.order.indexOf(id) > at) ?? candidates[0];
  return { ...state, cursor: next ?? null };
}

export function skip(state: FlowState): FlowState {
  if (state.cursor === null) return state;
  return advance({ ...state, skipped: new Set([...state.skipped, state.cursor]) });
}

export function move(state: FlowState, delta: -1 | 1): FlowState {
  if (state.cursor === null) return state;
  const at = state.order.indexOf(state.cursor);
  const list = steps(state);
  const target =
    delta === 1
      ? list.find((id) => state.order.indexOf(id) > at)
      : [...list].reverse().find((id) => state.order.indexOf(id) < at);
  return target === undefined ? state : { ...state, cursor: target };
}

export function resume(state: FlowState): FlowState {
  return { ...state, skipped: new Set(), cursor: steps(state)[0] ?? null };
}

export function goTo(state: FlowState, caseId: number): FlowState {
  return steps(state).includes(caseId) ? { ...state, cursor: caseId } : state;
}

export function progress(state: FlowState): {
  position: number;
  total: number;
  handled: number;
  remaining: number;
} {
  return {
    position: state.cursor === null ? 0 : state.order.indexOf(state.cursor) + 1,
    total: state.order.length,
    handled: state.order.filter((id) => !state.open.has(id)).length,
    remaining: state.open.size,
  };
}
