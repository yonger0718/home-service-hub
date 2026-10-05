import { describe, expect, it } from 'vitest';

import { makeAccount, makeDefinition } from '../testing/fixtures';
import { defaultDraft } from '../schedule-tabs/schedule-draft';
import {
  ScheduleFormError,
  ScheduleFormValues,
  buildDefinitionInput,
  definitionUpdateFrom,
  draftFromDefinition,
  formCanRepresent,
  shouldCatchUp,
} from './schedule-save';
import { ScheduleDefinition } from '../../../models/accounting.model';

const PAY = makeAccount({ id: 1, name: '薪轉' });
const CARD = makeAccount({ id: 2, name: '範例卡' });
const BROKER = makeAccount({ id: 3, name: '交割' });
const YEN = makeAccount({ id: 4, name: '日幣', currency: 'JPY' });

function values(overrides: Partial<ScheduleFormValues> = {}): ScheduleFormValues {
  return {
    kind: 'expense', draft: { ...defaultDraft('2026-10-03'), tab: 'recurring', start: '2026-10-22' }, name: '', merchant: '',
    description: '', tags: [], projectId: null, categoryId: 41, categoryName: 'Netflix', account: CARD, amount: 390,
    counterpartyId: null, transfer: null, transferFrom: null, transferTo: null, repayAccount: null, entryDate: '2026-10-03',
    loanEntryId: null, ...overrides,
  };
}

