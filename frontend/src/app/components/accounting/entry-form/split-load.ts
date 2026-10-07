import { EntryDetail, SplitGroupMember } from '../../../models/accounting.model';
import { isWritableKind } from './entry-draft';
import { feeAndDiscountFromDetail, fxFromDetail } from './entry-save';
import { ChildDraft, ParentDraft, newChild, normalizePosted } from './split-draft';

/** A loaded member as a child draft, with its stored provenance; `flag` is the group read's protection flag. */
export function childFromDetail(
  detail: EntryDetail,
  flag: Pick<SplitGroupMember, 'protected' | 'protected_reason'>,
  mainCurrency: string,
): ChildDraft {
  const children = feeAndDiscountFromDetail(detail);
  const fx = fxFromDetail(detail);
  const ruleIds = detail.rules.map(rule => rule.id);
  return {
    ...newChild(detail.kind, detail.account_id),
    id: detail.id,
    protected: flag.protected,
    protectedReason: flag.protected_reason,
    categoryId: detail.category_id,
    pendingCategoryId: detail.category_id,
    amountExpr: fx?.original_amount ?? String(Math.abs(Number(detail.amount))),
    counterpartyId: detail.counterparty_id,
    counterpartyName: detail.counterparty ?? '',
    name: detail.name ?? '',
    projectId: detail.project_id,
    tags: [...detail.tags],
    description: detail.description ?? '',
    fee: children.fee,
    discount: children.discount,
    fx,
    ruleIds,
    rulesTouched: true,
    availableRules: [...detail.rules],
    invoice: { number: detail.invoice_number ?? '', random: detail.invoice_random ?? '' },
    loaded: {
      entryDate: detail.entry_date,
      entryTime: detail.entry_time,
      postedDate: detail.posted_date,
      merchant: detail.merchant,
      attachedRuleIds: ruleIds,
      originalAccountId: detail.account_id,
      source: detail.source,
      onlineFx:
        detail.fx_source === 'fx_api' && detail.original_amount !== null && detail.original_currency !== null
          ? {
              originalAmount: detail.original_amount.replace(/^[+-]/, ''),
              originalCurrency: detail.original_currency,
              amountExpr: fx?.original_amount ?? '',
            }
          : undefined,
      signedAmount: detail.amount,
      signedBase: detail.currency === mainCurrency ? detail.amount : null,
      isSettlement: detail.is_settlement,
      kind: detail.kind,
      currency: detail.currency,
    },
  };
}

/**
 * A loaded split: children in `opened.group_members` order (never GET completion order), the opened member selected,
 * and the parent from the group row plus the first member's dates.
 */
export function splitFromDetails(
  opened: EntryDetail,
  related: EntryDetail[],
  mainCurrency: string,
): { children: ChildDraft[]; parent: ParentDraft; selected: string } {
  const details = new Map([opened, ...related].map(detail => [detail.id, detail]));
  const group = opened.group;
  if (!group || group.kind !== 'split') {
    throw new Error('不是多類別記錄');
  }
  const children = opened.group_members.map(member => {
    const detail = details.get(member.id);
    if (!detail) {
      throw new Error('子項載入不完整');
    }
    return childFromDetail(detail, member, mainCurrency);
  });
  const first = children[0]?.loaded;
  const selected = children.find(child => child.id === opened.id);
  if (!first || !selected) {
    throw new Error('子項載入不完整');
  }
  return {
    children,
    selected: selected.key,
    parent: {
      name: group.name ?? '',
      merchant: group.merchant ?? '',
      description: group.description ?? '',
      entryDate: first.entryDate,
      entryTime: first.entryTime,
      postedDate: normalizePosted(first.entryDate, first.postedDate),
      dateTouched: false,
    },
  };
}

/** What decides whether ＋ (add a child) is offered (spec §2.2). */
export interface AddMode {
  split: boolean;
  count: number;
  scheduleId: number | null;
  eventTab: string;
  editing: boolean;
  kind: string;
  convertBlockedReason: string | null;
  source: string;
  locked: boolean;
}

export function addAvailability(mode: AddMode): { visible: boolean; disabled: boolean; hint: string | null } {
  if (mode.scheduleId !== null || (!mode.editing && mode.eventTab !== 'single')) {
    return { visible: false, disabled: false, hint: null };
  }
  if (mode.locked) {
    return { visible: false, disabled: false, hint: '此記錄不能拆帳' };
  }
  if (!mode.split && (!isWritableKind(mode.kind) || mode.convertBlockedReason !== null || mode.source === 'schedule')) {
    return { visible: false, disabled: false, hint: mode.convertBlockedReason ?? '此記錄不能拆帳' };
  }
  const full = mode.count >= 50;
  return { visible: true, disabled: full, hint: full ? '最多 50 項' : null };
}
