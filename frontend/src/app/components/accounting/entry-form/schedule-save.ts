import {
  EntryDetail,
  LedgerAccount,
  ScheduleDefinition,
  ScheduleDefinitionInput,
  ScheduleDefinitionUpdate,
  ScheduleLine,
  ScheduleLineInput,
  ScheduleLineKind,
  TransferInput,
} from '../../../models/accounting.model';
import { amountString, parseAmountText } from '../amount-text';
import { splitInstallment } from '../schedule-math';
import { ScheduleDraft, defaultDraft } from '../schedule-tabs/schedule-draft';
import { FormKind } from './entry-draft';
import { TransferEdit } from './transfer-math';

/** A form problem found before sending; `field` is the server field the message belongs beside. */
export class ScheduleFormError extends Error {
  constructor(
    readonly field: string,
    message: string,
  ) {
    super(message);
    this.name = 'ScheduleFormError';
  }
}

/** Everything `buildDefinitionInput` reads from the entry form. */
export interface ScheduleFormValues {
  kind: FormKind;
  draft: ScheduleDraft;
  name: string;
  merchant: string;
  description: string;
  tags: string[];
  projectId: number | null;
  categoryId: number | null;
  categoryName: string | null;
  account: LedgerAccount | null;
  /** The amount tile, unsigned, in the account currency (FX is refused on schedules). */
  amount: number | null;
  counterpartyId: number | null;
  transfer: TransferInput | null;
  transferFrom: LedgerAccount | null;
  transferTo: LedgerAccount | null;
  /** 還款帳戶 (分期 on 應付款項); null = the entry's account. */
  repayAccount: LedgerAccount | null;
  entryDate: string;
  /** Definition mode of a loan: the payable the schedule already repays (no new loan is created). */
  loanEntryId: number | null;
}

/** The form fields a definition fills in definition mode (編輯整個排程). */
export interface ScheduleFormState {
  kind: FormKind;
  draft: ScheduleDraft;
  name: string;
  merchant: string;
  description: string;
  tags: string[];
  projectId: number | null;
  accountId: number;
  categoryId: number | null;
  amount: string;
  counterparty: string | null;
  transfer: TransferEdit | null;
  loanEntryId: number | null;
}

const KIND_NAMES: Partial<Record<FormKind, string>> = {
  expense: '支出',
  income: '收入',
  transfer: '轉帳',
  receivable: '應收款項',
  payable: '應付款項',
};

function line(kind: ScheduleLineKind, account: LedgerAccount, amount: string, extra: Partial<ScheduleLineInput> = {}): ScheduleLineInput {
  return {
    kind, account_id: account.id, to_account_id: null, to_amount: null, counterparty_id: null, category_id: null,
    project_id: null, amount, currency: account.currency, loan_entry_id: null, name: null, merchant: null, ...extra,
  };
}

function typed(text: string, currency: string, field: string, message: string): number {
  const value = parseAmountText(text, currency);
  if (value === null) {
    throw new ScheduleFormError(field, message);
  }
  return value;
}

function recurringLine(values: ScheduleFormValues, common: Partial<ScheduleLineInput>): ScheduleLineInput {
  if (values.kind === 'transfer') {
    const { transfer, transferFrom, transferTo } = values;
    if (!transfer || !transferFrom || !transferTo) {
      throw new ScheduleFormError('to_account_id', '請選擇轉出與轉入帳戶');
    }
    return line('transfer', transferFrom, transfer.out_amount, {
      ...common,
      to_account_id: transferTo.id,
      to_amount: transferFrom.currency === transferTo.currency ? null : transfer.in_amount,
      category_id: transfer.category_id,
    });
  }
  if (values.kind === 'system') {
    throw new ScheduleFormError('kind', '餘額調整不能排程');
  }
  const account = values.account;
  if (!account) {
    throw new ScheduleFormError('account_id', '請選擇帳戶');
  }
  if (values.amount === null || values.amount <= 0) {
    throw new ScheduleFormError('amount', '請輸入金額');
  }
  const party = values.kind === 'receivable' || values.kind === 'payable';
  return line(values.kind, account, amountString(values.amount, account.currency), {
    ...common,
    category_id: values.categoryId,
    counterparty_id: party ? values.counterpartyId : null,
  });
}

