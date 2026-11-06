import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EinvoiceTarget, InboxEinvoice } from '../einvoice.model';
import { AppEinvoiceTargetSheetComponent } from './einvoice-target-sheet';

const INVOICE = {
  id: 7,
  seller_name: '示範早餐店',
  candidates: [],
  offer_mute: false,
  state: 'unmatched',
} as unknown as InboxEinvoice;

const row = (id: number, kind: 'entry' | 'group' = 'entry'): EinvoiceTarget => ({
  kind,
  id,
  account_name: '玉山',
  date: '2026-09-03',
  amount: '120.0000',
  currency: 'TWD',
  category: null,
  name: '早餐',
});

describe('AppEinvoiceTargetSheetComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AppEinvoiceTargetSheetComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function render() {
    const fixture = TestBed.createComponent(AppEinvoiceTargetSheetComponent);
    fixture.componentRef.setInput('invoice', INVOICE);
    const picked = vi.fn();
    fixture.componentInstance.picked.subscribe(picked);
    fixture.detectChanges();
    const req = http.expectOne((r) => r.url === '/api/accounting/einvoices/7/targets');
    return { fixture, el: fixture.nativeElement as HTMLElement, picked, req };
  }

  it('shows 讀取中, then lists targets and emits picked', () => {
    const { fixture, el, picked, req } = render();
    expect(el.textContent).toContain('讀取中');
    req.flush([row(3), row(4, 'group')]);
    fixture.detectChanges();
    const rows = el.querySelectorAll<HTMLButtonElement>('.target-row');
    expect(rows.length).toBe(2);
    rows[0].click();
    expect(picked).toHaveBeenCalledWith({ entry_id: 3 });
    rows[1].click();
    expect(picked).toHaveBeenCalledWith({ group_id: 4 });
  });

  it('says so when there is nothing to link', () => {
    const { fixture, el, req } = render();
    req.flush([]);
    fixture.detectChanges();
    expect(el.textContent).toContain('沒有可連結的帳');
  });

  it('says so when the read fails', () => {
    const { fixture, el, req } = render();
    req.flush('x', { status: 500, statusText: 'err' });
    fixture.detectChanges();
    expect(el.textContent).toContain('讀取失敗');
  });
});
