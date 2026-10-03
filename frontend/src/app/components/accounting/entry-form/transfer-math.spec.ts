import { describe, expect, it } from 'vitest';

import { EntryDetail, LedgerAccount } from '../../../models/accounting.model';
import { emptyEntryInput } from './entry-save';
import { buildTransferInput, commonFromEntry, transferCommonFrom, transferEditFrom, transferRateLabel } from './transfer-math';

const TWD = { id: 1, name: '國泰主帳戶', currency: 'TWD' } as unknown as LedgerAccount;
const JPY = { id: 2, name: '日幣現金', currency: 'JPY' } as unknown as LedgerAccount;

describe('transfer math', () => {
  it('shows in / out to 6 decimals for a cross-currency transfer', () => {
    expect(transferRateLabel(10000, 46200, 'TWD', 'JPY')).toBe('4.620000');
  });

  it('shows 1.00 for the same currency and a dash until both amounts exist', () => {
    expect(transferRateLabel(3000, 3000, 'TWD', 'TWD')).toBe('1.00');
    expect(transferRateLabel(null, 46200, 'TWD', 'JPY')).toBe('—');
  });

  it('builds the transfer body with unsigned string amounts and per-leg children', () => {
    const common = commonFromEntry(
      emptyEntryInput({ account_id: 1, kind: 'expense', entry_date: '2026-10-02', entry_time: '09:05', name: '換日幣', tags: ['旅行'] }),
    );
    const body = buildTransferInput(
      {
        categoryId: 33,
        fromAccount: TWD,
        toAccount: JPY,
        outAmount: 10000,
        inAmount: 46200,
        outFee: { amount: '15', name: '手續費' },
        outDiscount: null,
        inFee: null,
        inDiscount: null,
      },
      common,
    );
    expect(body).toEqual({
      from_account_id: 1,
      to_account_id: 2,
      out_amount: '10000',
      in_amount: '46200',
      entry_date: '2026-10-02',
      entry_time: '09:05',
      posted_date: null,
      category_id: 33,
      name: '換日幣',
      merchant: null,
      description: null,
      project_id: null,
      tags: ['旅行'],
      out_fee: { amount: '15', name: '手續費' },
      out_discount: null,
      in_fee: null,
      in_discount: null,
      reward_rule_ids: [],
    });
  });
  it('takes the shared tiles as transfer fields, trimmed, with the posting date only when it differs', () => {
    expect(
      transferCommonFrom({
        entryDate: '2026-10-02',
        entryTime: '',
        postedDate: '2026-10-03',
        name: ' 換日幣 ',
        merchant: '',
        description: '機場',
        projectId: 2,
        tags: ['旅行'],
      }),
    ).toEqual({
      entry_date: '2026-10-02',
      entry_time: null,
      posted_date: '2026-10-03',
      name: '換日幣',
      merchant: null,
      description: '機場',
      project_id: 2,
      tags: ['旅行'],
      reward_rule_ids: [],
    });
  });

  it('orders the legs of a loaded transfer whichever leg was opened', () => {
    const out = { id: 7, kind: 'transfer_out', transfer_group_id: 'g-9' } as unknown as EntryDetail;
    const inn = { id: 8, kind: 'transfer_in', transfer_group_id: 'g-9' } as unknown as EntryDetail;
    expect(transferEditFrom(inn, out)).toEqual({ groupId: 'g-9', out, in: inn });
    expect(transferEditFrom(out, inn)).toEqual({ groupId: 'g-9', out, in: inn });
    expect(transferEditFrom(out, null)).toBeNull();
  });
});
