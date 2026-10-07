import { WritableSignal, computed, signal } from '@angular/core';

import { ChildInput, EntryKind, LedgerAccount, RewardRule } from '../../../models/accounting.model';
import { FxValue } from '../fx-sheet/fx-sheet';
import { isWritableKind } from './entry-draft';

/**
 * One member of the record being edited (spec §2.1). A plain entry is a single child; a split has 2 to 50. `key` is
 * the child's identity for its whole life in the form (echoed as `client_key`); `generation` is bumped whenever a
 * dependency of its asynchronous defaults (account, kind, currency, date) changes, so a stale answer is dropped.
 */
export interface ChildDraft {
  key: string;
  /** Persisted member id; null for a child added in this form. */
  id: number | null;
  /** Server-protected member: only its metadata is editable (it saves as a keep member). */
  protected: boolean;
  protectedReason: string | null;
  kind: EntryKind;
  categoryId: number | null;
  amountExpr: string;
  accountId: number | null;
  counterpartyName: string;
  counterpartyId: number | null;
  name: string;
  projectId: number | null;
  tags: string[];
  description: string;
  fee: ChildInput | null;
  discount: ChildInput | null;
  fx: FxValue | null;
  ruleIds: number[];
  invoice: { number: string; random: string };
  /** What the server stored, for a persisted child: re-sent untouched unless the owner changes it. */
  loaded: {
    entryDate: string;
    entryTime: string | null;
    postedDate: string | null;
    merchant: string | null;
    attachedRuleIds: number[];
    originalAccountId: number;
    source: string;
    /** Server-signed amount in the account currency. */
    signedAmount: string;
    /** `signedAmount` when the account currency is the main currency, else null (no client conversion). */
    signedBase: string | null;
    isSettlement: boolean;
    kind: EntryKind;
    currency: string;
    /** Online-FX provenance: the exact unsigned original quantity, re-sent while the typed amount is unchanged. */
    onlineFx?: { originalAmount: string; originalCurrency: string; amountExpr: string };
  } | null;
  rulesTouched: boolean;
  pendingCategoryId: number | null;
  generation: number;
  /** Rules offered for the child's account (presentation/default data; excluded from the dirty snapshot). */
  availableRules: RewardRule[];
}

/** Group-level fields: shared date / time / posting date and the split's own name, merchant and note. */
export interface ParentDraft {
  name: string;
  merchant: string;
  description: string;
  entryDate: string;
  entryTime: string | null;
  postedDate: string | null;
  /** The owner changed the parent dates: every full member is sent with them instead of its own. */
  dateTouched: boolean;
}

/** Captured identity of the child an asynchronous result belongs to. */
export interface Owner {
  key: string;
  generation: number;
}

/** The bubble key of the parent (group) view. */
export const PARENT_KEY = 'parent';
export const MAX_CHILDREN = 50;

export function newParent(entryDate: string, entryTime: string | null): ParentDraft {
  return { name: '', merchant: '', description: '', entryDate, entryTime, postedDate: null, dateTouched: false };
}

let childSequence = 0;

/** A short unique UI key (also the wire `client_key`, 1–64 characters). Never an authentication token. */
export function childKey(source: { randomUUID?: () => string } | undefined = globalThis.crypto): string {
  if (typeof source?.randomUUID === 'function') {
    return source.randomUUID();
  }
  // Insecure contexts have no randomUUID; a process-local monotonic suffix prevents collisions.
  return `child-${Date.now().toString(36)}-${(++childSequence).toString(36)}`;
}

export function newChild(kind: EntryKind = 'expense', accountId: number | null = null): ChildDraft {
  return {
    key: childKey(),
    id: null,
    protected: false,
    protectedReason: null,
    kind,
    categoryId: null,
    amountExpr: '',
    accountId,
    counterpartyName: '',
    counterpartyId: null,
    name: '',
    projectId: null,
    tags: [],
    description: '',
    fee: null,
    discount: null,
    fx: null,
    ruleIds: [],
    invoice: { number: '', random: '' },
    loaded: null,
    rulesTouched: false,
    pendingCategoryId: null,
    generation: 0,
    availableRules: [],
  };
}

