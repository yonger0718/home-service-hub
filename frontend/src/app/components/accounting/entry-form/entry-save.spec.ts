import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { Counterparty, EntryDetail, LedgerAccount, LedgerEntry } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import {
  EntryValues,
  NO_RELATED,
  RelatedLoad,
  buildEntryInput,
  emptyEntryInput,
  entryInputFromDetail,
  executeEntrySave,
  fxFromDetail,
  loadRelatedRows,
  planEntrySave,
  resolveCounterpartyId,
} from './entry-save';

const LUNCH = emptyEntryInput({
  account_id: 1,
  kind: 'expense',
  amount: '230',
  entry_date: '2026-10-02',
  entry_time: '12:31',
  category_id: 11,
  project_id: 4,
  name: '午餐',
  merchant: '麥當勞',
  tags: ['LinePay'],
});
const PAID_FOR_ALAN = emptyEntryInput({ account_id: 2, kind: 'receivable', amount: '180', category_id: 51, counterparty_id: 5 });

describe('entry save plan', () => {
  let http: HttpTestingController;
  let service: AccountingService;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    http = TestBed.inject(HttpTestingController);
    service = TestBed.inject(AccountingService);
  });

  afterEach(() => http.verify());

  it('posts a plain entry when there is one line', () => {
    const plan = planEntrySave(LUNCH, [], { entryId: null, groupId: null });
    expect(plan.kind).toBe('create');
    executeEntrySave(service, plan).subscribe();
    const req = http.expectOne('/api/accounting/entries');
    expect(req.request.method).toBe('POST');
    expect(req.request.body.amount).toBe('230');
    req.flush({});
  });

  it('posts a split with the expense and the 代付 receivable as members', () => {
    const plan = planEntrySave(LUNCH, [PAID_FOR_ALAN], { entryId: null, groupId: null });
    expect(plan.kind).toBe('create-split');
    executeEntrySave(service, plan).subscribe();

    const req = http.expectOne('/api/accounting/splits');
    expect(req.request.method).toBe('POST');
    const body = req.request.body;
    expect(body.name).toBe('午餐');
    expect(body.merchant).toBe('麥當勞');
    expect(body.entry_date).toBe('2026-10-02');
    expect(body.project_id).toBe(4);
    expect(body.members.map((m: { kind: string }) => m.kind)).toEqual(['expense', 'receivable']);
    expect(body.members[1]).toMatchObject({ account_id: 2, amount: '180', counterparty_id: 5, entry_date: '2026-10-02', entry_time: '12:31', project_id: 4 });
    req.flush({ group_id: 12, member_ids: [100, 101] });
  });

  it('replaces the members of an existing group', () => {
    const plan = planEntrySave(LUNCH, [PAID_FOR_ALAN], { entryId: 100, groupId: 12 });
    executeEntrySave(service, plan).subscribe();
    const req = http.expectOne('/api/accounting/splits/12');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body.members.length).toBe(2);
    req.flush({});
  });

  it('updates a single entry in place', () => {
    executeEntrySave(service, planEntrySave(LUNCH, [], { entryId: 100, groupId: null })).subscribe();
    const req = http.expectOne('/api/accounting/entries/100');
    expect(req.request.method).toBe('PUT');
    req.flush({});
  });

  it('turns a loaded member detail back into an unsigned entry input with its children', () => {
    const detail = {
      id: 101,
      kind: 'receivable',
      account_id: 2,
      amount: '-180.0000',
      currency: 'TWD',
      original_amount: null,
      original_currency: null,
      fx_rate: null,
      fx_source: null,
      entry_date: '2026-10-02',
      entry_time: '12:31:00',
      posted_date: '2026-10-02',
      category_id: 51,
      project_id: 4,
      counterparty_id: 5,
      name: '代付',
      merchant: null,
      description: null,
      tags: [],
      invoice_number: null,
      children: [{ id: 102, kind: 'fee', amount: '-15.0000', name: '手續費' } as unknown as LedgerEntry],
      rules: [],
    } as unknown as EntryDetail;
    expect(entryInputFromDetail(detail)).toMatchObject({
      account_id: 2,
      kind: 'receivable',
      amount: '180',
      entry_time: '12:31',
      counterparty_id: 5,
      fee: { amount: '15', name: '手續費' },
      discount: null,
    });
  });
  it('loads the other split members and nothing else for a split member', () => {
    const detail = {
      id: 100,
      group: { id: 12, kind: 'split' },
      group_members: [{ id: 100 }, { id: 101 }, { id: 102 }],
      transfer_counterpart: null,
      transfer_group_id: null,
    } as unknown as EntryDetail;
    let result: RelatedLoad | undefined;
    loadRelatedRows(service, detail).subscribe(value => (result = value));
    http.expectOne('/api/accounting/entries/101').flush({ id: 101 });
    http.expectOne('/api/accounting/entries/102').flush({ id: 102 });
    expect(result!.members.map(member => member.id)).toEqual([101, 102]);
    expect(result!.transfer).toBeNull();
  });

  it('loads the counterpart leg of a transfer and answers at once for a plain entry', () => {
    const leg = { id: 7, group: null, group_members: [], transfer_group_id: 'g-1', transfer_counterpart: { id: 8 } } as unknown as EntryDetail;
    let transfer: RelatedLoad | undefined;
    loadRelatedRows(service, leg).subscribe(value => (transfer = value));
    http.expectOne('/api/accounting/entries/8').flush({ id: 8 });
    expect(transfer!.transfer?.id).toBe(8);
    expect(transfer!.members).toEqual([]);

    const plain = { id: 9, group: null, group_members: [], transfer_group_id: null, transfer_counterpart: null } as unknown as EntryDetail;
    let done = false;
    let value: RelatedLoad | undefined;
    loadRelatedRows(service, plain).subscribe({ next: v => (value = v), complete: () => (done = true) });
    expect(done).toBe(true);
    expect(value).toEqual(NO_RELATED);
  });
  it('resolves a typed counterparty to a known id or creates it once', () => {
    const known = [{ id: 5, name: 'Alan', open_amounts: [] }] as Counterparty[];
    const created: Counterparty[] = [];
    const ids: number[] = [];
    resolveCounterpartyId(service, ' Alan ', known, party => created.push(party)).subscribe(id => ids.push(id));
    resolveCounterpartyId(service, 'Bob', known, party => created.push(party)).subscribe(id => ids.push(id));
    const req = http.expectOne('/api/accounting/counterparties');
    expect(req.request.body).toEqual({ name: 'Bob' });
    req.flush({ id: 9, name: 'Bob', open_amounts: [] });
    expect(ids).toEqual([5, 9]);
    expect(created.map(party => party.id)).toEqual([9]);
  });
});

