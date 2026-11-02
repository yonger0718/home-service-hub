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
  // Stubbing `URL` in place would leak into later specs of the same worker; the two methods are restored after each test.
  const originalObjectUrls = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL };
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
    Object.assign(URL, {
      createObjectURL: (b: Blob) => { const u = `blob:${++counter}`; created.set(b, u); return u; },
      revokeObjectURL: revoke,
    });
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
    Object.assign(URL, originalObjectUrls);
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
    const sig = store.url(7, A);
    flush(http.expectOne(urlFor(A, 7)), 'b', B);
    expect(sig()).toBe('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);

    store.url(7, A);
    http.expectNone(urlFor(A, 7));
    expect(sig()).toBe('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);

    store.url(7, B);
    http.expectNone(urlFor(B, 7));
    expect(sig()).toBe('blob:1');

    vi.advanceTimersByTime(30_001);
    store.url(7, A);
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(sig()).toBe('blob:2');
    vi.runOnlyPendingTimers();
    expect(revoke).toHaveBeenCalledWith('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('missing etag shows the image and is resolved under the requested hash', () => {
    const sig = store.url(7, A);
    flush(http.expectOne(urlFor(A, 7)), 'a', null);
    expect(sig()).toBe('blob:1');
    expect(refresh).not.toHaveBeenCalled();
    store.url(7, A);
    http.expectNone(urlFor(A, 7));
    expect(sig()).toBe('blob:1');
  });

  it('a computed re-reading a no-ETag slot fetches exactly once', () => {
    const c = computed(() => store.url(7, A)());
    expect(c()).toBeNull();
    flush(http.expectOne(urlFor(A, 7)), 'a', null);
    expect(c()).toBe('blob:1');
    expect(c()).toBe('blob:1');
    http.expectNone(urlFor(A, 7));
  });

  it('a stale hash stays suppressed for 30 s even after a fresher hash was seen', () => {
    vi.useFakeTimers();
    store.url(7, A);
    flush(http.expectOne(urlFor(A, 7)), 'b', B);
    store.url(7, B);
    store.url(7, A);
    http.expectNone(urlFor(A, 7));
    expect(refresh).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(30_001);
    store.url(7, A);
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
  });

  for (const order of ['A first', 'B first']) {
    it(`two slots with a stale and a fresh hash do not loop (${order})`, () => {
      const cA = computed(() => store.url(7, A)());
      const cB = computed(() => store.url(7, B)());
      if (order === 'A first') { cA(); cB(); } else { cB(); cA(); }
      const reqs = http.match(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
      expect(reqs.length).toBe(2);
      for (const r of reqs) { flush(r, 'b', B); }
      for (let i = 0; i < 3; i++) { cA(); cB(); }
      const more = http.match(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
      expect(more.length).toBeLessThanOrEqual(0);
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(cA()).toBe(cB());
      expect(cA()).not.toBeNull();
    });
  }

  it('a 200 with an empty body is treated as a failure', () => {
    const sig = store.url(7, A);
    http.expectOne(urlFor(A, 7)).flush(null as unknown as Blob);
    expect(sig()).toBeNull();
    store.url(7, A);
    http.expectNone(urlFor(A, 7));
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