/** A posting date equal to the entry date means "none" on the wire. */
export const normalizePosted = (date: string, posted: string | null): string | null =>
  posted && posted !== date ? posted : null;

/** Copy rules treat null and whitespace-only as empty. */
export const nonempty = (value: string | null | undefined): boolean => !!value?.trim();

/**
 * Form-local store of the children, the parent and the selection. Every write to a child goes through `accept` with
 * a captured `Owner`; a removed key or a bumped generation makes the write a no-op.
 */
export class SplitDraftStore {
  readonly children = signal<ChildDraft[]>([newChild()]);
  /** A child key, or `PARENT_KEY` while the parent bubble is selected. */
  readonly selected = signal<string>('');
  readonly parent: WritableSignal<ParentDraft>;
  /** The loaded split group (upsert / dissolve target); null for a new record or a single entry. */
  readonly groupId = signal<number | null>(null);
  /** The single entry being converted into a split (its id stays). */
  readonly anchorId = signal<number | null>(null);
  /** Parent fields a split → single removal dropped (new records only; a loaded group shows dissolve notices). */
  readonly droppedNotices = signal<string[]>([]);
  readonly isSplit = computed(() => this.children().length >= 2);

  constructor(parent: ParentDraft) {
    this.parent = signal(parent);
    this.selected.set(this.children()[0].key);
  }

  current(): ChildDraft | null {
    const key = this.selected();
    return this.children().find(child => child.key === key) ?? null;
  }

  capture(): Owner | null {
    const child = this.current();
    return child ? { key: child.key, generation: child.generation } : null;
  }

  /** Applies `patch` to the owner's child unless it was removed or its generation moved on; true when applied. */
  accept(owner: Owner, patch: Partial<ChildDraft>): boolean {
    const child = this.children().find(row => row.key === owner.key);
    if (!child || child.generation !== owner.generation) {
      return false;
    }
    this.children.update(rows => rows.map(row => (row.key === owner.key ? { ...row, ...patch } : row)));
    return true;
  }

  /** A dependency changed: outstanding results for this child are now stale. */
  invalidate(key: string): void {
    this.children.update(rows => rows.map(row => (row.key === key ? { ...row, generation: row.generation + 1 } : row)));
  }

  /**
   * Changes the selection after `flush` committed the outgoing child's pending input; false (selection unchanged)
   * when the flush refused or the target does not exist. The parent exists only while there are two children.
   */
  select(key: string, flush: (owner: Owner) => boolean): boolean {
    if (key === this.selected()) {
      return true;
    }
    if (key === PARENT_KEY ? !this.isSplit() : !this.children().some(child => child.key === key)) {
      return false;
    }
    const owner = this.capture();
    if (owner && !flush(owner)) {
      return false;
    }
    this.selected.set(key);
    return true;
  }

  /**
   * ＋: a new child after the others. Its kind is the selected (else last) child's, falling back over protected /
   * non-editable kinds to the nearest editable predecessor; its account is that child's when still open.
   */
  add(accounts: readonly LedgerAccount[]): ChildDraft | null {
    const rows = this.children();
    if (rows.length >= MAX_CHILDREN) {
      return null;
    }
    const previous = this.current() ?? rows[rows.length - 1];
    const index = rows.indexOf(previous);
    const editable = rows
      .slice(0, index + 1)
      .reverse()
      .find(child => isWritableKind(child.kind));
    const kind = editable?.kind ?? 'expense';
    const accountId =
      accounts.find(account => account.id === previous.accountId && !account.is_archived)?.id ??
      accounts.find(account => !account.is_archived)?.id ??
      null;
    if (rows.length === 1 && this.groupId() === null) {
      // single → split: name / note stay on the first child; the dates become the parent's (§2.3a).
      const parent = this.parent();
      const loaded = rows[0].loaded;
      this.parent.set({
        ...parent,
        name: '',
        description: '',
        dateTouched:
          !loaded ||
          parent.entryDate !== loaded.entryDate ||
          parent.entryTime !== loaded.entryTime ||
          normalizePosted(parent.entryDate, parent.postedDate) !== normalizePosted(loaded.entryDate, loaded.postedDate),
      });
    }
    const next = newChild(kind, accountId);
    this.children.update(current => [...current, next]);
    this.selected.set(next.key);
    this.droppedNotices.set([]);
    return next;
  }

