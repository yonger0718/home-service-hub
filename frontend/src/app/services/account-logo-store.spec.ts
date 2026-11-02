import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { computed, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountLogoStore } from './account-logo-store';
import { AccountingService } from './accounting.service';

const A = 'a'.repeat(16);
const B = 'b'.repeat(16);
const C = 'c'.repeat(16);
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

  it('the stale window re-resolves on expiry: the original computed recovers with no external trigger', () => {
    vi.useFakeTimers();
    const c = computed(() => store.url(7, A)());
    c();
    flush(http.expectOne(urlFor(A, 7)), 'b', B);
    expect(c()).toBe('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);
    c();
    http.expectNone(urlFor(A, 7));
    vi.advanceTimersByTime(30_001);
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(c()).toBe('blob:2');
    vi.runOnlyPendingTimers();
    expect(revoke).toHaveBeenCalledWith('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
  });

  it('the stale window expiry makes no GET when the model already moved to the resolved hash', () => {
    vi.useFakeTimers();
    const hash = signal<string | null>(A);
    const c = computed(() => store.url(7, hash())());
    c();
    flush(http.expectOne(urlFor(A, 7)), 'b', B);
    hash.set(B);
    expect(c()).toBe('blob:1');
    vi.advanceTimersByTime(30_001);
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
    expect(c()).toBe('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);
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

  it('null hash exposes null at once, clears the slot on the next macrotask and revokes on the one after', () => {
    vi.useFakeTimers();
    const sig = store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(sig()).toBe('blob:1');
    expect(store.url(1, null)()).toBeNull();
    expect(sig()).toBe('blob:1');
    vi.runOnlyPendingTimers();
    expect(sig()).toBeNull();
    expect(revoke).not.toHaveBeenCalled();
    vi.runOnlyPendingTimers();
    expect(revoke).toHaveBeenCalledWith('blob:1');
  });

  for (const order of ['A first', 'null first']) {
    it(`a null consumer and an A consumer of one account share one GET and never loop (${order})`, () => {
      vi.useFakeTimers();
      const cA = computed(() => store.url(7, A)());
      const cN = computed(() => store.url(7, null)());
      if (order === 'A first') { cA(); cN(); } else { cN(); cA(); }
      flush(http.expectOne(urlFor(A, 7)), 'a', A);
      for (let i = 0; i < 3; i++) { cA(); cN(); cN(); cA(); }
      vi.runOnlyPendingTimers();
      for (let i = 0; i < 3; i++) { cN(); cA(); }
      vi.runOnlyPendingTimers();
      http.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
      expect(counter).toBe(1);
      expect(cA()).toBe('blob:1');
      expect(cN()).toBeNull();
      expect(revoke).not.toHaveBeenCalled();
    });
  }

  it('a null consumer mounting while the A load is in flight costs one clear and one refetch, then settles', () => {
    vi.useFakeTimers();
    const cA = computed(() => store.url(7, A)());
    const cN = computed(() => store.url(7, null)());
    cA();
    cN();
    const first = http.expectOne(urlFor(A, 7));
    vi.runOnlyPendingTimers();
    flush(first, 'stale-a', A);
    expect(counter).toBe(0);
    cA();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(cA()).toBe('blob:1');
    expect(cN()).toBeNull();
    for (let i = 0; i < 3; i++) { cA(); cN(); }
    vi.runOnlyPendingTimers();
    for (let i = 0; i < 3; i++) { cN(); cA(); }
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
    expect(counter).toBe(1);
    expect(revoke).not.toHaveBeenCalled();
  });

  it('a failed load stays bounded across re-reads and timers', () => {
    vi.useFakeTimers();
    const cA = computed(() => store.url(7, A)());
    cA();
    http.expectOne(urlFor(A, 7)).flush(new Blob(['nope']), { status: 404, statusText: 'Not Found' });
    for (let i = 0; i < 3; i++) { expect(cA()).toBeNull(); }
    vi.runOnlyPendingTimers();
    cA();
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
  });

  for (const order of ['A first', 'B first']) {
    it(`two consumers whose different hashes both 404 make exactly 2 GETs and never loop (${order})`, () => {
      vi.useFakeTimers();
      const cA = computed(() => store.url(7, A)());
      const cB = computed(() => store.url(7, B)());
      if (order === 'A first') { cA(); cB(); } else { cB(); cA(); }
      const reqs = http.match(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
      expect(reqs.length).toBe(2);
      for (const r of reqs) { r.flush(new Blob(['nope']), { status: 404, statusText: 'Not Found' }); }
      for (let i = 0; i < 3; i++) {
        cA(); cB(); cB(); cA();
        vi.runOnlyPendingTimers();
        http.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
      }
      expect(cA()).toBeNull();
      expect(cB()).toBeNull();
      expect(counter).toBe(0);

      const cC = computed(() => store.url(7, C)());
      cC();
      flush(http.expectOne(urlFor(C, 7)), 'c', C);
      expect(cC()).toBe('blob:1');
      // A failed hash stays failed until the account's logo is removed: no GET for A or B after C resolved.
      store.url(7, A);
      store.url(7, B);
      cA(); cB(); cC();
      http.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
      expect(cA()).toBeNull();
      expect(cB()).toBeNull();
      expect(cC()).toBe('blob:1');
    });
  }

  for (const order of ['A first', 'B first']) {
    it(`a server answering per hash (A 404, B 200) with both consumers alive stays bounded (${order})`, () => {
      vi.useFakeTimers();
      const cA = computed(() => store.url(7, A)());
      const cB = computed(() => store.url(7, B)());
      if (order === 'A first') { cA(); cB(); } else { cB(); cA(); }
      let total = 0;
      for (let round = 0; round < 5; round++) {
        const reqs = http.match(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
        total += reqs.length;
        for (const r of reqs) {
          if (r.request.url.endsWith(`v=${A}`)) {
            r.flush(new Blob(['nope']), { status: 404, statusText: 'Not Found' });
          } else {
            flush(r, 'b', B);
          }
        }
        cA(); cB(); cB(); cA();
        vi.runOnlyPendingTimers();
      }
      expect(total).toBeLessThanOrEqual(3);
      http.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
      expect(cB()).toMatch(/^blob:/);
      expect(cA()).toBeNull();
      expect(refresh).not.toHaveBeenCalled();
    });
  }

  it('a late 404 from before a deletion clear does not block a same-bytes re-upload', () => {
    vi.useFakeTimers();
    store.url(7, A);
    const late = http.expectOne(urlFor(A, 7));
    store.url(7, null);
    vi.runOnlyPendingTimers();
    late.flush(new Blob(['nope']), { status: 404, statusText: 'Not Found' });
    const again = store.url(7, A);
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(again()).toBe('blob:1');
  });

  it('destroy cancels in-flight loads, arms nothing and revokes the current URLs', () => {
    vi.useFakeTimers();
    const shown = store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(shown()).toBe('blob:1');
    store.url(7, A);
    const pending = http.expectOne(urlFor(A, 7));
    store.url(1, null);
    store.ngOnDestroy();
    expect(pending.cancelled).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    expect(revoke).toHaveBeenCalledWith('blob:1');
    expect(store.url(7, B)()).toBeNull();
    http.expectNone(urlFor(B, 7));
    expect(refresh).not.toHaveBeenCalled();
    expect(counter).toBe(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('destroy clears an armed stale-window timer', () => {
    vi.useFakeTimers();
    store.url(7, A);
    flush(http.expectOne(urlFor(A, 7)), 'b', B);
    expect(vi.getTimerCount()).toBe(1);
    store.ngOnDestroy();
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(30_001);
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
  });

  it('a null consumer appearing after A is shown costs at most one refetch, then settles', () => {
    vi.useFakeTimers();
    const cA = computed(() => store.url(7, A)());
    cA();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(cA()).toBe('blob:1');
    const cN = computed(() => store.url(7, null)());
    cN();
    vi.runOnlyPendingTimers();
    cA();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    for (let i = 0; i < 3; i++) { cA(); cN(); }
    vi.runOnlyPendingTimers();
    for (let i = 0; i < 3; i++) { cA(); cN(); }
    http.expectNone(r => r.url.startsWith('/api/accounting/accounts/7/logo.png'));
    expect(cA()).toBe('blob:2');
    expect(cN()).toBeNull();
    expect(revoke).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith('blob:1');
  });

  it('deletion: every consumer turning null clears the slot, revokes the blob and drops a late response', () => {
    vi.useFakeTimers();
    const slot = store.url(7, A);
    const hash = signal<string | null>(A);
    const c1 = computed(() => store.url(7, hash())());
    const c2 = computed(() => store.url(7, hash())());
    c1(); c2();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(c1()).toBe('blob:1');
    expect(c2()).toBe('blob:1');
    hash.set(B);
    c1(); c2();
    const late = http.expectOne(urlFor(B, 7));
    hash.set(null);
    expect(c1()).toBeNull();
    expect(c2()).toBeNull();
    vi.runOnlyPendingTimers();
    expect(slot()).toBeNull();
    vi.runOnlyPendingTimers();
    expect(revoke).toHaveBeenCalledWith('blob:1');
    flush(late, 'b', B);
    expect(counter).toBe(1);
    expect(slot()).toBeNull();
    expect(c1()).toBeNull();
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
