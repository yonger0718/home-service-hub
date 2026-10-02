import { ChildInput, EntryDetail, EntryInput, LedgerAccount, TransferInput } from '../../../models/accounting.model';
import { amountString } from '../amount-text';
import { SharedFields, sharedInput } from './entry-save';

/** Both legs of a loaded transfer, for editing. */
export interface TransferEdit {
  groupId: string;
  out: EntryDetail;
  in: EntryDetail;
}

/** The loaded leg plus its counterpart as out / in legs; null without a counterpart or a transfer group. */
export function transferEditFrom(detail: EntryDetail, counterpart: EntryDetail | null): TransferEdit | null {
  const groupId = detail.transfer_group_id;
  if (!counterpart || !groupId) {
    return null;
  }
  return detail.kind === 'transfer_out'
    ? { groupId, out: detail, in: counterpart }
    : { groupId, out: counterpart, in: detail };
}

/** Fields a transfer shares with the rest of the entry form. */
export interface TransferCommon {
  entry_date: string;
  entry_time: string | null;
  posted_date: string | null;
  name: string | null;
  merchant: string | null;
  description: string | null;
  project_id: number | null;
  tags: string[];
  reward_rule_ids: number[];
}

export interface TransferDraft {
  categoryId: number | null;
  fromAccount: LedgerAccount;
  toAccount: LedgerAccount;
  outAmount: number;
  inAmount: number;
  outFee: ChildInput | null;
  outDiscount: ChildInput | null;
  inFee: ChildInput | null;
  inDiscount: ChildInput | null;
}

/** 匯率 tile: `in / out` to 6 decimals; `1.00` for the same currency; `—` until both amounts exist. */
export function transferRateLabel(
  outAmount: number | null,
  inAmount: number | null,
  outCurrency: string,
  inCurrency: string,
): string {
  if (outCurrency === inCurrency) {
    return '1.00';
  }
  if (!outAmount || !inAmount) {
    return '—';
  }
  return (inAmount / outAmount).toFixed(6);
}

/** The entry form's shared tiles as transfer fields (a transfer offers no reward rules). */
export function transferCommonFrom(fields: SharedFields): TransferCommon {
  return { ...sharedInput(fields), reward_rule_ids: [] };
}

export function commonFromEntry(input: EntryInput): TransferCommon {
  return {
    entry_date: input.entry_date,
    entry_time: input.entry_time,
    posted_date: input.posted_date,
    name: input.name,
    merchant: input.merchant,
    description: input.description,
    project_id: input.project_id,
    tags: input.tags,
    reward_rule_ids: input.reward_rule_ids,
  };
}

export function buildTransferInput(draft: TransferDraft, common: TransferCommon): TransferInput {
  return {
    from_account_id: draft.fromAccount.id,
    to_account_id: draft.toAccount.id,
    out_amount: amountString(draft.outAmount, draft.fromAccount.currency),
    in_amount: amountString(draft.inAmount, draft.toAccount.currency),
    entry_date: common.entry_date,
    entry_time: common.entry_time,
    posted_date: common.posted_date,
    category_id: draft.categoryId,
    name: common.name,
    merchant: common.merchant,
    description: common.description,
    project_id: common.project_id,
    tags: common.tags,
    out_fee: draft.outFee,
    out_discount: draft.outDiscount,
    in_fee: draft.inFee,
    in_discount: draft.inDiscount,
    reward_rule_ids: common.reward_rule_ids,
  };
}
