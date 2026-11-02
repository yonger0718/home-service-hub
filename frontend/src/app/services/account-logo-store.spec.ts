import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { Signal, computed, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SILENT_HTTP_ERRORS } from '../interceptors/error-logging.interceptor';
import { AccountLogoStore } from './account-logo-store';
import { AccountingService } from './accounting.service';

const A = 'a'.repeat(16);
const B = 'b'.repeat(16);
const C = 'c'.repeat(16);
const etag = (hash: string) => `"${hash}${'0'.repeat(48)}"`;
const blobOf = (label: string) => new Blob([label], { type: 'image/png' });
/** Every logo request of account 7 (the account used by the multi-consumer scenarios). */
const P = (r: { url: string }) => r.url.startsWith('/api/accounting/accounts/7/logo.png');
const hashOf = (req: TestRequest) => req.request.url.slice(-16);

/**
 * Scenario matrix (design rev 4.6, unified failure model). Letters in the test names refer to it:
 * a dedupe · b generation guard · c ETag mismatch (succeeded hashes) · d missing ETag · e null/A alive ·
 * f null consumer after A · g deletion · h same-bytes re-upload · i both 404 (per-account server) ·
 * j per-hash server · k stuck-blank regression · l transient error + expiry · m destroy · n NG0600 ·
 * o two live hashes both 200 (per-hash / weak-ETag server) · p shown A, list moves to a 404 hash ·
 * q 0-byte body · r silent interceptor context.
 * Consumers are real computed()s wherever re-evaluation matters; "settle" re-reads every live computed for
 * three rounds (letting setTimeout(0) work run between them) and proves that no GET follows.
 */
