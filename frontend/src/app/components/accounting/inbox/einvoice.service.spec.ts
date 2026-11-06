import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AccountingService } from '../../../services/accounting.service';
import { EinvoiceService } from './einvoice.service';

const BASE = '/api/accounting/einvoices';
const STATE = { id: 7, state: 'linked', dismiss_reason: null, linked_by: 'owner', entry_id: 3, group_id: null };

describe('EinvoiceService', () => {
  let service: EinvoiceService;
  let accounting: AccountingService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(EinvoiceService);
    accounting = TestBed.inject(AccountingService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('links to an entry or a group', () => {
    service.link(7, { entry_id: 3 }).subscribe();
    const req = http.expectOne(`${BASE}/7/link`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ entry_id: 3 });
    req.flush(STATE);
    service.link(7, { group_id: 5 }).subscribe();
    const group = http.expectOne(`${BASE}/7/link`);
    expect(group.request.body).toEqual({ group_id: 5 });
    group.flush(STATE);
  });

  it('unlinks, dismisses (with the mute flag) and undismisses', () => {
    service.unlink(7).subscribe();
    const unlink = http.expectOne(`${BASE}/7/unlink`);
    expect(unlink.request.method).toBe('POST');
    unlink.flush(STATE);
    service.dismiss(7).subscribe();
    const plain = http.expectOne(`${BASE}/7/dismiss`);
    expect(plain.request.body).toEqual({ mute_seller: false });
    plain.flush(STATE);
    service.dismiss(7, true).subscribe();
    const muted = http.expectOne(`${BASE}/7/dismiss`);
    expect(muted.request.method).toBe('POST');
    expect(muted.request.body).toEqual({ mute_seller: true });
    muted.flush(STATE);
    service.undismiss(7).subscribe();
    const undo = http.expectOne(`${BASE}/7/undismiss`);
    expect(undo.request.method).toBe('POST');
    undo.flush(STATE);
  });

  it('reads targets, the picker, the dismissed list, mutes and preferences', () => {
    service.targets(7).subscribe();
    const targets = http.expectOne(`${BASE}/7/targets?days=14`);
    expect(targets.request.method).toBe('GET');
    targets.flush([]);
    service.targets(7, 30).subscribe();
    http.expectOne(`${BASE}/7/targets?days=30`).flush([]);
    service.picker(3).subscribe();
    http.expectOne(`${BASE}/picker?entry_id=3`).flush({});
    service.dismissed().subscribe();
    expect(http.expectOne(`${BASE}/dismissed`).request.method).toBe('GET');
    service.mutes().subscribe();
    http.expectOne(`${BASE}/mutes`).flush([]);
    service.preferences().subscribe();
    http.expectOne(`${BASE}/preferences`).flush({});
  });

  it('deletes a mute by its path-encoded tax id and patches the preference', () => {
    service.deleteMute('00000001').subscribe();
    const del = http.expectOne(`${BASE}/mutes/00000001`);
    expect(del.request.method).toBe('DELETE');
    del.flush(null);
    service.deleteMute('a/b c').subscribe();
    http.expectOne(`${BASE}/mutes/a%2Fb%20c`).flush(null);
    service.setRemindUnmatched(true).subscribe();
    const patch = http.expectOne(`${BASE}/preferences`);
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({ remind_unmatched: true });
    patch.flush({});
  });

  it('every write bumps entriesChanged; a read does not', () => {
    const start = accounting.entriesChanged();
    service.targets(7).subscribe();
    http.expectOne(`${BASE}/7/targets?days=14`).flush([]);
    service.mutes().subscribe();
    http.expectOne(`${BASE}/mutes`).flush([]);
    expect(accounting.entriesChanged()).toBe(start);

    let expected = start;
    const writes: [() => ReturnType<EinvoiceService['link']>, string, typeof STATE][] = [
      [() => service.link(7, { entry_id: 3 }), `${BASE}/7/link`, STATE],
      [() => service.unlink(7), `${BASE}/7/unlink`, STATE],
      [() => service.dismiss(7), `${BASE}/7/dismiss`, STATE],
      [() => service.undismiss(7), `${BASE}/7/undismiss`, STATE],
    ];
    for (const [call, url, body] of writes) {
      call().subscribe();
      http.expectOne(url).flush(body);
      expected += 1;
      expect(accounting.entriesChanged()).toBe(expected);
    }
    service.deleteMute('1').subscribe();
    http.expectOne(`${BASE}/mutes/1`).flush(null);
    service.setRemindUnmatched(false).subscribe();
    http.expectOne(`${BASE}/preferences`).flush({});
    expect(accounting.entriesChanged()).toBe(expected + 2);
  });

  it('a failed write does not bump entriesChanged', () => {
    const start = accounting.entriesChanged();
    service.unlink(7).subscribe({ error: () => undefined });
    http.expectOne(`${BASE}/7/unlink`).flush({}, { status: 409, statusText: 'Conflict' });
    expect(accounting.entriesChanged()).toBe(start);
  });
});
