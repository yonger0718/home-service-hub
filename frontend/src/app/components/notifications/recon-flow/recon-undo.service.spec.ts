import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountingService } from '../../../services/accounting.service';
import { ToastService } from '../../../services/toast.service';
import { ReconUndoService, UndoOffer } from './recon-undo.service';

const BASE = '/api/accounting/reconciliation/actions';
const RULE = '/api/accounting/reconciliation/owner-rules/77';
const RECONCILE = '/api/accounting/accounts/3/statements/10/reconcile';
const isRule = (r: { url: string }) => r.url === RULE;
const isReconcile = (r: { url: string }) => r.url === RECONCILE;

function offerOf(over: Partial<UndoOffer> = {}): UndoOffer {
  return {
    answer: '改成銀行的 −$2,096',
    caseId: 5,
    actionIds: [901],
    accountId: 3,
    statementId: 10,
    rule: null,
    ...over,
  };
}

describe('ReconUndoService', () => {
  let svc: ReconUndoService;
  let toast: ToastService;
  let http: HttpTestingController;
  let changed: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    TestBed.resetTestingModule(); // a failed verify in the previous afterEach skips Angular's own reset
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    svc = TestBed.inject(ReconUndoService);
    toast = TestBed.inject(ToastService);
    http = TestBed.inject(HttpTestingController);
    changed = vi.spyOn(TestBed.inject(AccountingService), 'markEntriesChanged');
  });

  afterEach(() => http.verify());

  const runUndo = () => toast.runAction(toast.current()!.id);

  it('offers 已處理 with a 復原 action', () => {
    svc.offer(offerOf());
    expect(toast.current()).toEqual(
      expect.objectContaining({ kind: 'done', text: '已處理：改成銀行的 −$2,096', action: expect.objectContaining({ label: '復原' }) }),
    );
  });

  it('without a rule: 復原中… while the revert runs, then reconcile, then 已復原', () => {
    svc.offer(offerOf());
    runUndo();
    expect(toast.current()).toEqual(expect.objectContaining({ kind: 'progress', text: '復原中…' }));
    const revert = http.expectOne(`${BASE}/901/revert`);
    http.expectNone(isReconcile);
    revert.flush({});
    expect(svc.reverted()).toBeNull();
    const reconcile = http.expectOne(isReconcile);
    expect(reconcile.request.method).toBe('POST');
    http.expectNone(isRule);
    reconcile.flush({});
    expect(toast.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已復原', action: null }));
    expect(svc.reverted()).toEqual({ caseId: 5, seq: 1 });
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('with a rule: revert, then switch the rule off, then reconcile; the toast names the pattern', () => {
    svc.offer(offerOf({ rule: { id: 77, pattern: '測試超市' } }));
    runUndo();
    const revert = http.expectOne(`${BASE}/901/revert`);
    http.expectNone(isRule);
    revert.flush({});
    const patch = http.expectOne(isRule);
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({ enabled: false });
    http.expectNone(isReconcile);
    patch.flush({});
    http.expectOne(isReconcile).flush({});
    expect(toast.current()).toEqual(
      expect.objectContaining({ kind: 'done', text: '已復原，並停用「測試超市」的規則', action: null }),
    );
    expect(svc.reverted()).toEqual({ caseId: 5, seq: 1 });
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('a failed rule switch does not reconcile (it would repeat the answer)', () => {
    svc.offer(offerOf({ rule: { id: 77, pattern: '測試超市' } }));
    runUndo();
    http.expectOne(`${BASE}/901/revert`).flush({});
    http.expectOne(isRule).flush('boom', { status: 500, statusText: 'Server Error' });
    http.expectNone(isReconcile);
    expect(toast.current()).toEqual(
      expect.objectContaining({
        kind: 'error',
        text: '已復原，但「測試超市」的規則沒停用，下次對帳可能又照做；請到記帳設定關掉',
        action: null,
      }),
    );
    expect(svc.reverted()).toEqual({ caseId: 5, seq: 1 });
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('a failed reconcile still bumps reverted, with its own error', () => {
    svc.offer(offerOf());
    runUndo();
    http.expectOne(`${BASE}/901/revert`).flush({});
    http.expectOne(isReconcile).flush({ detail: 'confirmed' }, { status: 409, statusText: 'Conflict' });
    expect(toast.current()).toEqual(
      expect.objectContaining({
        kind: 'error',
        text: '已復原，但帳單沒有重新對過，相關的題目可能晚一點才出現',
        action: null,
      }),
    );
    expect(svc.reverted()).toEqual({ caseId: 5, seq: 1 });
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('a failed revert: no rule switch, no reconcile', () => {
    svc.offer(offerOf({ rule: { id: 77, pattern: '測試超市' } }));
    runUndo();
    http.expectOne(`${BASE}/901/revert`).flush({ detail: 'not_revertable' }, { status: 422, statusText: 'Unprocessable' });
    http.expectNone(isRule);
    http.expectNone(isReconcile);
    expect(toast.current()).toEqual(
      expect.objectContaining({ kind: 'error', text: '復原沒成功：之後已有其他變更，無法復原', action: null }),
    );
    expect(svc.reverted()).toEqual({ caseId: 5, seq: 1 });
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('reverts newest first and stops at a failure on the second', () => {
    svc.offer(offerOf({ actionIds: [901, 902] }));
    runUndo();
    const first = http.expectOne(`${BASE}/902/revert`);
    http.expectNone(`${BASE}/901/revert`);
    first.flush({});
    http.expectOne(`${BASE}/901/revert`).flush({ detail: 'not_revertable' }, { status: 422, statusText: 'Unprocessable' });
    http.expectNone(isReconcile);
    expect(toast.current()).toEqual(
      expect.objectContaining({ kind: 'error', text: '復原沒成功：之後已有其他變更，無法復原', action: null }),
    );
    expect(svc.reverted()).toEqual({ caseId: 5, seq: 1 });
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('another revert error goes through inboxErrorMessage', () => {
    svc.offer(offerOf());
    runUndo();
    http.expectOne(`${BASE}/901/revert`).flush({ detail: 'case_closed' }, { status: 409, statusText: 'Conflict' });
    expect(toast.current()).toEqual(
      expect.objectContaining({ kind: 'error', text: '復原沒成功：已經處理過了', action: null }),
    );
  });

  it('seq grows with each undo of the same case', () => {
    for (const n of [1, 2]) {
      svc.offer(offerOf({ actionIds: [900 + n] }));
      runUndo();
      http.expectOne(`${BASE}/${900 + n}/revert`).flush({});
      http.expectOne(isReconcile).flush({});
      expect(svc.reverted()?.seq).toBe(n);
    }
  });

  it('offers no action without an action id', () => {
    svc.offer(offerOf({ actionIds: [] }));
    expect(toast.current()).toEqual(expect.objectContaining({ kind: 'done', action: null }));
  });
});
