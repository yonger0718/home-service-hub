import { Observable } from 'rxjs';

import {
  EntryDetail,
  EntryInput,
  LedgerAccount,
  SplitInput,
  SplitMemberInput,
  SplitResult,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { currencyDecimals } from '../format';
import { evalOrNull, isWritableKind, rulesForDate } from './entry-draft';
import { buildEntryInput } from './entry-save';
import { ChildDraft, ParentDraft, normalizePosted } from './split-draft';

/** What the form was opened on: the single entry it updates / converts, the split group it upserts. */
export interface SaveContext {
  entryId: number | null;
  groupId: number | null;
}

/** The five save routes (spec §2.7). */
export type EntrySavePlan =
  | { kind: 'create'; input: EntryInput }
  | { kind: 'update'; id: number; input: EntryInput }
  | { kind: 'create-split'; input: SplitInput }
  | { kind: 'convert-split'; id: number; input: SplitInput }
  | { kind: 'update-split'; groupId: number; input: SplitInput };

/**
 * The child's rule ids that may be sent: rules of its own account valid on `date`, plus the rules the stored row was
 * already linked to while it stays on its original account (the server accepts those even when disabled or expired).
 */
export function permittedRuleIds(child: ChildDraft, date: string): number[] {
  const offered = new Set(
    rulesForDate(
      child.availableRules.filter(rule => rule.account_id === child.accountId),
      date,
    ).map(rule => rule.id),
  );
  if (child.loaded?.originalAccountId === child.accountId) {
    for (const id of child.loaded.attachedRuleIds) {
      offered.add(id);
    }
  }
  return child.ruleIds.filter(id => offered.has(id));
}

/**
 * A full member payload with every field explicit. Untouched loaded dates and time are re-sent raw; the parent's are
 * used for a single entry, a new child, or once the owner changed the parent dates. An unchanged online-FX original
 * quantity is re-sent exactly (`amount` / `fx_rate` null). Throws the owner-facing message of an invalid child.
 */
export function fullChild(
  child: ChildDraft,
  parent: ParentDraft,
  accounts: readonly LedgerAccount[],
  partyId: number | null,
  single: boolean,
): EntryInput {
  if (child.protected || !isWritableKind(child.kind)) {
    throw new Error('受保護子項只可更新備註資料');
  }
  const kind = child.kind;
  const account = accounts.find(candidate => candidate.id === child.accountId);
  if (!account) {
    throw new Error('請選擇帳戶');
  }
  const original = child.loaded?.onlineFx;
  const unchangedOriginal =
    !!child.fx?.use_online &&
    !!original &&
    child.amountExpr === original.amountExpr &&
    child.fx.original_currency === original.originalCurrency;
  const amount =
    unchangedOriginal && original
      ? Number(original.originalAmount)
      : evalOrNull(child.amountExpr, currencyDecimals(child.fx?.original_currency ?? account.currency));
  if (amount === null || !Number.isFinite(amount) || amount <= 0) {
    throw new Error('請輸入有效金額');
  }
  const fx = child.fx;
  const manual = fx?.manual === 'amount' ? fx.amount : fx?.manual === 'rate' ? fx.fx_rate : null;
  if (fx && !fx.use_online && (!manual?.trim() || !Number.isFinite(Number(manual)) || Number(manual) <= 0)) {
    throw new Error('請輸入匯率或轉換後金額');
  }
  const parentDates = single || parent.dateTouched || !child.loaded;
  const dates = parentDates || !child.loaded ? parent : child.loaded;
  const input = buildEntryInput({
    kind,
    account,
    amount,
    fx,
    categoryId: child.categoryId,
    counterpartyId: partyId,
    name: child.name,
    merchant: '',
    description: child.description,
    projectId: child.projectId,
    tags: [...child.tags],
    entryDate: dates.entryDate,
    entryTime: dates.entryTime ?? '',
    postedDate: dates.postedDate ?? '',
    invoiceNumber: child.invoice.number,
    invoiceRandom: child.invoice.random,
    fee: child.fee,
    discount: child.discount,
    ruleIds: permittedRuleIds(child, dates.entryDate),
  });
  const party = kind === 'receivable' || kind === 'payable';
  return {
    ...input,
    ...(fx?.use_online ? { amount: null, fx_rate: null } : {}),
    ...(unchangedOriginal && original
      ? { original_amount: original.originalAmount, original_currency: original.originalCurrency }
      : {}),
    entry_date: dates.entryDate,
    entry_time: dates.entryTime,
    posted_date: parentDates ? normalizePosted(dates.entryDate, dates.postedDate) : dates.postedDate,
    // A split member keeps its own stored merchant (the group's is the parent's); a single entry uses the parent's.
    merchant: single ? (party ? null : parent.merchant.trim() || null) : (child.loaded?.merchant ?? null),
  };
}

/** The split body: protected children as keep members (metadata only), every other child as a full member. */
export function toSplitInput(
  children: readonly ChildDraft[],
  parent: ParentDraft,
  accounts: readonly LedgerAccount[],
  partyIds: ReadonlyMap<string, number>,
): SplitInput {
  return {
    name: parent.name.trim() || null,
    merchant: parent.merchant.trim() || null,
    description: parent.description.trim() || null,
    entry_date: parent.entryDate,
    entry_time: parent.entryTime,
    posted_date: normalizePosted(parent.entryDate, parent.postedDate),
    project_id: null,
    tags: [],
    members: children.map(child => {
      if (child.protected) {
        if (child.id === null) {
          throw new Error('受保護子項缺少記錄編號');
        }
        return {
          id: child.id,
          keep: true as const,
          client_key: child.key,
          name: child.name.trim() || null,
          project_id: child.projectId,
          tags: [...child.tags],
          description: child.description.trim() || null,
        };
      }
      const full: SplitMemberInput = {
        ...fullChild(child, parent, accounts, partyIds.get(child.key) ?? child.counterpartyId, false),
        client_key: child.key,
      };
      if (child.id !== null) {
        full.id = child.id;
      }
      return full;
    }),
  };
}

/**
 * Chooses the route: a loaded group always upserts (one member dissolves); otherwise one child is a plain create /
 * update, and two or more create a split or convert the opened entry (its anchor must be present, alone persisted).
 */
export function planEntrySave(
  children: readonly ChildDraft[],
  parent: ParentDraft,
  accounts: readonly LedgerAccount[],
  partyIds: ReadonlyMap<string, number>,
  context: SaveContext,
): EntrySavePlan {
  if (children.length === 0) {
    throw new Error('至少保留一項');
  }
  if (context.groupId !== null) {
    if (!children.some(child => child.id !== null)) {
      throw new Error('至少保留一項原有記錄');
    }
    return { kind: 'update-split', groupId: context.groupId, input: toSplitInput(children, parent, accounts, partyIds) };
  }
  if (children.length === 1) {
    const child = children[0];
    const input = fullChild(child, parent, accounts, partyIds.get(child.key) ?? child.counterpartyId, true);
    return context.entryId === null ? { kind: 'create', input } : { kind: 'update', id: context.entryId, input };
  }
  const input = toSplitInput(children, parent, accounts, partyIds);
  if (context.entryId !== null) {
    if (
      children.filter(child => child.id === context.entryId).length !== 1 ||
      children.some(child => child.id !== null && child.id !== context.entryId)
    ) {
      throw new Error('原記錄不可移除或替換');
    }
    return { kind: 'convert-split', id: context.entryId, input };
  }
  if (children.some(child => child.id !== null)) {
    throw new Error('新增多類別不可包含既有編號');
  }
  return { kind: 'create-split', input };
}

export function executeEntrySave(service: AccountingService, plan: EntrySavePlan): Observable<EntryDetail | SplitResult> {
  switch (plan.kind) {
    case 'create':
      return service.createEntry(plan.input);
    case 'update':
      return service.updateEntry(plan.id, plan.input);
    case 'create-split':
      return service.createSplit(plan.input);
    case 'update-split':
      return service.updateSplit(plan.groupId, plan.input);
    case 'convert-split':
      return service.convertEntryToSplit(plan.id, plan.input);
  }
}
