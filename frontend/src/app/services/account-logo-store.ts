import { HttpClient, HttpContext, HttpResponse } from '@angular/common/http';
import { Injectable, OnDestroy, Signal, WritableSignal, inject, signal, untracked } from '@angular/core';
import { Subscription } from 'rxjs';

import { SILENT_HTTP_ERRORS } from '../interceptors/error-logging.interceptor';
import { AccountingService } from './accounting.service';

/**
 * Unified failure model (rev 4.6).
 *
 * One slot signal per account; every consumer with a non-null hash reads that slot, whatever its hash and
 * whether or not that hash has failed, so any image resolved for the account is shown by every consumer and a
 * consumer holding a stale or failed hash is woken by every slot change. Two per-account, per-hash sets only
 * suppress fetching, never display: `succeeded` (every post-clear 200 records the requested and the ETag hash;
 * a hash is content-addressed, so a 200 for it is a durable fact) and `failed` (current-generation errors only,
 * emptied by a 30 s timer without fetching). A fetch starts only for a hash that is neither resolved, in
 * flight, succeeded nor failed, so between two deletion clears the GETs of an account are bounded by the
 * number of distinct hashes seen (plus one wake-up refetch per hash when a current failure leaves the account
 * blank, which also empties `succeeded`; such a failure adds a `failed` entry, so that happens at most once per
 * live hash per window). Responses from before the last deletion clear are ignored entirely; a superseded
 * error is ignored entirely; a superseded 200 is recorded but never shown. An ETag mismatch is cached under
 * the real hash and refreshes the accounts once per requested hash. Logo GETs run with `SILENT_HTTP_ERRORS`
 * (no retry, no toast); the fallback is the slug / emoji.
 *
 * `url()` may be called inside a `computed()`: every synchronous signal write runs inside `untracked()`, and
 * a null hash never writes synchronously at all.
 */

/** How long a (account, hash) failure suppresses re-fetching before `failed` is emptied (no fetch on expiry). */
const FAILURE_WINDOW_MS = 30_000;

/** Returned for a null hash: that consumer shows nothing without touching the account's shared slot. */
const NO_LOGO: Signal<string | null> = signal<string | null>(null).asReadonly();

interface LogoState {
  /** Hash the slot's current object URL was resolved under (ETag hash when present); null when nothing is shown. */
  hash: string | null;
  /** Hash of the current-generation load in flight; null when none. Superseded loads are not current. */
  pending: string | null;
  /** Hashes a post-clear 200 resolved (requested and ETag hash). Suppresses fetching only; emptied by the deletion clear, or by a current failure that leaves the account blank. */
  succeeded: Set<string>;
  /** Hashes whose current-generation load failed. Suppresses fetching only; emptied by `failTimer` or the deletion clear. */
  failed: Set<string>;
  /** One per account, armed/replaced by every current-generation failure; on expiry empties `failed` without fetching. */
  failTimer: ReturnType<typeof setTimeout> | null;
  /** Bumped by every fetch and by the deletion clear; a response from an older generation is never shown. */
  generation: number;
  /** Generation set by the last deletion clear; responses of loads started before it are ignored entirely. */
  clearedGeneration: number;
  /** Every in-flight subscription (superseded ones included), unsubscribed on destroy. */
  inflight: Set<Subscription>;
  /**
   * Deferred clear scheduled by a null-hash url() call. Any non-null url() call for the account cancels it,
   * so a consumer holding a stale null (one-shot detail payload) never clears a slot another consumer uses.
   */
  pendingClear: ReturnType<typeof setTimeout> | null;
}

/** Fetches uploaded account logos as blobs and exposes one object-URL signal per account (model: module comment). */
@Injectable({ providedIn: 'root' })
export class AccountLogoStore implements OnDestroy {
  private readonly http = inject(HttpClient);
  private readonly accounting = inject(AccountingService);

  private readonly signals = new Map<number, WritableSignal<string | null>>();
  private readonly states = new Map<number, LogoState>();
  /** Pending revoke timers and the URL each one would revoke. */
  private readonly revokeTimers = new Map<ReturnType<typeof setTimeout>, string>();
  private destroyed = false;