describe('AccountLogoStore', () => {
  // Stubbing `URL` in place would leak into later specs of the same worker; the two methods are restored after each test.
  const originalObjectUrls = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL };
  let store: AccountLogoStore;
  let http: HttpTestingController;
  let accounting: AccountingService;
  let refresh: ReturnType<typeof vi.fn>;
  let revoke: ReturnType<typeof vi.fn>;
  let counter: number;

  const urlFor = (hash: string, id = 1) => `/api/accounting/accounts/${id}/logo.png?v=${hash}`;
  const flush = (req: TestRequest, label: string, hash: string | null) =>
    req.flush(blobOf(label), { headers: hash ? { ETag: etag(hash) } : {} });
  const fail404 = (req: TestRequest) => req.flush(new Blob(['nope']), { status: 404, statusText: 'Not Found' });
  const read = (...consumers: Signal<unknown>[]) => consumers.forEach(c => c());
  /** Three rounds of re-reads (both orders) with the macrotask queue drained in between; no GET may follow. */
  const settle = (...consumers: Signal<unknown>[]) => {
    for (let i = 0; i < 3; i++) {
      read(...consumers);
      vi.advanceTimersByTime(1);
      read(...[...consumers].reverse());
      http.expectNone(P);
    }
  };

  beforeEach(() => {
    counter = 0;
    revoke = vi.fn();
    Object.assign(URL, {
      createObjectURL: () => `blob:${++counter}`,
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

  // ---- a. dedupe -------------------------------------------------------------------------------------------

  it('(a) fetches once for N slots of the same (id, hash) and exposes the object URL', () => {
    const slots = Array.from({ length: 5 }, () => store.url(1, A));
    const req = http.expectOne(urlFor(A));
    expect(req.request.context.get(SILENT_HTTP_ERRORS)).toBe(true); // (r) no retry, no toast on the prod chain
    flush(req, 'a', A);
    expect(refresh).not.toHaveBeenCalled();
    expect(slots[0]()).toBe('blob:1');
    expect(slots.every(s => s === slots[0])).toBe(true);
    store.url(1, A);
    http.expectNone(urlFor(A));
  });

  it('(a) N computeds of the same (id, hash) share one GET and stay settled', () => {
    vi.useFakeTimers();
    const cs = Array.from({ length: 4 }, () => computed(() => store.url(7, A)()));
    read(...cs);
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    settle(...cs);
    expect(cs.map(c => c())).toEqual(Array(4).fill('blob:1'));
  });

  it('sends the bearer-safe URL with ?v=<hash> and responseType blob', () => {
    store.url(1, A);
    const req = http.expectOne(urlFor(A));
    expect(req.request.method).toBe('GET');
    expect(req.request.responseType).toBe('blob');
    flush(req, 'a', A);
  });

  // ---- b. generation guard ---------------------------------------------------------------------------------

  it('(b) A slow, B fast: B is shown, the late A response is dropped and never becomes a URL', () => {
    vi.useFakeTimers();
    const hash = signal(A);
    const c = computed(() => store.url(7, hash())());
    c();
    const slowA = http.expectOne(urlFor(A, 7));
    hash.set(B);
    c();
    flush(http.expectOne(urlFor(B, 7)), 'b', B);
    expect(c()).toBe('blob:1');
    flush(slowA, 'stale-a', A);
    expect(counter).toBe(1);
    expect(c()).toBe('blob:1');
    settle(c);
    expect(revoke).not.toHaveBeenCalled();
  });

  it('(b) drops a late response from a superseded generation even when its hash matches the current one', () => {
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

  // ---- c. ETag mismatch: cached under the real hash, both hashes succeeded ---------------------------------

  it('(c) etag mismatch A→B: cached under B, 1 refresh; re-reads no GET; 30 s later still no GET; consumer shows B', () => {
    vi.useFakeTimers();
    const c = computed(() => store.url(7, A)());
    c();
    flush(http.expectOne(urlFor(A, 7)), 'b', B);
    expect(c()).toBe('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);
    store.url(7, B);
    http.expectNone(urlFor(B, 7));
    settle(c);
    vi.advanceTimersByTime(30_001);
    c();
    http.expectNone(P);
    settle(c);
    expect(c()).toBe('blob:1');
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('(c) A→B→A: after a mismatch the model moving to B and back to A makes no GET (A is a succeeded hash)', () => {
    vi.useFakeTimers();
    const hash = signal(A);
    const c = computed(() => store.url(7, hash())());
    c();
    flush(http.expectOne(urlFor(A, 7)), 'b', B);
    hash.set(B);
    expect(c()).toBe('blob:1');
    http.expectNone(P);
    hash.set(A);
    expect(c()).toBe('blob:1');
    http.expectNone(P);
    vi.advanceTimersByTime(30_001);
    settle(c);
    expect(refresh).toHaveBeenCalledTimes(1);
    // A fresh hash still loads.
    hash.set(C);
    c();
    flush(http.expectOne(urlFor(C, 7)), 'c', C);
    expect(c()).toBe('blob:2');
    settle(c);
  });

  for (const order of ['A first', 'B first']) {
    it(`(c) two slots with a stale and a fresh hash, server answers B for both: 2 GETs, 1 refresh, no loop (${order})`, () => {
      vi.useFakeTimers();
      const cA = computed(() => store.url(7, A)());
      const cB = computed(() => store.url(7, B)());
      if (order === 'A first') read(cA, cB); else read(cB, cA);
      const reqs = http.match(P);
      expect(reqs.length).toBe(2);
      for (const r of reqs) flush(r, 'b', B);
      settle(cA, cB);
      vi.advanceTimersByTime(30_001);
      settle(cA, cB);
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(cA()).toBe(cB());
      expect(cA()).toMatch(/^blob:/);
    });
  }

  // ---- d. missing ETag -------------------------------------------------------------------------------------

  it('(d) missing etag shows the image, resolves under the requested hash, no refresh, re-reads make no GET', () => {
    vi.useFakeTimers();
    const c = computed(() => store.url(7, A)());
    expect(c()).toBeNull();
    flush(http.expectOne(urlFor(A, 7)), 'a', null);
    expect(c()).toBe('blob:1');
    expect(refresh).not.toHaveBeenCalled();
    store.url(7, A);
    settle(c);
  });

  // ---- e. null consumer and A consumer alive ---------------------------------------------------------------

  it('(e) null hash exposes null at once, clears the slot on the next macrotask and revokes on the one after', () => {
    vi.useFakeTimers();
    const sig = store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(sig()).toBe('blob:1');
    expect(store.url(1, null)()).toBeNull();
    expect(sig()).toBe('blob:1');
    vi.runOnlyPendingTimers(); // the deferred clear only (not the revoke it schedules)
    expect(sig()).toBeNull();
    expect(revoke).not.toHaveBeenCalled();
    vi.runOnlyPendingTimers();
    expect(revoke).toHaveBeenCalledWith('blob:1');
  });

  for (const order of ['A first', 'null first']) {
    it(`(e) a null consumer and an A consumer of one account settle at the blob with ≤ 2 GETs (${order})`, () => {
      vi.useFakeTimers();
      const cA = computed(() => store.url(7, A)());
      const cN = computed(() => store.url(7, null)());
      if (order === 'A first') read(cA, cN); else read(cN, cA);
      let gets = 0;
      for (let i = 0; i < 3; i++) {
        const reqs = http.match(P);
        gets += reqs.length;
        for (const r of reqs) flush(r, 'a', A);
        read(cA, cN);
        vi.advanceTimersByTime(1);
        read(cN, cA);
      }
      expect(gets).toBeLessThanOrEqual(2);
      settle(cA, cN);
      expect(cA()).toMatch(/^blob:/);
      expect(cN()).toBeNull();
    });
  }

  it('(e) a null consumer mounting while the A load is in flight (clear fires before the flush): one refetch, then settles', () => {
    vi.useFakeTimers();
    const cA = computed(() => store.url(7, A)());
    const cN = computed(() => store.url(7, null)());
    read(cA, cN);
    const first = http.expectOne(urlFor(A, 7));
    vi.advanceTimersByTime(1); // deferred clear: drops the in-flight load and wakes cA
    flush(first, 'stale-a', A);
    expect(counter).toBe(0);
    cA();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(cA()).toBe('blob:1');
    expect(cN()).toBeNull();
    settle(cA, cN);
    expect(counter).toBe(1);
    expect(revoke).not.toHaveBeenCalled();
  });

  it('(e) in-flight order, A consumer re-evaluated before the dropped response arrives: still 2 GETs and the blob', () => {
    vi.useFakeTimers();
    const cA = computed(() => store.url(7, A)());
    const cN = computed(() => store.url(7, null)());
    read(cA, cN);
    const first = http.expectOne(urlFor(A, 7));
    vi.advanceTimersByTime(1);
    cA(); // re-evaluates after the clear: the superseded load does not block the refetch
    const second = http.expectOne(urlFor(A, 7));
    flush(first, 'stale-a', A);
    expect(counter).toBe(0);
    flush(second, 'a', A);
    expect(cA()).toBe('blob:1');
    settle(cA, cN);
    expect(counter).toBe(1);
  });

  // ---- f. null consumer appearing after A is shown ---------------------------------------------------------

  it('(f) a null consumer appearing after A is shown costs at most one refetch and one revoke, then settles', () => {
    vi.useFakeTimers();
    const cA = computed(() => store.url(7, A)());
    cA();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(cA()).toBe('blob:1');
    const cN = computed(() => store.url(7, null)());
    cN();
    vi.advanceTimersByTime(1);
    cA();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    settle(cA, cN);
    expect(cA()).toBe('blob:2');
    expect(cN()).toBeNull();
    expect(revoke).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith('blob:1');
  });

  it('(f) A, A, null: the two A consumers share the single refetch after the clear', () => {
    vi.useFakeTimers();
    const cA1 = computed(() => store.url(7, A)());
    const cA2 = computed(() => store.url(7, A)());
    read(cA1, cA2);
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    read(cA1, cA2);
    const cN = computed(() => store.url(7, null)());
    cN();
    vi.advanceTimersByTime(1);
    read(cA1, cA2);
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    settle(cA1, cA2, cN);
    expect(cA1()).toBe('blob:2');
    expect(cA2()).toBe('blob:2');
    expect(revoke).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith('blob:1');
  });

  // ---- g. deletion -----------------------------------------------------------------------------------------

  it('(g) deletion: every consumer turning null clears the slot, revokes the blob, drops a late response and empties failed', () => {
    vi.useFakeTimers();
    const slot = store.url(7, A);
    const hash = signal<string | null>(A);
    const c1 = computed(() => store.url(7, hash())());
    const c2 = computed(() => store.url(7, hash())());
    read(c1, c2);
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(c1()).toBe('blob:1');
    expect(c2()).toBe('blob:1');
    hash.set(C);
    read(c1, c2);
    fail404(http.expectOne(urlFor(C, 7))); // C is now failed; the image stays
    expect(c1()).toBe('blob:1');
    hash.set(B);
    read(c1, c2);
    const late = http.expectOne(urlFor(B, 7));
    hash.set(null);
    expect(c1()).toBeNull();
    expect(c2()).toBeNull();
    vi.advanceTimersByTime(1);
    expect(slot()).toBeNull();
    vi.advanceTimersByTime(1);
    expect(revoke).toHaveBeenCalledWith('blob:1');
    flush(late, 'b', B);
    expect(counter).toBe(1);
    expect(slot()).toBeNull();
    // failed was emptied by the clear: C loads again when asked for.
    hash.set(C);
    read(c1, c2);
    flush(http.expectOne(urlFor(C, 7)), 'c', C);
    expect(c1()).toBe('blob:2');
    settle(c1, c2);
  });

  // ---- h. same-bytes re-upload after a deletion ------------------------------------------------------------

  it('(h) a late 404 from before a deletion clear does not block a same-bytes re-upload (hash A again)', () => {
    vi.useFakeTimers();
    store.url(7, A);
    const late = http.expectOne(urlFor(A, 7));
    store.url(7, null);
    vi.advanceTimersByTime(1);
    fail404(late);
    const again = computed(() => store.url(7, A)());
    again();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(again()).toBe('blob:1');
    settle(again);
  });

  it('(h) shown A, deleted, then the same hash A uploaded again loads once more', () => {
    vi.useFakeTimers();
    const hash = signal<string | null>(A);
    const c = computed(() => store.url(7, hash())());
    c();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    hash.set(null);
    c();
    vi.advanceTimersByTime(2);
    expect(revoke).toHaveBeenCalledWith('blob:1');
    hash.set(A);
    c();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(c()).toBe('blob:2');
    settle(c);
  });

  // ---- i. both hashes 404 (per-account server) -------------------------------------------------------------

  for (const order of ['A first', 'B first']) {
    it(`(i) two consumers whose hashes both 404 (server 404 per account) make 3 GETs, then nothing (${order})`, () => {
      vi.useFakeTimers();
      const cA = computed(() => store.url(7, A)());
      const cB = computed(() => store.url(7, B)());
      if (order === 'A first') read(cA, cB); else read(cB, cA);
      const reqs = http.match(P);
      expect(reqs.length).toBe(2);
      for (const r of reqs) fail404(r);
      read(cA, cB);
      // The first request was superseded by the second, so its 404 was ignored; the second's failure wakes
      // both consumers and the superseded hash is fetched once more. That failure is recorded, so it ends here.
      const retry = http.expectOne(P);
      expect(hashOf(retry)).toBe(order === 'A first' ? A : B);
      fail404(retry);
      settle(cA, cB);
      expect(cA()).toBeNull();
      expect(cB()).toBeNull();
      expect(counter).toBe(0);
      expect(refresh).not.toHaveBeenCalled();

      // A later success for the account is shown by every consumer, failed hashes included; no GET for A or B.
      const cC = computed(() => store.url(7, C)());
      cC();
      flush(http.expectOne(urlFor(C, 7)), 'c', C);
      settle(cA, cB, cC);
      expect(cA()).toBe('blob:1');
      expect(cB()).toBe('blob:1');
      expect(cC()).toBe('blob:1');
    });
  }

  // ---- j. per-hash server (A 404, B 200) -------------------------------------------------------------------

  for (const order of ['A first', 'B first']) {
    it(`(j) a server answering per hash (A 404, B 200), both consumers alive: ≤ 3 GETs, both show B (${order})`, () => {
      vi.useFakeTimers();
      const cA = computed(() => store.url(7, A)());
      const cB = computed(() => store.url(7, B)());
      if (order === 'A first') read(cA, cB); else read(cB, cA);
      let total = 0;
      for (let round = 0; round < 5; round++) {
        const reqs = http.match(P);
        total += reqs.length;
        for (const r of reqs) {
          if (hashOf(r) === A) fail404(r); else flush(r, 'b', B);
        }
        read(cA, cB);
        vi.advanceTimersByTime(1);
        read(cB, cA);
      }
      expect(total).toBeLessThanOrEqual(3);
      settle(cA, cB);
      expect(cB()).toMatch(/^blob:/);
      expect(cA()).toBe(cB());
      expect(refresh).not.toHaveBeenCalled();
    });
  }

  // ---- k. stuck-blank regression ---------------------------------------------------------------------------

  for (const order of ['detail first', 'list first']) {
    it(`(k) detail holds A, list holds B, both 404; upload C moves the list to C → both computeds show C (${order})`, () => {
      vi.useFakeTimers();
      const list = signal(B);
      const cD = computed(() => store.url(7, A)());
      const cL = computed(() => store.url(7, list())());
      const rd = () => (order === 'detail first' ? read(cD, cL) : read(cL, cD));
      rd();
      for (const r of http.match(P)) fail404(r);
      rd();
      for (const r of http.match(P)) fail404(r); // the one wake-up refetch of the superseded hash
      settle(cD, cL);
      expect(cD()).toBeNull();
      expect(cL()).toBeNull();

      list.set(C);
      rd();
      flush(http.expectOne(urlFor(C, 7)), 'c', C);
      settle(cD, cL);
      expect(cD()).toBe('blob:1');
      expect(cL()).toBe('blob:1');
      expect(counter).toBe(1);
    });
  }

  // ---- l. transient error ----------------------------------------------------------------------------------

  it('(l) a transient network error: no refetch on re-reads nor on expiry; a new consumer read after 30 s loads it', () => {
    vi.useFakeTimers();
    const hash = signal(A);
    const c = computed(() => store.url(7, hash())());
    c();
    http.expectOne(urlFor(A, 7)).error(new ProgressEvent('error'));
    expect(c()).toBeNull();
    settle(c);
    vi.advanceTimersByTime(30_001); // failure expiry: failed emptied, no auto refetch
    c();
    http.expectNone(P);
    const c2 = computed(() => store.url(7, A)()); // next consumer re-evaluation recovers
    c2();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(c()).toBe('blob:1');
    expect(c2()).toBe('blob:1');
    settle(c, c2);
  });

  it('(l) inside the window a failed hash is never refetched; after it the next model change loads it', () => {
    vi.useFakeTimers();
    const hash = signal(A);
    const c = computed(() => store.url(7, hash())());
    c();
    http.expectOne(urlFor(A, 7)).error(new ProgressEvent('error'));
    hash.set(B);
    c();
    fail404(http.expectOne(urlFor(B, 7)));
    hash.set(A);
    c();
    http.expectNone(P); // A still failed
    hash.set(B);
    c();
    http.expectNone(P); // B still failed
    vi.advanceTimersByTime(30_001); // failed emptied, nothing fetched
    http.expectNone(P);
    hash.set(A);
    c();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    expect(c()).toBe('blob:1');
    settle(c);
  });

  it('(l) A fails, another consumer loads B → the A consumer shows B, no GET for A', () => {
    vi.useFakeTimers();
    const cA = computed(() => store.url(7, A)());
    cA();
    http.expectOne(urlFor(A, 7)).error(new ProgressEvent('error'));
    expect(cA()).toBeNull();
    const cB = computed(() => store.url(7, B)());
    cB();
    flush(http.expectOne(urlFor(B, 7)), 'b', B);
    settle(cA, cB);
    expect(cB()).toBe('blob:1');
    expect(cA()).toBe('blob:1');
  });

  it('(l) a failure keeps the image already shown and does not revoke it', () => {
    vi.useFakeTimers();
    const hash = signal(A);
    const c = computed(() => store.url(7, hash())());
    c();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    hash.set(B);
    c();
    fail404(http.expectOne(urlFor(B, 7)));
    expect(c()).toBe('blob:1');
    settle(c);
    expect(revoke).not.toHaveBeenCalled();
  });

  it('(l) a 200 with an empty body is treated as a current-generation failure', () => {
    vi.useFakeTimers();
    const c = computed(() => store.url(7, A)());
    c();
    http.expectOne(urlFor(A, 7)).flush(null as unknown as Blob);
    expect(c()).toBeNull();
    settle(c);
    expect(counter).toBe(0);
  });

  it('(l) 404 marks the hash failed and does not retry until the hash changes', () => {
    const sig = store.url(1, A);
    fail404(http.expectOne(urlFor(A)));
    expect(sig()).toBeNull();
    store.url(1, A);
    http.expectNone(urlFor(A));
    store.url(1, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    expect(sig()).not.toBeNull();
  });

  // ---- m. destroy ------------------------------------------------------------------------------------------

  it('(m) destroy cancels in-flight loads, clears every timer, revokes the held URLs and late callbacks do nothing', () => {
    vi.useFakeTimers();
    const shown = store.url(1, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(shown()).toBe('blob:1');
    store.url(2, A);
    fail404(http.expectOne(urlFor(A, 2))); // arms the failure-expiry timer
    store.url(3, A);
    flush(http.expectOne(urlFor(A, 3)), 'b', B); // mismatch: refresh, no timer
    store.url(7, A);
    const pending = http.expectOne(urlFor(A, 7));
    store.url(1, null); // arms a deferred clear
    expect(vi.getTimerCount()).toBe(2);
    store.ngOnDestroy();
    expect(pending.cancelled).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    expect(revoke).toHaveBeenCalledWith('blob:1');
    expect(revoke).toHaveBeenCalledWith('blob:2');
    expect(store.url(7, B)()).toBeNull();
    http.expectNone(urlFor(B, 7));
    vi.advanceTimersByTime(60_000);
    http.expectNone(P);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(counter).toBe(2);
  });

  it('(m) destroy clears an armed failure-expiry timer', () => {
    vi.useFakeTimers();
    store.url(7, A);
    fail404(http.expectOne(urlFor(A, 7)));
    expect(vi.getTimerCount()).toBe(1);
    store.ngOnDestroy();
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(30_001);
    http.expectNone(P);
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

  // ---- o. two live hashes that both answer 200 ------------------------------------------------------------

  for (const weak of [false, true]) {
    for (const order of ['detail first', 'list first']) {
      it(`(o${weak ? '2, W/ ETags' : ''}) detail A + list B both 200: exactly 2 GETs, both show the last-resolved image (${order})`, () => {
        vi.useFakeTimers();
        const cD = computed(() => store.url(7, A)());
        const cL = computed(() => store.url(7, B)());
        if (order === 'detail first') read(cD, cL); else read(cL, cD);
        const reqs = http.match(P);
        expect(reqs.length).toBe(2);
        for (const r of reqs) {
          const h = hashOf(r);
          r.flush(blobOf(h), { headers: { ETag: weak ? `W/${etag(h)}` : etag(h) } });
        }
        settle(cD, cL);
        vi.advanceTimersByTime(31_000);
        settle(cD, cL);
        expect(counter).toBe(1); // the superseded 200 never became a URL
        expect(cD()).toBe('blob:1');
        expect(cL()).toBe('blob:1');
        expect(refresh).not.toHaveBeenCalled();
        expect(revoke).not.toHaveBeenCalled();
      });
    }
  }

  // ---- p. shown A, list moves to a hash that 404s --------------------------------------------------------

  it('(p) A shown, then the list moves to B which 404s: exactly 1 extra GET, both consumers keep A', () => {
    vi.useFakeTimers();
    const list = signal(A);
    const cD = computed(() => store.url(7, A)());
    const cL = computed(() => store.url(7, list())());
    read(cD, cL);
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    settle(cD, cL);
    list.set(B);
    read(cL, cD);
    fail404(http.expectOne(urlFor(B, 7)));
    settle(cD, cL);
    vi.advanceTimersByTime(31_000);
    settle(cD, cL);
    expect(cD()).toBe('blob:1');
    expect(cL()).toBe('blob:1');
    expect(revoke).not.toHaveBeenCalled();
  });

  // ---- q. 0-byte body --------------------------------------------------------------------------------------

  it('(q) a 200 with a 0-byte body is a failure: no URL, slot unchanged, no refetch', () => {
    vi.useFakeTimers();
    const hash = signal(A);
    const c = computed(() => store.url(7, hash())());
    c();
    flush(http.expectOne(urlFor(A, 7)), 'a', A);
    hash.set(B);
    c();
    http.expectOne(urlFor(B, 7)).flush(new Blob([]), { headers: { ETag: etag(B) } });
    expect(c()).toBe('blob:1');
    settle(c);
    expect(counter).toBe(1);
    expect(revoke).not.toHaveBeenCalled();
    const fresh = computed(() => store.url(8, A)());
    fresh();
    http.expectOne(urlFor(A, 8)).flush(new Blob([]), { headers: { ETag: etag(A) } });
    expect(fresh()).toBeNull();
    fresh();
    http.expectNone(urlFor(A, 8));
  });

  // ---- n. NG0600 -------------------------------------------------------------------------------------------

  it('(n) url() can be called inside a computed without throwing, for a hash, a failed hash and null', () => {
    const withHash = computed(() => store.url(7, 'abc')());
    expect(() => withHash()).not.toThrow();
    expect(withHash()).toBeNull();
    flush(http.expectOne(urlFor('abc', 7)), 'x', null);
    expect(withHash()).toBe('blob:1');
    const failing = computed(() => store.url(7, A)());
    expect(() => failing()).not.toThrow();
    fail404(http.expectOne(urlFor(A, 7)));
    expect(() => failing()).not.toThrow();
    expect(failing()).toBe('blob:1');
    const cleared = computed(() => store.url(7, null)());
    expect(() => cleared()).not.toThrow();
    expect(cleared()).toBeNull();
  });
});
