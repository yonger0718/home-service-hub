import { InboxCard, buildCards } from './inbox-view';
import { InboxAccount, InboxApplyOut, InboxCounts, InboxItem, InboxOut } from './inbox.model';

/** Synthetic inbox DTOs for specs: a bank_only 全家 line by default; pass only what the test is about. */
export function makeItem(overrides: Partial<InboxItem> = {}): InboxItem {
  return {
    case_id: 1,
    case_version: 1,
    kind: 'line_unmatched',
    status: 'open',
    question: 'bank_only',
    folded: false,
    group_key: null,
    statement: { id: 10, period_end: '2026-09-30' },
    line: {
      id: 5,
      date: '2026-09-03',
      merchant: '全家便利商店',
      amount: '-120.0000',
      line_kind: 'purchase',
    },
    entry: null,
    candidates: [],
    proposal: null,
    actions: ['record', 'match_candidate', 'dismiss'],
    rule_hint: {
      merchant_pattern: '全家便利商店',
      line_kind: 'purchase',
      flow_sign: '-',
      amount: '120.0000',
      default_category_id: null,
    },
    ...overrides,
  };
}

export function makeInboxAccount(overrides: Partial<InboxAccount> = {}): InboxAccount {
  return { id: 1, name: '玉山', currency: 'TWD', is_credit: false, items: [], ...overrides };
}

export function makeInbox(accounts: InboxAccount[], counts: Partial<InboxCounts> = {}): InboxOut {
  return {
    generated_at: '2026-10-22T00:00:00Z',
    counts: {
      ask: 0,
      ask_items: 0,
      proposed: 0,
      balance_gap: 0,
      settlement: 0,
      other: 0,
      ...counts,
    },
    accounts,
  };
}

/** The first ask card the panel would build from these items (a merged card when they share a group_key). */
export function makeCard(items: InboxItem[], account: Partial<InboxAccount> = {}): InboxCard {
  return buildCards([makeInboxAccount({ ...account, items })]).ask[0];
}

/** An `/inbox/apply` answer where every listed case applied. */
export function applied(...caseIds: number[]): InboxApplyOut {
  return {
    results: caseIds.map((case_id) => ({
      case_id,
      status: 'applied',
      action_id: 900 + case_id,
      code: null,
    })),
    rule_id: null,
    rule_error: null,
  };
}

/** Text content with whitespace collapsed. */
export function text(node: Element | null | undefined): string {
  return node?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
}

/** The button in `scope` whose text is exactly `label`. */
export function button(scope: Element, label: string): HTMLButtonElement {
  return Array.from(scope.querySelectorAll<HTMLButtonElement>('button')).find(
    (node) => text(node) === label,
  )!;
}

/** A tile of an `app-category-picker` grid by its name (the text after its icon). */
export function categoryTile(scope: Element, name: string): HTMLButtonElement {
  return Array.from(scope.querySelectorAll<HTMLButtonElement>('app-category-picker .cat')).find(
    (node) => node.lastChild?.textContent?.trim() === name,
  )!;
}