  /** Why `key` cannot be removed, or null. */
  removeReason(key: string): string | null {
    const rows = this.children();
    const child = rows.find(row => row.key === key);
    if (!child) {
      return '找不到子項';
    }
    if (rows.length === 1) {
      return '至少保留一項';
    }
    if (child.protected) {
      return '受保護子項不可移除';
    }
    if (child.id !== null && child.id === this.anchorId()) {
      return '原記錄不可移除';
    }
    if (this.groupId() !== null && !rows.some(row => row.key !== key && row.id !== null)) {
      return '至少保留一項原有記錄';
    }
    return null;
  }

  /** Removes the child (its key disappears, so its outstanding callbacks are dropped) and selects its predecessor. */
  remove(key: string): boolean {
    if (this.removeReason(key)) {
      return false;
    }
    const index = this.children().findIndex(child => child.key === key);
    const remaining = this.children().filter(child => child.key !== key);
    this.children.set(remaining);
    this.selected.set(remaining[Math.max(0, index - 1)].key);
    if (remaining.length === 1 && this.groupId() === null) {
      // An unsaved split back to one: the survivor keeps its own name / note; the parent's are dropped visibly.
      const parent = this.parent();
      this.droppedNotices.set([
        ...(nonempty(parent.name) ? [`整筆名稱「${parent.name}」不會保留`] : []),
        ...(nonempty(parent.description) ? ['整筆備註不會保留'] : []),
      ]);
      this.parent.set({
        ...parent,
        name: '',
        description: '',
        merchant: nonempty(parent.merchant) ? parent.merchant : (remaining[0].loaded?.merchant ?? ''),
      });
    }
    return true;
  }
}

/** A selected-child field with the old signal call / set / update API. */
export interface ChildView<T> {
  (): T;
  set(value: T): void;
  update(fn: (value: T) => T): void;
}

/**
 * Reads and writes `field` of the selected child (the fallback while the parent is selected). Writes go through the
 * captured owner; a financial field of a protected child is never written.
 */
export function childView<K extends keyof ChildDraft>(
  store: SplitDraftStore,
  field: K,
  fallback: ChildDraft[K],
  financial = false,
): ChildView<ChildDraft[K]> {
  const read = (() => store.current()?.[field] ?? fallback) as ChildView<ChildDraft[K]>;
  read.set = value => {
    const child = store.current();
    const owner = store.capture();
    if (!child || !owner || (financial && child.protected)) {
      return;
    }
    store.accept(owner, { [field]: value } as Partial<ChildDraft>);
  };
  read.update = fn => read.set(fn(read()));
  return read;
}

/**
 * The unsaved-input key: what the save would send. Identity bookkeeping (generation, selection), presentation data
 * (offered rules), automatic rule defaults until `rulesTouched`, and the online quote's rate / date are left out.
 */
export function draftSnapshot(
  parent: ParentDraft,
  children: readonly ChildDraft[],
  scheduleDraftKey: unknown,
  transferDraftKey: string | null,
  targetExpr: string,
): string {
  return JSON.stringify({
    parent,
    children: children.map(child => ({
      id: child.id,
      kind: child.kind,
      categoryId: child.categoryId,
      amountExpr: child.amountExpr,
      accountId: child.accountId,
      counterpartyName: child.counterpartyName,
      name: child.name,
      projectId: child.projectId,
      tags: child.tags,
      description: child.description,
      fee: child.fee,
      discount: child.discount,
      invoice: child.invoice,
      fx: child.fx?.use_online
        ? {
            original_amount: child.fx.original_amount,
            original_currency: child.fx.original_currency,
            account_currency: child.fx.account_currency,
            use_online: true,
          }
        : child.fx,
      ruleIds: child.rulesTouched ? [...child.ruleIds].sort((a, b) => a - b) : null,
    })),
    scheduleDraftKey,
    transferDraftKey,
    targetExpr,
  });
}
