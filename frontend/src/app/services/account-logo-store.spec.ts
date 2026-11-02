import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { computed, effect } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SILENT_HTTP_ERRORS } from '../interceptors/error-logging.interceptor';
import { AccountLogoStore, LogoLease } from './account-logo-store';
import { AccountingService } from './accounting.service';

const A = 'a'.repeat(16);
const B = 'b'.repeat(16);
const C = 'c'.repeat(16);
const etag = (hash: string) => `"${hash}${'0'.repeat(48)}"`;
/** Every logo request of account 7 (the account used by the scenarios). */
const P = (r: { url: string }) => r.url.startsWith('/api/accounting/accounts/7/logo.png');
const hashOf = (req: TestRequest) => req.request.url.slice(-16);

/**
 * Scenario matrix (design rev 5.0, lease model). Letters in the test names refer to it:
 * a dedupe · b generation guard · c ETag mismatch (alias) · d missing / weak / non-hex ETag · e stale null lease ·
 * f deletion · g release + re-acquire in one macrotask · h re-upload of an earlier logo (W1) · i stale detail + list ·
 * j both 404 · k A 404 then B 200 · l transient error · m 0-byte body · n destroy · o silent context · p NG0600.
 * Each scenario ends with the boundedness epilogue: the macrotask queue drained, 31 s advanced with no lease event,
 * and no GET observed (scenarios where a live target keeps failing assert the one allowed GET per 30 s instead).
 */
