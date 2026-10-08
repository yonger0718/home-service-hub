import { Observable, forkJoin, map, of, tap } from 'rxjs';

import {
  ChildInput,
  Counterparty,
  EntryDetail,
  EntryInput,
  LedgerAccount,
  LedgerEntry,
  WritableEntryKind,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { roundHalfAway } from '../amount-math';
import { currencyDecimals } from '../format';
import { FxValue } from '../fx-sheet/fx-sheet';
import { recordAmount, writeLastUse } from './entry-draft';

/**
 * Rows a record depends on, loaded in the same sequence as the record itself (plan-review round 2): the other members
 * of its split group and the counterpart leg of its transfer.
 */
export interface RelatedLoad {
  members: EntryDetail[];
  transfer: EntryDetail | null;
}

export const NO_RELATED: RelatedLoad = { members: [], transfer: null };

/** The form tiles a single entry and a transfer share, as typed. */
export interface SharedFields {
  entryDate: string;
  entryTime: string;
  postedDate: string;
  name: string;
  merchant: string;
  description: string;
  projectId: number | null;
  tags: string[];
}

/** Everything `buildEntryInput` needs from the entry form; `ruleIds` are already limited to the offered rules. */
export interface EntryValues extends SharedFields {
  kind: WritableEntryKind;
  account: LedgerAccount;
  /** Unsigned, in the entry currency (the original currency when `fx` is set). */
  amount: number;
  fx: FxValue | null;
  categoryId: number | null;
  invoiceNumber: string;
  invoiceRandom: string;
  fee: ChildInput | null;
  discount: ChildInput | null;
  ruleIds: number[];
  counterpartyId: number | null;
}

const text = (value: string) => value.trim() || null;

/** Shared tiles as API fields: trimmed text, no time when empty, a posting date only when it differs from the date. */
export function sharedInput(fields: SharedFields) {
  return {
    entry_date: fields.entryDate,
    entry_time: fields.entryTime || null,
    posted_date: fields.postedDate && fields.postedDate !== fields.entryDate ? fields.postedDate : null,
    name: text(fields.name),
    merchant: text(fields.merchant),
    description: text(fields.description),
    project_id: fields.projectId,
    tags: fields.tags,
  };
}

/**
 * The single-entry body. Receivables and payables carry a counterparty and no merchant; with FX the server converts
 * `original_amount` unless the owner fixed the converted amount (sent as `amount`) or the rate (sent as `fx_rate`).
 */
export function buildEntryInput(values: EntryValues): EntryInput {
  const party = values.kind === 'receivable' || values.kind === 'payable';
  const shared = sharedInput(values);
  const base = {
    ...shared,
    account_id: values.account.id,
    kind: values.kind,
    category_id: values.categoryId,
    merchant: party ? null : shared.merchant,
    counterparty_id: party ? values.counterpartyId : null,
    invoice_number: text(values.invoiceNumber),
    invoice_random: text(values.invoiceRandom),
    fee: values.fee,
    discount: values.discount,
    reward_rule_ids: values.ruleIds,
  };
  const fx = values.fx;
  if (!fx) {
    return { ...base, amount: String(values.amount), original_amount: null, original_currency: null, fx_rate: null };
  }
  const manualAmount =
    !fx.use_online && fx.manual === 'amount' && fx.amount
      ? String(roundHalfAway(Number(fx.amount), currencyDecimals(values.account.currency)))
      : null;
  return {
    ...base,
    amount: manualAmount,
    original_amount: String(values.amount),
    original_currency: fx.original_currency,
    fx_rate: !fx.use_online && fx.manual === 'rate' ? fx.fx_rate : null,
  };
}

/** A complete `EntryInput` with every optional field explicitly empty. */
export function emptyEntryInput(fields: Partial<EntryInput> & Pick<EntryInput, 'account_id' | 'kind'>): EntryInput {
  return {
    amount: null,
    original_amount: null,
    original_currency: null,
    fx_rate: null,
    entry_date: '',
    entry_time: null,
    posted_date: null,
    category_id: null,
    project_id: null,
    name: null,
    merchant: null,
    counterparty_id: null,
    description: null,
    tags: [],
    invoice_number: null,
    invoice_random: null,
    fee: null,
    discount: null,
    reward_rule_ids: [],
    ...fields,
  };
}

function unsigned(value: string | null): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  return String(Math.abs(Number(value)));
}

function childFrom(children: LedgerEntry[], kind: 'fee' | 'discount'): ChildInput | null {
  const matching = children.filter(child => child.kind === kind);
  if (matching.length === 0) {
    return null;
  }
  const total = matching.reduce((sum, child) => sum + Math.abs(Number(child.amount)), 0);
  return { amount: String(total), name: matching[0].name };
}