/** The `POST /schedules/definitions` body for the 週期 / 分期 tab (spec "Entry form schedule tabs"). */
export function buildDefinitionInput(values: ScheduleFormValues): ScheduleDefinitionInput {
  const { draft } = values;
  const typedName = values.name.trim();
  const name = typedName || values.categoryName || KIND_NAMES[values.kind] || '排程';
  const common: Partial<ScheduleLineInput> = {
    project_id: values.projectId,
    name: typedName || null,
    merchant: values.merchant.trim() || null,
  };
  const template = { description: values.description.trim() || null, tags: [...values.tags] };
  if (draft.tab === 'recurring') {
    return {
      kind: 'recurring',
      name,
      template: { ...template, lines: [recurringLine(values, common)] },
      interval_unit: draft.unit,
      interval_n: draft.every,
      anchor_date: draft.start,
      day_of_month: draft.dayOfMonth,
      times: draft.endMode === 'times' ? draft.endTimes : null,
      end_date: draft.endMode === 'date' ? draft.endDate : null,
      total_amount: null,
      posting_mode: draft.mode,
      loan: null,
    };
  }
  if (draft.tab !== 'installment') {
    throw new ScheduleFormError('kind', '請選擇週期或分期');
  }
  if (values.kind !== 'expense' && values.kind !== 'payable') {
    throw new ScheduleFormError('kind', '分期只適用支出與應付款項');
  }
  const account = values.account;
  if (!account) {
    throw new ScheduleFormError('account_id', '請選擇帳戶');
  }
  const total = values.amount;
  if (total === null || total <= 0) {
    throw new ScheduleFormError('total_amount', '請輸入總額');
  }
  if (draft.periods < 2) {
    throw new ScheduleFormError('times', '分期至少 2 期');
  }
  const currency = account.currency;
  const perPeriod = draft.perPeriod
    ? typed(draft.perPeriod, currency, 'lines[0].amount', '每期金額格式錯誤')
    : splitInstallment(total, draft.periods, currency).perPeriod;
  const interest = draft.interest ? typed(draft.interest, currency, 'lines[1].amount', '利息格式錯誤') : null;
  const totalText = amountString(total, currency);
  const base = {
    kind: 'installment' as const,
    name,
    interval_unit: 'month' as const,
    interval_n: 1,
    anchor_date: draft.firstDate,
    day_of_month: draft.dayOfMonth,
    times: draft.periods,
    end_date: null,
    total_amount: totalText,
    posting_mode: draft.mode,
  };
  if (values.kind === 'expense') {
    const lines = [line('expense', account, amountString(perPeriod, currency), { ...common, category_id: values.categoryId })];
    if (interest !== null) {
      lines.push(line('interest', account, amountString(interest, currency), { name: '利息' }));
    }
    return { ...base, template: { ...template, lines }, loan: null };
  }
  const repay = values.repayAccount ?? account;
  const lines = [
    line('repayment', repay, amountString(perPeriod, currency), {
      ...common,
      category_id: values.categoryId,
      loan_entry_id: values.loanEntryId,
    }),
  ];
  if (interest !== null) {
    lines.push(line('interest', repay, amountString(interest, currency), { name: '利息' }));
  }
  if (values.loanEntryId !== null) {
    return { ...base, template: { ...template, lines }, loan: null };
  }
  if (values.counterpartyId === null) {
    throw new ScheduleFormError('counterparty_id', '請輸入對象');
  }
  return {
    ...base,
    template: { ...template, lines },
    loan: {
      account_id: account.id,
      counterparty_id: values.counterpartyId,
      category_id: values.categoryId,
      amount: totalText,
      entry_date: values.entryDate,
      name: typedName || null,
    },
  };
}

/** `PUT /schedules/definitions/{id}`: everything but `kind` and `loan`. */
export function definitionUpdateFrom(input: ScheduleDefinitionInput): ScheduleDefinitionUpdate {
  const { kind: _kind, loan: _loan, ...update } = input;
  return update;
}

/** 起始日 / 首次還款日 today with 自動入帳: the form posts today's period at once (D43); a back-dated one waits. */
export function shouldCatchUp(input: ScheduleDefinitionInput, today: string): boolean {
  return input.posting_mode === 'auto' && input.anchor_date === today;
}