describe('AccountLogoStore', () => {
  // Stubbing `URL` in place would leak into later specs of the same worker; the two methods are restored after each test.
  const originalObjectUrls = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL };
  let store: AccountLogoStore;
  let http: HttpTestingController;
  let accounting: AccountingService;
  let refresh: ReturnType<typeof vi.fn>;
  let revoke: ReturnType<typeof vi.fn>;
  /** Object URLs created so far; each one carries the label of the bytes it was made from. */
  let created: string[];
  const labels = new WeakMap<Blob, string>();

  const urlFor = (hash: string, id = 7) => `/api/accounting/accounts/${id}/logo.png?v=${hash}`;
  const blobOf = (label: string) => {
    const blob = new Blob([label], { type: 'image/png' });
    labels.set(blob, label);
    return blob;
  };
  /** 200 whose bytes are labelled `label`, with a strong ETag for `hash` (none when null). */
  const flush = (req: TestRequest, label: string, hash: string | null) =>
    req.flush(blobOf(label), { headers: hash ? { ETag: etag(hash) } : {} });
  const fail404 = (req: TestRequest) => req.flush(new Blob(['nope']), { status: 404, statusText: 'Not Found' });
  /** The label of the bytes a slot shows, or null. */
  const shown = (id = 7) => {
    const url = store.slot(id)();
    return url === null ? null : url.split(':')[2];
  };
  /** Drains the macrotask queue (0 ms timers only), then no GET may be pending. */
  const settle = () => {
    vi.advanceTimersByTime(1);
    http.expectNone(P);
  };
  /** Boundedness epilogue: settled, then 31 s pass with no lease event and nothing is fetched. */
  const epilogue = () => {
    settle();
    vi.advanceTimersByTime(31_000);
    http.expectNone(P);
  };

  beforeEach(() => {
    vi.useFakeTimers();
    created = [];
    revoke = vi.fn();
    Object.assign(URL, {
      createObjectURL: (blob: Blob) => {
        const url = `blob:${created.length + 1}:${labels.get(blob) ?? '?'}`;
        created.push(url);
        return url;
      },
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

  it('(a) N leases of the same (id, A) make one GET; every consumer reads the same slot signal', () => {
    const leases = Array.from({ length: 5 }, () => store.acquire(7, A));
    const req = http.expectOne(urlFor(A));
    expect(req.request.method).toBe('GET');
    expect(req.request.responseType).toBe('blob');
    flush(req, 'a', A);
    const slots = Array.from({ length: 5 }, () => store.slot(7));
    expect(slots.every(s => s === slots[0])).toBe(true);
    expect(shown()).toBe('a');
    expect(refresh).not.toHaveBeenCalled();
    store.acquire(7, A);
    http.expectNone(P);
    leases.forEach(l => l.release()); // one non-null lease stays
    epilogue();
    expect(shown()).toBe('a');
  });

  it('(a) slot() never fetches, for a known and an unknown account', () => {
    expect(store.slot(7)()).toBeNull();
    expect(store.slot(8)()).toBeNull();
    http.expectNone(() => true);
    epilogue();
  });

  // ---- b. generation guard ---------------------------------------------------------------------------------

  it('(b) A acquired, B acquired before A answers: 2 GETs, the late A 200 is dropped, B shown', () => {
    const leaseA = store.acquire(7, A);
    const slowA = http.expectOne(urlFor(A));
    leaseA.release();
    store.acquire(7, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    expect(shown()).toBe('b');
    flush(slowA, 'stale-a', A);
    expect(created).toEqual(['blob:1:b']);
    expect(shown()).toBe('b');
    epilogue();
    expect(revoke).not.toHaveBeenCalled();
  });

  it('(b) a superseded error is ignored entirely: no failed entry, no timer', () => {
    store.acquire(7, A); // stays live
    const slowA = http.expectOne(urlFor(A));
    const leaseB = store.acquire(7, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    fail404(slowA);
    expect(vi.getTimerCount()).toBe(0);
    // A is not failed: releasing B makes A the target and it loads at once.
    leaseB.release();
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(shown()).toBe('a');
    epilogue();
  });

  // ---- c. ETag mismatch ------------------------------------------------------------------------------------

  it('(c) acquire A → 200 ETag B: shown, one refresh; acquiring B after the list reloads costs nothing, nor does releasing A', () => {
    const leaseA = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'b', B);
    expect(shown()).toBe('b');
    expect(refresh).toHaveBeenCalledTimes(1);
    store.acquire(7, B); // the list reloaded to B
    http.expectNone(P);
    leaseA.release();
    http.expectNone(P);
    epilogue();
    expect(shown()).toBe('b');
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('(c) a stale detail lease acquired while B is shown is served the alias and makes no second refresh', () => {
    store.acquire(7, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    const detail = store.acquire(7, A); // one-shot payload from before the move; the real server answers B
    flush(http.expectOne(urlFor(A)), 'b', B);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(shown()).toBe('b');
    expect(revoke).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(revoke).toHaveBeenCalledWith('blob:1:b'); // same bytes, new URL; the old one goes
    detail.release();
    store.acquire(7, A); // the detail re-renders with its stale payload: alias A → B is already shown
    http.expectNone(P);
    epilogue();
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  // ---- d. missing / weak / non-hex ETag --------------------------------------------------------------------

  for (const [name, header] of [['missing', null], ['weak', `W/${etag(B)}`], ['non-hex', '"not-a-hash-at-all"']] as const) {
    it(`(d) a ${name} ETag resolves under the requested hash, no refresh, no alias`, () => {
      store.acquire(7, A);
      http.expectOne(urlFor(A)).flush(blobOf('a'), { headers: header ? { ETag: header } : {} });
      expect(shown()).toBe('a');
      expect(refresh).not.toHaveBeenCalled();
      store.acquire(7, A);
      http.expectNone(P);
      epilogue();
    });
  }

  // ---- e. stale null lease ---------------------------------------------------------------------------------

  for (const order of ['null older', 'null newer']) {
    it(`(e) a null lease beside a live A lease clears nothing and fetches nothing (${order})`, () => {
      let leaseA: LogoLease;
      if (order === 'null older') {
        store.acquire(7, null);
        leaseA = store.acquire(7, A);
      } else {
        leaseA = store.acquire(7, A);
        flush(http.expectOne(urlFor(A)), 'a', A);
        store.acquire(7, null);
      }
      if (order === 'null older') flush(http.expectOne(urlFor(A)), 'a', A);
      epilogue();
      expect(shown()).toBe('a');
      expect(revoke).not.toHaveBeenCalled();
      // Releasing A with the null lease still live: the target is null → deferred clear.
      leaseA.release();
      expect(shown()).toBe('a');
      vi.runOnlyPendingTimers(); // the clear only
      expect(shown()).toBeNull();
      expect(revoke).not.toHaveBeenCalled();
      vi.runOnlyPendingTimers(); // the revoke on the following macrotask
      expect(revoke).toHaveBeenCalledWith('blob:1:a');
      epilogue();
    });
  }

  // ---- f. deletion -----------------------------------------------------------------------------------------

  it('(f) every lease released or null: slot null after setTimeout(0), URL revoked next macrotask, a late 200 creates no URL, same hash loads again', () => {
    const l1 = store.acquire(7, A);
    const l2 = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(shown()).toBe('a');
    l1.release();
    l2.release();
    const l3 = store.acquire(7, B);
    const late = http.expectOne(urlFor(B));
    l3.release();
    store.acquire(7, null); // the list now says null
    expect(shown()).toBe('a'); // nothing synchronous
    vi.runOnlyPendingTimers();
    expect(shown()).toBeNull();
    vi.runOnlyPendingTimers();
    expect(revoke).toHaveBeenCalledWith('blob:1:a');
    flush(late, 'b', B);
    expect(created).toEqual(['blob:1:a']);
    expect(shown()).toBeNull();
    epilogue();
    // The same bytes uploaded again load again (the clear forgot the account).
    store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(shown()).toBe('a');
    epilogue();
  });

  it('(f) the clear empties aliases and failed: a hash that 404ed loads again and a mismatch refreshes again', () => {
    const lC = store.acquire(7, C);
    fail404(http.expectOne(urlFor(C)));
    lC.release();
    const lA = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'b', B);
    expect(refresh).toHaveBeenCalledTimes(1);
    lA.release();
    vi.advanceTimersByTime(2);
    expect(shown()).toBeNull();
    expect(vi.getTimerCount()).toBe(0); // the fail timer went with the clear
    const lC2 = store.acquire(7, C);
    flush(http.expectOne(urlFor(C)), 'c', C);
    lC2.release();
    store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'b', B);
    expect(refresh).toHaveBeenCalledTimes(2);
    epilogue();
  });

  // ---- g. release + re-acquire in one macrotask ------------------------------------------------------------

  it('(g) releasing and re-acquiring the same hash before the clear fires keeps the image with 0 GETs', () => {
    const lease = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    lease.release();
    store.acquire(7, A);
    epilogue();
    expect(shown()).toBe('a');
    expect(revoke).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  // ---- h. re-upload of an earlier logo (W1) ----------------------------------------------------------------

  it('(h) A → B → A with a real server: 3 GETs and A is shown again', () => {
    let lease = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    lease.release();
    lease = store.acquire(7, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    expect(shown()).toBe('b');
    lease.release();
    lease = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(shown()).toBe('a');
    epilogue();
    expect(created).toEqual(['blob:1:a', 'blob:2:b', 'blob:3:a']);
    expect(refresh).not.toHaveBeenCalled();
  });

  it('(h) A → B → A where ?v=A is answered with B bytes (ETag B) after the move: 3 GETs, B shown, one refresh, then silence', () => {
    let lease = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    lease.release();
    lease = store.acquire(7, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    lease.release();
    lease = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'b', B); // the server (or a stale list) still has B
    expect(shown()).toBe('b');
    expect(refresh).toHaveBeenCalledTimes(1);
    epilogue();
    // The refreshed list may keep A (nothing changed server side) or move to B: neither costs a GET.
    lease.release();
    store.acquire(7, A);
    http.expectNone(P);
    store.acquire(7, B);
    epilogue();
    expect(shown()).toBe('b');
  });

  it('(h) A → B → A where the browser cache serves old A bytes with ETag A: bounded (3 GETs); the old image shows until the next acquire', () => {
    let lease = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a-old', A);
    lease.release();
    lease = store.acquire(7, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    lease.release();
    store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a-old', A); // immutable cache hit
    expect(shown()).toBe('a-old');
    expect(refresh).not.toHaveBeenCalled();
    epilogue();
  });

  // ---- i. stale detail + list (per-hash server) ------------------------------------------------------------

  it('(i) detail A acquired, then list B: 2 GETs (A then B), B shown, the A lease never refetches', () => {
    store.acquire(7, A);
    const reqA = http.expectOne(urlFor(A));
    store.acquire(7, B);
    const reqB = http.expectOne(urlFor(B));
    flush(reqA, 'a', A); // superseded: dropped
    flush(reqB, 'b', B);
    expect(shown()).toBe('b');
    expect(created).toEqual(['blob:1:b']);
    epilogue();
    expect(refresh).not.toHaveBeenCalled();
  });

  it('(i) list B acquired, then a stale detail A: ≤ 2 GETs and A is shown (the newest lease is the target; documented edge)', () => {
    store.acquire(7, B);
    const reqB = http.expectOne(urlFor(B));
    store.acquire(7, A);
    const reqA = http.expectOne(urlFor(A));
    flush(reqB, 'b', B);
    flush(reqA, 'a', A);
    expect(shown()).toBe('a');
    expect(created).toEqual(['blob:1:a']);
    epilogue();
  });

  // ---- j. both 404 (per-account server) --------------------------------------------------------------------

  it('(j) A then B both 404: 2 GETs, slot null; each 30 s window costs one GET for the current target only', () => {
    store.acquire(7, A);
    fail404(http.expectOne(urlFor(A)));
    store.acquire(7, B);
    fail404(http.expectOne(urlFor(B)));
    expect(shown()).toBeNull();
    settle();
    vi.advanceTimersByTime(29_000);
    http.expectNone(P);
    vi.advanceTimersByTime(2_000);
    const retry = http.expectOne(P);
    expect(hashOf(retry)).toBe(B);
    fail404(retry);
    settle();
    vi.advanceTimersByTime(31_000);
    fail404(http.expectOne(urlFor(B)));
    settle();
    expect(shown()).toBeNull();
    expect(created).toEqual([]);
    expect(refresh).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(1); // the next window's timer
  });

  it('(j) a failure window ends with nothing when no lease wants an unresolved hash', () => {
    const lease = store.acquire(7, A);
    fail404(http.expectOne(urlFor(A)));
    lease.release();
    store.acquire(7, null);
    vi.advanceTimersByTime(1); // deferred clear: failed emptied, timer cleared
    expect(vi.getTimerCount()).toBe(0);
    epilogue();
  });

  // ---- k. A 404 then B 200 ---------------------------------------------------------------------------------

  it('(k) A 404 then B 200: B shown; releasing B makes A the target but it is failed → 0 GETs until the window ends → 1 GET', () => {
    store.acquire(7, A);
    fail404(http.expectOne(urlFor(A)));
    const leaseB = store.acquire(7, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    expect(shown()).toBe('b');
    leaseB.release();
    http.expectNone(P);
    settle();
    vi.advanceTimersByTime(31_000);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(shown()).toBe('a');
    epilogue();
    expect(revoke).toHaveBeenCalledWith('blob:1:b');
  });

  it('(k) a failure keeps the image already shown and does not revoke it', () => {
    const leaseA = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    leaseA.release();
    store.acquire(7, B);
    fail404(http.expectOne(urlFor(B)));
    expect(shown()).toBe('a');
    settle();
    expect(revoke).not.toHaveBeenCalled();
    vi.advanceTimersByTime(31_000);
    fail404(http.expectOne(urlFor(B))); // the live target keeps failing: one GET per window
    expect(shown()).toBe('a');
    settle();
  });

  // ---- l. transient error ----------------------------------------------------------------------------------

  it('(l) a status-0 error keeps null, then the window ends → 1 GET → 200 → shown', () => {
    store.acquire(7, A);
    http.expectOne(urlFor(A)).error(new ProgressEvent('error'));
    expect(shown()).toBeNull();
    settle();
    store.acquire(7, A); // another slot mounts: the hash is inside its window, no GET
    http.expectNone(P);
    vi.advanceTimersByTime(31_000);
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(shown()).toBe('a');
    epilogue();
    expect(vi.getTimerCount()).toBe(0);
  });

  // ---- m. 0-byte body --------------------------------------------------------------------------------------

  it('(m) a 200 with a 0-byte or null body is a failure: no URL, image kept, one GET after the window', () => {
    const leaseA = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    leaseA.release();
    store.acquire(7, B);
    http.expectOne(urlFor(B)).flush(new Blob([]), { headers: { ETag: etag(B) } });
    expect(shown()).toBe('a');
    expect(created).toEqual(['blob:1:a']);
    settle();
    vi.advanceTimersByTime(31_000);
    http.expectOne(urlFor(B)).flush(null as unknown as Blob);
    expect(shown()).toBe('a');
    settle();
    expect(revoke).not.toHaveBeenCalled();
  });

  // ---- n. destroy ------------------------------------------------------------------------------------------

  it('(n) destroy cancels in-flight loads, clears every timer, revokes the held URLs and late callbacks do nothing', () => {
    store.acquire(1, A);
    flush(http.expectOne(urlFor(A, 1)), 'a', A);
    store.acquire(2, A);
    fail404(http.expectOne(urlFor(A, 2))); // arms a failure timer
    store.acquire(3, A);
    flush(http.expectOne(urlFor(A, 3)), 'b', B); // mismatch: refresh
    store.acquire(7, A);
    const pending = http.expectOne(urlFor(A));
    const l4 = store.acquire(4, A);
    flush(http.expectOne(urlFor(A, 4)), 'a', A);
    l4.release(); // arms a deferred clear
    expect(vi.getTimerCount()).toBe(2);
    store.ngOnDestroy();
    expect(pending.cancelled).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    expect(revoke).toHaveBeenCalledWith('blob:1:a');
    expect(revoke).toHaveBeenCalledWith('blob:2:b');
    expect(revoke).toHaveBeenCalledWith('blob:3:a');
    const lease = store.acquire(7, B);
    expect(() => lease.release()).not.toThrow();
    expect(store.slot(7)()).toBeNull();
    http.expectNone(() => true);
    vi.advanceTimersByTime(60_000);
    http.expectNone(() => true);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(created.length).toBe(3);
  });

  it('(n) a URL that is still current for the account is never revoked', () => {
    const leaseA = store.acquire(7, A);
    flush(http.expectOne(urlFor(A)), 'a', A);
    leaseA.release();
    store.acquire(7, B);
    flush(http.expectOne(urlFor(B)), 'b', B);
    expect(shown()).toBe('b');
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith('blob:1:a');
  });

  // ---- o. silent context -----------------------------------------------------------------------------------

  it('(o) the logo GET carries SILENT_HTTP_ERRORS', () => {
    store.acquire(7, A);
    const req = http.expectOne(urlFor(A));
    expect(req.request.context.get(SILENT_HTTP_ERRORS)).toBe(true);
    flush(req, 'a', A);
    epilogue();
  });

  // ---- p. reactive contexts --------------------------------------------------------------------------------

  it('(p) slot() inside a computed and acquire() inside an effect never throw (NG0600) and track the slot', () => {
    const c = computed(() => store.slot(7)());
    expect(() => c()).not.toThrow();
    expect(c()).toBeNull();
    TestBed.runInInjectionContext(() => {
      effect(onCleanup => {
        const lease = store.acquire(7, A);
        onCleanup(() => lease.release());
      });
    });
    expect(() => TestBed.tick()).not.toThrow();
    flush(http.expectOne(urlFor(A)), 'a', A);
    expect(c()).toBe('blob:1:a');
    TestBed.tick();
    epilogue();
    expect(c()).toBe('blob:1:a');
  });
});
