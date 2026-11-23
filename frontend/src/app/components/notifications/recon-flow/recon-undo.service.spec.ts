import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountingService } from '../../../services/accounting.service';
import { ToastService } from '../../../services/toast.service';
import { ReconUndoService } from './recon-undo.service';

const BASE = '/api/accounting/reconciliation/actions';

describe('ReconUndoService', () => {
  let svc: ReconUndoService;
  let toast: ToastService;
  let http: HttpTestingController;
  let changed: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    svc = TestBed.inject(ReconUndoService);
    toast = TestBed.inject(ToastService);
    http = TestBed.inject(HttpTestingController);
    changed = vi.spyOn(TestBed.inject(AccountingService), 'markEntriesChanged');
  });

  const runUndo = () => toast.runAction(toast.current()!.id);

  it('offers 已處理 with a 復原 action', () => {
    svc.offer('改成銀行的 -$2,096', 5, [901], null);
    expect(toast.current()).toEqual(
      expect.objectContaining({ kind: 'done', text: '已處理：改成銀行的 -$2,096', action: expect.objectContaining({ label: '復原' }) }),
    );
  });

  it('reverts newest first, one request at a time, then shows 已復原 and bumps reverted', () => {
    svc.offer('x', 5, [901, 902], null);
    runUndo();
    const first = http.expectOne(`${BASE}/902/revert`);
    http.expectNone(`${BASE}/901/revert`);
    first.flush({});
    http.expectOne(`${BASE}/901/revert`).flush({});
    expect(toast.current()?.text).toBe('已復原');
    expect(svc.reverted()).toEqual({ caseId: 5, seq: 1 });
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('names the taught rule', () => {
    svc.offer('x', 5, [901], '全聯');
    runUndo();
    http.expectOne(`${BASE}/901/revert`).flush({});
    expect(toast.current()?.text).toBe('已復原。「全聯」的規則還在，可在記帳設定關掉');
  });

  it('stops at a not_revertable 422 with an error toast and no retry', () => {
    svc.offer('x', 5, [901, 902], null);
    runUndo();
    http.expectOne(`${BASE}/902/revert`).flush({ detail: 'not_revertable' }, { status: 422, statusText: 'Unprocessable' });
    http.expectNone(`${BASE}/901/revert`);
    expect(toast.current()).toEqual(
      expect.objectContaining({ kind: 'error', text: '復原沒成功：之後已有其他變更，無法復原', action: null }),
    );
    expect(svc.reverted()).toEqual({ caseId: 5, seq: 1 });
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it('seq grows with each undo of the same case', () => {
    for (const n of [1, 2]) {
      svc.offer('x', 5, [900 + n], null);
      runUndo();
      http.expectOne(`${BASE}/${900 + n}/revert`).flush({});
      expect(svc.reverted()?.seq).toBe(n);
    }
  });

  it('offers no action without an action id', () => {
    svc.offer('x', 5, [], null);
    expect(toast.current()).toEqual(expect.objectContaining({ kind: 'done', action: null }));
  });
});