  url(accountId: number, hash: string | null): Signal<string | null> {
    return untracked(() => this.resolve(accountId, hash));
  }

  /** Cancels every timer and in-flight load, then revokes every object URL the store still owns. */
  ngOnDestroy(): void {
    this.destroyed = true;
    const owned = new Set<string>();
    for (const state of this.states.values()) {
      for (const timer of [state.pendingClear, state.failTimer]) {
        if (timer !== null) clearTimeout(timer);
      }
      state.pendingClear = state.failTimer = null;
      for (const sub of state.inflight) sub.unsubscribe();
      state.inflight.clear();
    }
    for (const [timer, old] of this.revokeTimers) {
      clearTimeout(timer);
      owned.add(old);
    }
    this.revokeTimers.clear();
    for (const slot of this.signals.values()) {
      const current = untracked(slot);
      if (current) owned.add(current);
    }
    for (const u of owned) {
      // Never throw from teardown (jsdom has no revokeObjectURL; specs restore the original after each test).
      try { URL.revokeObjectURL(u); } catch { /* ignore */ }
    }
  }

  private resolve(id: number, hash: string | null): Signal<string | null> {
    if (this.destroyed) {
      return NO_LOGO;
    }
    if (hash === null) {
      this.scheduleClear(id);
      return NO_LOGO;
    }

    let slot = this.signals.get(id);
    if (!slot) {
      // Every write notifies, even same -> same, so the deferred clear and a failure can wake the consumers.
      slot = signal<string | null>(null, { equal: () => false });
      this.signals.set(id, slot);
    }
    let state = this.states.get(id);
    if (!state) {
      state = {
        hash: null, pending: null, succeeded: new Set(), failed: new Set(), failTimer: null,
        generation: 0, clearedGeneration: 0, inflight: new Set(), pendingClear: null,
      };
      this.states.set(id, state);
    }

    if (state.pendingClear !== null) {
      clearTimeout(state.pendingClear);
      state.pendingClear = null;
    }
    if (this.wanted(state, hash)) {
      this.fetch(id, state, hash);
    }
    // Always the shared slot, also for a failed hash: display follows the account, only fetching is suppressed.
    return slot;
  }

  /** True when `hash` is neither resolved, in flight (current generation), succeeded nor failed. */
  private wanted(state: LogoState, hash: string): boolean {
    return hash !== state.hash && hash !== state.pending && !state.succeeded.has(hash) && !state.failed.has(hash);
  }

  /** Starts a generation-protected load of `hash`; any earlier in-flight load for the account is superseded. */
  private fetch(id: number, state: LogoState, hash: string): void {
    state.generation++;
    state.pending = hash;
    const generation = state.generation;

    // The holder keeps the callbacks safe if an interceptor answers synchronously inside subscribe().
    const holder: { sub?: Subscription } = {};
    const done = () => {
      if (holder.sub) state.inflight.delete(holder.sub);
      if (state.generation === generation) state.pending = null;
    };
    const options = {
      responseType: 'blob', observe: 'response',
      context: new HttpContext().set(SILENT_HTTP_ERRORS, true),
    } as const;
    holder.sub = this.http.get(this.accounting.logoImageUrl(id, hash), options).subscribe({
      next: response => { done(); this.onResponse(id, hash, generation, response); },
      error: () => { done(); this.onError(id, hash, generation); },
    });
    if (!holder.sub.closed) {
      state.inflight.add(holder.sub);
    }
  }

  /** Clears the account on the next macrotask unless a non-null url() call for it comes first. */
  private scheduleClear(id: number): void {
    const state = this.states.get(id);
    if (!state || state.pendingClear !== null) {
      return;
    }
    const idle = state.hash === null && state.pending === null && state.failed.size === 0 && state.succeeded.size === 0;
    if (idle && this.signals.get(id)?.() == null) {
      return;
    }
    state.pendingClear = setTimeout(() => {
      state.pendingClear = null;
      if (this.destroyed) {
        return;
      }
      state.generation++;
      state.clearedGeneration = state.generation;
      state.pending = null;
      state.hash = null;
      state.succeeded.clear();
      this.clearFailed(state);
      // Forced notify (the slot signal never compares equal), even null -> null: a clear that drops an
      // in-flight load must still wake the non-null consumers so they refetch.
      this.show(id, null);
    }, 0);
  }