/** A transfer line as the two legs the transfer panel prefills from. */
export function transferEditFromLine(source: ScheduleLine): TransferEdit {
  const leg = (accountId: number, amount: string) =>
    ({ account_id: accountId, amount, category_id: source.category_id, children: [] }) as unknown as EntryDetail;
  return {
    groupId: '',
    out: leg(source.account_id, `-${source.amount}`),
    in: leg(source.to_account_id ?? source.account_id, source.to_amount ?? source.amount),
  };
}

function formKindOf(kind: ScheduleLineKind): FormKind {
  switch (kind) {
    case 'repayment':
      return 'payable';
    case 'collection':
      return 'receivable';
    case 'interest':
      return 'expense';
    default:
      return kind;
  }
}

/** The error line of a definition the entry form cannot edit without losing or changing its lines. */
export const FORM_CANNOT_REPRESENT = '此排程的格式無法在表單編輯，請改用排程管理';

/**
 * 編輯整個排程 through the entry form rebuilds the template from one form line (+ the 利息 line of 分期), so it is
 * offered only when that rebuild gives back the same lines: one main line, at most one `interest` line on the same
 * account (分期 only), a 週期 line the form can write (no `repayment` / `collection`), and a 分期 of 支出 (`expense`)
 * or of a loan (`repayment` with its payable), monthly, with its 總額. Anything else (imported multi-line templates,
 * a recurring repayment, a collection installment) is shown read-only.
 */
export function formCanRepresent(definition: ScheduleDefinition): boolean {
  const lines = definition.template.lines;
  const [first, ...rest] = lines;
  if (!first || first.kind === 'interest' || rest.length > 1 || rest.some(item => item.kind !== 'interest')) {
    return false;
  }
  const interest = rest[0] ?? null;
  if (definition.kind === 'recurring') {
    const writable: ScheduleLineKind[] = ['expense', 'income', 'receivable', 'payable', 'transfer'];
    return interest === null && writable.includes(first.kind) && !(definition.times !== null && definition.end_date !== null);
  }
  if (first.kind !== 'expense' && !(first.kind === 'repayment' && first.loan_entry_id !== null)) {
    return false;
  }
  return (
    definition.interval_unit === 'month' &&
    definition.interval_n === 1 &&
    definition.times !== null &&
    definition.total_amount !== null &&
    (interest === null || interest.account_id === first.account_id)
  );
}

/** 編輯整個排程: the entry form's fields from a definition (its first line, and the interest line of a loan). */
export function draftFromDefinition(definition: ScheduleDefinition): ScheduleFormState {
  const [first, ...rest] = definition.template.lines;
  const interest = rest.find(item => item.kind === 'interest') ?? null;
  const installment = definition.kind === 'installment';
  const base = defaultDraft(definition.anchor_date);
  const draft: ScheduleDraft = {
    ...base,
    tab: installment ? 'installment' : 'recurring',
    unit: definition.interval_unit,
    every: definition.interval_n,
    start: definition.anchor_date,
    startTouched: true,
    dayOfMonth: definition.day_of_month,
    endMode: !installment && definition.times !== null ? 'times' : definition.end_date ? 'date' : 'never',
    endTimes: definition.times ?? base.endTimes,
    endDate: definition.end_date ?? base.endDate,
    mode: definition.posting_mode,
    periods: definition.times ?? base.periods,
    firstDate: definition.anchor_date,
    firstTouched: true,
    perPeriod: first.amount,
    interest: interest?.amount ?? '',
    repayAccountId: first.kind === 'repayment' ? first.account_id : null,
  };
  const total = installment && definition.total_amount !== null ? definition.total_amount : first.amount;
  return {
    kind: formKindOf(first.kind),
    draft,
    name: definition.name,
    merchant: first.merchant ?? '',
    description: definition.template.description ?? '',
    tags: [...definition.template.tags],
    projectId: first.project_id,
    accountId: first.account_id,
    categoryId: first.category_id,
    amount: String(Number(total)),
    counterparty: first.counterparty,
    transfer: first.kind === 'transfer' ? transferEditFromLine(first) : null,
    loanEntryId: first.loan_entry_id,
  };
}