describe('schedule-save', () => {
  it('builds a monthly expense definition named after the category', () => {
    const input = buildDefinitionInput(values());
    expect(input).toMatchObject({
      kind: 'recurring', name: 'Netflix', interval_unit: 'month', interval_n: 1, anchor_date: '2026-10-22', times: null,
      end_date: null, total_amount: null, posting_mode: 'auto', loan: null,
    });
    expect(input.template.lines).toEqual([
      {
        kind: 'expense', account_id: 2, to_account_id: null, to_amount: null, counterparty_id: null, category_id: 41,
        project_id: null, amount: '390', currency: 'TWD', loan_entry_id: null, name: null, merchant: null,
      },
    ]);
    const finite = buildDefinitionInput(values({ draft: { ...values().draft, endMode: 'times', endTimes: 12 } }));
    expect(finite.times).toBe(12);
  });

  it('builds a transfer line, with the in-amount only across currencies', () => {
    const transfer = {
      from_account_id: 1, to_account_id: 3, out_amount: '15000', in_amount: '15000', entry_date: '2026-10-03', entry_time: null,
      posted_date: null, category_id: 60, name: null, merchant: null, description: null, project_id: null, tags: [],
      out_fee: null, out_discount: null, in_fee: null, in_discount: null, reward_rule_ids: [],
    };
    const same = buildDefinitionInput(values({ kind: 'transfer', transfer, transferFrom: PAY, transferTo: BROKER }));
    expect(same.template.lines[0]).toMatchObject({ kind: 'transfer', account_id: 1, to_account_id: 3, amount: '15000', to_amount: null, category_id: 60 });
    const across = buildDefinitionInput(
      values({ kind: 'transfer', transfer: { ...transfer, to_account_id: 4, in_amount: '70000' }, transferFrom: PAY, transferTo: YEN }),
    );
    expect(across.template.lines[0]).toMatchObject({ to_account_id: 4, to_amount: '70000' });
  });

  it('builds a card installment with the total', () => {
    const input = buildDefinitionInput(
      values({ amount: 10000, draft: { ...defaultDraft('2026-10-03'), tab: 'installment', periods: 3 } }),
    );
    expect(input).toMatchObject({ kind: 'installment', anchor_date: '2026-11-03', times: 3, total_amount: '10000', interval_unit: 'month' });
    expect(input.template.lines.map(line => [line.kind, line.amount])).toEqual([['expense', '3333']]);
  });

  it('builds a new loan with its interest in one input', () => {
    // Spec "New loan with interest".
    const input = buildDefinitionInput(
      values({
        kind: 'payable', name: '信貸', account: PAY, amount: 300000, categoryId: 50, counterpartyId: 7, repayAccount: PAY,
        draft: { ...defaultDraft('2026-10-03'), tab: 'installment', periods: 36, firstDate: '2026-11-09', interest: '620' },
      }),
    );
    expect(input).toMatchObject({ kind: 'installment', name: '信貸', times: 36, total_amount: '300000', anchor_date: '2026-11-09' });
    expect(input.template.lines.map(line => [line.kind, line.account_id, line.amount, line.loan_entry_id])).toEqual([
      ['repayment', 1, '8333', null], ['interest', 1, '620', null],
    ]);
    expect(input.loan).toEqual({ account_id: 1, counterparty_id: 7, category_id: 50, amount: '300000', entry_date: '2026-10-03', name: '信貸' });
  });

  it('reads a formatted 每期金額 and 利息 (grouping commas, full-width digits)', () => {
    const input = buildDefinitionInput(
      values({ amount: 10000, draft: { ...defaultDraft('2026-10-03'), tab: 'installment', periods: 3, perPeriod: '3,333', interest: '１２０' } }),
    );
    expect(input.template.lines.map(line => [line.kind, line.amount])).toEqual([['expense', '3333'], ['interest', '120']]);
  });

  it('names the field of a form problem', () => {
    const tryBuild = (overrides: Partial<ScheduleFormValues>) => {
      try {
        buildDefinitionInput(values(overrides));
        return null;
      } catch (error) {
        return error instanceof ScheduleFormError ? error.field : 'other';
      }
    };
    expect(tryBuild({ kind: 'income', draft: { ...defaultDraft('2026-10-03'), tab: 'installment' } })).toBe('kind');
    expect(tryBuild({ amount: null })).toBe('amount');
    expect(tryBuild({ draft: { ...defaultDraft('2026-10-03'), tab: 'installment', perPeriod: 'abc' }, amount: 900 })).toBe('lines[0].amount');
  });

  it('prefills the form from a loan definition', () => {
    const loan = makeDefinition({
      kind: 'installment', name: '信貸 每月還款', anchor_date: '2026-11-09', times: 36, total_amount: '300000.0000',
      template: {
        lines: [
          { ...makeDefinition().template.lines[0], kind: 'repayment', account_id: 1, amount: '8333', loan_entry_id: 4021, category_id: 50 },
          { ...makeDefinition().template.lines[0], kind: 'interest', account_id: 1, amount: '620', category_id: null },
        ],
        description: null,
        tags: [],
      },
    });
    const state = draftFromDefinition(loan);
    expect([state.kind, state.amount, state.accountId, state.loanEntryId, state.name]).toEqual(['payable', '300000', 1, 4021, '信貸 每月還款']);
    // 8333 is floor(300000 ÷ 36): 每期金額 hydrates as automatic (''), so a changed 期數 / 總額 re-splits it.
    expect(state.draft).toMatchObject({ tab: 'installment', periods: 36, firstDate: '2026-11-09', perPeriod: '', interest: '620', repayAccountId: 1 });
  });

  it('keeps a stored 每期金額 that is not the automatic split', () => {
    const line = { ...makeDefinition().template.lines[0], kind: 'expense' as const, amount: '9000.0000' };
    const card = makeDefinition({
      kind: 'installment', anchor_date: '2026-11-03', times: 3, total_amount: '25000.0000',
      template: { lines: [line], description: null, tags: [] },
    });
    expect(draftFromDefinition(card).draft.perPeriod).toBe('9000.0000');
    const split = makeDefinition({ ...card, template: { lines: [{ ...line, amount: '8333.0000' }], description: null, tags: [] } });
    expect(draftFromDefinition(split).draft.perPeriod).toBe('');
  });

  it('decides the catch-up and strips kind and loan for an update', () => {
    const input = buildDefinitionInput(values({ draft: { ...values().draft, start: '2026-10-03' } }));
    expect(shouldCatchUp(input, '2026-10-03')).toBe(true);
    expect(shouldCatchUp({ ...input, posting_mode: 'confirm' }, '2026-10-03')).toBe(false);
    expect(shouldCatchUp(buildDefinitionInput(values()), '2026-10-03')).toBe(false);
    const update = definitionUpdateFrom(input);
    expect(update).not.toHaveProperty('kind');
    expect(update).not.toHaveProperty('loan');
  });

  it('tells which definitions the entry form can edit without losing lines', () => {
    const base = makeDefinition().template.lines[0];
    const withLines = (kind: 'recurring' | 'installment', lines: (typeof base)[], extra = {}) =>
      makeDefinition({ kind, total_amount: kind === 'installment' ? '300000' : null, ...extra, template: { lines, description: null, tags: [] } });
    const interest = { ...base, kind: 'interest' as const, amount: '620', category_id: null };
    expect(formCanRepresent(withLines('recurring', [base]))).toBe(true);
    expect(formCanRepresent(withLines('installment', [base, interest], { times: 36 }))).toBe(true);
    expect(formCanRepresent(withLines('recurring', [base, { ...base, amount: '120' }]))).toBe(false);
    expect(formCanRepresent(withLines('recurring', [{ ...base, kind: 'repayment', loan_entry_id: 4021 }]))).toBe(false);
    expect(formCanRepresent(withLines('installment', [{ ...base, kind: 'collection', loan_entry_id: 4021 }], { times: 36 }))).toBe(false);
    expect(formCanRepresent(withLines('installment', [{ ...base, kind: 'repayment', loan_entry_id: 4021 }, interest], { times: 36 }))).toBe(true);
    expect(formCanRepresent(withLines('recurring', [base, interest]))).toBe(false);
  });

  describe('R1: a no-op 編輯整個排程 round trip keeps every field', () => {
    const ACCOUNTS = [PAY, CARD, BROKER, YEN];
    const lineOf = makeDefinition().template.lines[0];

    /** The entry form's values exactly as hydrated from the definition (nothing touched). */
    function roundTrip(definition: ScheduleDefinition) {
      const state = draftFromDefinition(definition);
      const repay = state.draft.repayAccountId;
      const input = buildDefinitionInput(
        {
          kind: state.kind, draft: state.draft, name: state.name, merchant: state.merchant, description: state.description,
          tags: state.tags, projectId: state.projectId, categoryId: state.categoryId, categoryName: null,
          account: ACCOUNTS.find(account => account.id === state.accountId) ?? null, amount: Number(state.amount),
          counterpartyId: null, transfer: null, transferFrom: null, transferTo: null,
          repayAccount: repay === null ? null : (ACCOUNTS.find(account => account.id === repay) ?? null),
          entryDate: '2026-10-03', loanEntryId: state.loanEntryId,
        },
        { original: definition },
      );
      return definitionUpdateFrom(input);
    }

    function loadedFields(definition: ScheduleDefinition) {
      const strip = ({ account_name: _a, to_account_name: _t, category: _c, counterparty: _p, ...line }: typeof lineOf) => line;
      return {
        name: definition.name,
        template: { lines: definition.template.lines.map(strip), description: definition.template.description, tags: definition.template.tags },
        interval_unit: definition.interval_unit, interval_n: definition.interval_n, anchor_date: definition.anchor_date,
        day_of_month: definition.day_of_month, times: definition.times, end_date: definition.end_date,
        total_amount: definition.total_amount, posting_mode: definition.posting_mode,
      };
    }

    it('keeps a 3-period installment cutoff (end_date)', () => {
      const card = makeDefinition({
        kind: 'installment', name: '手機分期', anchor_date: '2026-11-03', times: 3, end_date: '2026-12-03', total_amount: '27000',
        template: { lines: [{ ...lineOf, kind: 'expense', name: '手機分期', amount: '9000' }], description: null, tags: [] },
      });
      expect(formCanRepresent(card)).toBe(true);
      expect(roundTrip(card)).toEqual(loadedFields(card));
    });

    it('N2: drops the cutoff once 期數 or 首次還款日 changed', () => {
      const card = makeDefinition({
        kind: 'installment', name: '手機分期', anchor_date: '2026-11-03', times: 3, end_date: '2026-12-03', total_amount: '27000',
        template: { lines: [{ ...lineOf, kind: 'expense', name: '手機分期', amount: '9000' }], description: null, tags: [] },
      });
      const state = draftFromDefinition(card);
      const build = (draft: typeof state.draft) =>
        buildDefinitionInput(
          {
            kind: state.kind, draft, name: state.name, merchant: '', description: '', tags: [], projectId: null,
            categoryId: state.categoryId, categoryName: null, account: CARD, amount: 27000, counterpartyId: null,
            transfer: null, transferFrom: null, transferTo: null, repayAccount: null, entryDate: '2026-10-03', loanEntryId: null,
          },
          { original: card },
        );
      expect(build(state.draft).end_date).toBe('2026-12-03');
      expect(build({ ...state.draft, periods: 4 }).end_date).toBeNull();
      expect(build({ ...state.draft, firstDate: '2026-11-10' }).end_date).toBeNull();
    });

    it('keeps the interest line category / project / merchant / name', () => {
      const loan = makeDefinition({
        kind: 'installment', name: '信貸', anchor_date: '2026-11-09', times: 36, total_amount: '300000',
        template: {
          lines: [
            { ...lineOf, kind: 'repayment', account_id: 1, amount: '8333', loan_entry_id: 4021, category_id: 50, counterparty_id: 9, name: '信貸' },
            { ...lineOf, kind: 'interest', account_id: 1, amount: '620', category_id: 61, project_id: 7, merchant: '範例銀行', name: '信貸利息' },
          ],
          description: '每月 9 日',
          tags: ['貸款'],
        },
      });
      expect(formCanRepresent(loan)).toBe(true);
      expect(roundTrip(loan)).toEqual(loadedFields(loan));
    });
  });
});