  /** Arms (replacing) the account's failure-expiry timer; on expiry `failed` is emptied and nothing is fetched. */
  private armFailureExpiry(state: LogoState): void {
    if (state.failTimer !== null) {
      clearTimeout(state.failTimer);
    }
    state.failTimer = setTimeout(() => {
      state.failTimer = null;
      if (!this.destroyed) {
        state.failed.clear();
      }
    }, FAILURE_WINDOW_MS);
  }

  private clearFailed(state: LogoState): void {
    state.failed.clear();
    if (state.failTimer !== null) {
      clearTimeout(state.failTimer);
      state.failTimer = null;
    }
  }

  private onResponse(id: number, requested: string, generation: number, response: HttpResponse<Blob>): void {
    if (this.destroyed) {
      return;
    }
    const state = this.states.get(id)!;
    if (generation <= state.clearedGeneration) {
      return; // a load from before the last deletion: ignored entirely (a same-bytes re-upload must load again)
    }
    if (!response.body || response.body.size === 0) {
      if (state.generation === generation) {
        this.onError(id, requested, generation);
      }
      return;
    }
    const actual = this.parseEtag(response.headers.get('ETag'));
    // A 200 is a durable fact about its hashes: recorded even when superseded, so no consumer fetches them again
    // (the account shows whatever its newest load resolves). The refresh runs once per requested hash because
    // a succeeded hash is never requested again.
    const first = !state.succeeded.has(requested);
    state.succeeded.add(requested);
    state.failed.delete(requested);
    if (actual !== null) {
      state.succeeded.add(actual);
      state.failed.delete(actual);
    }
    if (actual !== null && actual !== requested && first) {
      this.accounting.refreshAccounts();
    }
    // Generation check: a superseded response never becomes an object URL and never sets the resolved hash.
    if (state.generation !== generation) {
      return;
    }
    // Missing or unusable ETag: resolved under the requested hash.
    state.hash = actual ?? requested;
    this.show(id, URL.createObjectURL(response.body));
  }

  private onError(id: number, requested: string, generation: number): void {
    if (this.destroyed) {
      return;
    }
    const state = this.states.get(id)!;
    if (state.generation !== generation) {
      // Superseded (or pre-clear): ignored entirely. Whatever superseded it answers and wakes the consumers.
      return;
    }
    state.failed.add(requested);
    this.armFailureExpiry(state);
    if (state.hash === null) {
      // Nothing is shown: a 200 recorded while superseded was never displayed, so let its consumer fetch again.
      // Bounded: this failure added a `failed` entry, so each live hash can cause it at most once per window.
      state.succeeded.clear();
    }
    // Keep whatever the slot shows; wake every consumer (the slot never compares equal) so each re-resolves.
    const slot = this.signals.get(id)!;
    slot.set(untracked(slot));
  }

  private parseEtag(raw: string | null): string | null {
    if (!raw || raw.startsWith('W/')) {
      return null;
    }
    const hex = raw.replace(/"/g, '').slice(0, 16);
    return /^[0-9a-f]{16}$/i.test(hex) ? hex.toLowerCase() : null;
  }

  /** Sets the account's signal first, then revokes the old URL on the next macrotask unless still in use. */
  private show(id: number, next: string | null): void {
    const slot = this.signals.get(id)!;
    const old = slot();
    slot.set(next);
    if (old && old !== next) {
      const timer = setTimeout(() => {
        this.revokeTimers.delete(timer);
        if (this.destroyed) {
          return;
        }
        const inUse = [...this.signals.values()].some(s => s() === old);
        if (!inUse) {
          URL.revokeObjectURL(old);
        }
      }, 0);
      this.revokeTimers.set(timer, old);
    }
  }
}
