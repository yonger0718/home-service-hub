import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { computed } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountLogoStore } from './account-logo-store';
import { AccountingService } from './accounting.service';

const A = 'a'.repeat(16);
const B = 'b'.repeat(16);
const etag = (hash: string) => `"${hash}${'0'.repeat(48)}"`;
const blobOf = (label: string) => new Blob([label], { type: 'image/png' });

describe('AccountLogoStore', () => {
  let store: AccountLogoStore;
  let http: HttpTestingController;
  let accounting: AccountingService;
  let refresh: ReturnType<typeof vi.fn>;
  let created: Map<Blob, string>;
  let revoke: ReturnType<typeof vi.fn>;
  let counter: number;

  const urlFor = (hash: string, id = 1) => `/api/accounting/accounts/${id}/logo.png?v=${hash}`;
  const flush = (req: { flush: (b: Blob, o: object) => void }, label: string, hash: string | null) =>
    req.flush(blobOf(label), { headers: hash ? { ETag: etag(hash) } : {} });

  beforeEach(() => {
    counter = 0;
    created = new Map();
    revoke = vi.fn();
    vi.stubGlobal('URL', Object.assign(URL, {
      createObjectURL: (b: Blob) => { const u = `blob:${++counter}`; created.set(b, u); return u; },
      revokeObjectURL: revoke,
    }));
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    store = TestBed.inject(AccountLogoStore);
    http = TestBed.inject(HttpTestingController);
    accounting = TestBed.inject(AccountingService);
    refresh = vi.fn();
    vi.spyOn(accounting, 'refreshAccounts').mockImplementation(refresh as () => void);
  });

  afterEach(() => {
    http.verify();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('fetches once for N slots of the same (id, hash) and exposes the object URL', () => {
    const slots = Array.from({ length: 5 }, () => store.url(1, A));
    const req = http.expectOne(urlFor(A));
    flush(req, 'a', A);
    expect(refresh).not.toHaveBeenCalled();
    expect(slots[0]()).toBe('blob:1');
    expect(slots.every(s => s === slots[0])).toBe(true);
    store.url(1, A);
    http.expectNone(urlFor(A));
  });

  it('sends the bearer-safe URL with ?v=<hash> and responseType blob', () => {
    store.url(1, A);
    const req = http.expectOne(urlFor(A));
    expect(req.request.method).toBe('GET');
    expect(req.request.responseType).toBe('blob');
    flush(req, 'a', A);
  });

  it('drops a late response from a superseded generation even when its hash matches the current one', () => {
    const sig = store.url(1, A);
    const firstA = http.expectOne(urlFor(A));
    store.url(1, B);
    const reqB = http.expectOne(urlFor(B));
    store.url(1, A);
    const secondA = http.expectOne(urlFor(A));
    flush(reqB, 'b', B);
    expect(sig()).toBeNull();
    flush(firstA, 'stale-a', A);
    expect(sig()).toBeNull();
    flush(secondA, 'a', A);
    expect(sig()).not.toBeNull();
    expect(counter).toBe(1);
  });

  it('etag mismatch is cached under the actual hash and refreshes accounts', () => {
    const sig = store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'b', B);
    expect(sig()).toBe('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);
    store.url(1, B);
    http.expectNone(urlFor(B));
    expect(sig()).toBe('blob:1');
  });

  it('mismatch B then model reload A refetches A (A→B→A end to end)', () => {
    vi.useFakeTimers();
    const sig = store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'b', B);
    expect(sig()).toBe('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);

    store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(sig()).toBe('blob:2');
    expect(revoke).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('missing etag shows the image but caches nothing', () => {
    const sig = store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'a', null);
    expect(sig()).toBe('blob:1');
    expect(refresh).not.toHaveBeenCalled();
    store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'a2', null);
    expect(sig()).toBe('blob:2');
    expect(refresh).not.toHaveBeenCalled();
  });

  it('404 marks the hash failed, exposes null, and does not retry until the hash changes', () => {
    const sig = store.url(1, A);
    http.expectOne(urlFor(A)).flush(new Blob(['nope']), { status: 404, statusText: 'Not Found' });
    expect(sig()).toBeNull();
    store.url(1, A);
    http.expectNone(urlFor(A));
    store.url(1, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    expect(sig()).not.toBeNull();
  });

  it('null hash exposes null and revokes the previous URL on the next macrotask', () => {
    vi.useFakeTimers();
    const sig = store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(sig()).toBe('blob:1');
    store.url(1, null);
    expect(sig()).toBeNull();
    expect(revoke).not.toHaveBeenCalled();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith('blob:1');
  });

  it('does not revoke a URL that is still current for the account', () => {
    vi.useFakeTimers();
    const sig = store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    store.url(1, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    expect(sig()).toBe('blob:2');
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith('blob:1');
    expect(revoke).not.toHaveBeenCalledWith('blob:2');
  });

  it('url() can be called inside a computed without throwing', () => {
    const withHash = computed(() => store.url(7, 'abc')());
    expect(() => withHash()).not.toThrow();
    expect(withHash()).toBeNull();
    flush(http.expectOne(urlFor('abc', 7)), 'x', null);
    const cleared = computed(() => store.url(7, null)());
    expect(() => cleared()).not.toThrow();
    expect(cleared()).toBeNull();
  });
});