/** A loaded entry's fee / discount children as the form's sheet values. */
export function feeAndDiscountFromDetail(detail: EntryDetail): { fee: ChildInput | null; discount: ChildInput | null } {
  return { fee: childFrom(detail.children ?? [], 'fee'), discount: childFrom(detail.children ?? [], 'discount') };
}

/** The FX sheet value of a loaded foreign-currency entry; null when it is in the account currency. */
export function fxFromDetail(detail: EntryDetail): FxValue | null {
  if (!detail.original_currency || detail.original_amount === null || detail.original_currency === detail.currency) {
    return null;
  }
  const online = detail.fx_source === 'fx_api';
  return {
    original_amount: unsigned(detail.original_amount)!,
    original_currency: detail.original_currency,
    account_currency: detail.currency,
    fx_rate: detail.fx_rate === null ? null : String(Number(detail.fx_rate)),
    amount: unsigned(detail.amount),
    use_online: online,
    manual: online ? null : 'amount',
    rate_date: null,
  };
}

/** The id for a typed counterparty name: a known row, else one created now (handed to `created`). */
export function resolveCounterpartyId(
  service: AccountingService,
  typed: string,
  known: Counterparty[],
  created: (party: Counterparty) => void,
): Observable<number> {
  const name = typed.trim();
  const existing = known.find(party => party.name === name);
  if (existing) {
    return of(existing.id);
  }
  return service.createCounterparty({ name }).pipe(
    tap(created),
    map(party => party.id),
  );
}

/**
 * Best-effort removal of counterparties created inline for a write that then failed, so no orphan 對象 is left behind.
 * The server refuses (409) to delete one an entry references, so a write that committed despite the error keeps its
 * party; `removed` fires only for a row the server actually deleted.
 */
export function discardCreatedCounterparties(
  service: AccountingService,
  created: readonly Counterparty[],
  removed: (id: number) => void,
): void {
  for (const party of created) {
    service.deleteCounterparty(party.id).subscribe({ next: () => removed(party.id), error: () => undefined });
  }
}

/**
 * After a save: this device's last account / project and quick-amount history for the category, in the currency the
 * amount was typed in (the original currency when set, else the account's).
 */
export function rememberEntryUse(input: EntryInput, amount: number | null, accountCurrency: string | null): void {
  if (input.category_id === null) {
    return;
  }
  writeLastUse(input.category_id, { account_id: input.account_id, project_id: input.project_id });
  const currency = input.original_currency ?? accountCurrency;
  if (amount !== null && currency) {
    recordAmount(input.category_id, currency, amount);
  }
}

/** A loaded entry (detail shape) as an editable, unsigned `EntryInput`. */
export function entryInputFromDetail(detail: EntryDetail): EntryInput {
  return emptyEntryInput({
    account_id: detail.account_id,
    kind: detail.kind as EntryInput['kind'],
    amount: unsigned(detail.amount),
    original_amount: unsigned(detail.original_amount),
    original_currency: detail.original_currency,
    fx_rate: detail.fx_source === 'manual' ? detail.fx_rate : null,
    entry_date: detail.entry_date,
    entry_time: detail.entry_time,
    posted_date: detail.posted_date,
    category_id: detail.category_id,
    project_id: detail.project_id,
    name: detail.name,
    merchant: detail.merchant,
    counterparty_id: detail.counterparty_id,
    description: detail.description,
    tags: detail.tags ?? [],
    invoice_number: detail.invoice_number,
    invoice_random: detail.invoice_random,
    fee: childFrom(detail.children ?? [], 'fee'),
    discount: childFrom(detail.children ?? [], 'discount'),
    reward_rule_ids: (detail.rules ?? []).map(rule => rule.id),
  });
}

/**
 * The entry form's related stage: the other members of a split (each fetched for its children) and the transfer
 * counterpart's detail (`transfer_counterpart` is only a `LedgerEntry`). Always completes; a plain entry answers at once.
 */
export function loadRelatedRows(service: AccountingService, detail: EntryDetail): Observable<RelatedLoad> {
  const other = detail.transfer_counterpart;
  const memberIds =
    detail.group?.kind === 'split'
      ? (detail.group_members ?? []).map(member => member.id).filter(id => id !== detail.id)
      : [];
  return forkJoin({
    members: memberIds.length ? forkJoin(memberIds.map(id => service.getEntry(id))) : of<EntryDetail[]>([]),
    transfer: other && detail.transfer_group_id ? service.getEntry(other.id) : of<EntryDetail | null>(null),
  });
}
