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
  const draft: FlowState = {
    order: items.map((i) => i.case_id),
    open: toMap(items),
    skipped: new Set(),
    cursor: null,
  };
  const leaderOf = followers(draft);
  const order: number[] = [];
  for (const id of draft.order) {
    if (leaderOf.has(id)) continue;
    order.push(id);
    for (const [follower, leader] of leaderOf) {
      if (leader === id) order.push(follower);
    }
  }
  const state = { ...draft, order };
  return { ...state, cursor: steps(state)[0] ?? null };
}

export function afterRead(state: FlowState, items: readonly InboxItem[]): FlowState {
  const open = toMap(items);
  const known = new Set(state.order);
  const order = [...state.order, ...items.map((i) => i.case_id).filter((id) => !known.has(id))];
  const skipped = new Set([...state.skipped].filter((id) => open.has(id)));
  return { order, open, skipped, cursor: state.cursor };
}

export function advance(state: FlowState): FlowState {
  if (state.cursor === null) return state;
  const at = state.order.indexOf(state.cursor);
  const next = steps(state).find((id) => state.order.indexOf(id) > at && !state.skipped.has(id));
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