describe('buildEntryInput', () => {
  const TWD = { id: 1, currency: 'TWD' } as unknown as LedgerAccount;
  const VALUES: EntryValues = {
    kind: 'expense',
    account: TWD,
    amount: 230,
    fx: null,
    entryDate: '2026-10-02',
    entryTime: '12:31',
    postedDate: '2026-10-02',
    categoryId: 11,
    projectId: 4,
    name: ' 午餐 ',
    merchant: '麥當勞',
    description: '',
    tags: ['LinePay'],
    invoiceNumber: '',
    invoiceRandom: '',
    fee: null,
    discount: null,
    ruleIds: [3],
    counterpartyId: 5,
  };

  it('trims text, drops a posting date equal to the entry date and keeps the merchant only for non-party kinds', () => {
    expect(buildEntryInput(VALUES)).toEqual(
      emptyEntryInput({
        account_id: 1,
        kind: 'expense',
        amount: '230',
        entry_date: '2026-10-02',
        entry_time: '12:31',
        category_id: 11,
        project_id: 4,
        name: '午餐',
        merchant: '麥當勞',
        tags: ['LinePay'],
        reward_rule_ids: [3],
      }),
    );
    expect(buildEntryInput({ ...VALUES, kind: 'receivable' })).toMatchObject({ merchant: null, counterparty_id: 5 });
  });

  it('sends the original amount and only the manual side of an FX entry', () => {
    const fx = { original_amount: '30', original_currency: 'USD', account_currency: 'TWD', fx_rate: '32.1', amount: '963.4', use_online: false, manual: 'amount', rate_date: null } as const;
    expect(buildEntryInput({ ...VALUES, amount: 30, fx })).toMatchObject({
      amount: '963',
      original_amount: '30',
      original_currency: 'USD',
      fx_rate: null,
    });
    expect(buildEntryInput({ ...VALUES, amount: 30, fx: { ...fx, manual: 'rate' } })).toMatchObject({ amount: null, fx_rate: '32.1' });
  });
});

describe('fxFromDetail', () => {
  it('rebuilds the FX sheet value of a foreign-currency entry and returns null otherwise', () => {
    const base = { currency: 'TWD', amount: '-963.0000', original_amount: '-30.0000', original_currency: 'USD', fx_rate: '32.1000' };
    expect(fxFromDetail({ ...base, fx_source: 'manual' } as unknown as EntryDetail)).toEqual({
      original_amount: '30',
      original_currency: 'USD',
      account_currency: 'TWD',
      fx_rate: '32.1',
      amount: '963',
      use_online: false,
      manual: 'amount',
      rate_date: null,
    });
    expect(fxFromDetail({ ...base, fx_source: 'fx_api' } as unknown as EntryDetail)).toMatchObject({ use_online: true, manual: null });
    expect(fxFromDetail({ ...base, original_currency: 'TWD' } as unknown as EntryDetail)).toBeNull();
  });
});
