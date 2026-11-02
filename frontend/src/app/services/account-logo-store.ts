import { HttpClient, HttpResponse } from '@angular/common/http';
import { Injectable, OnDestroy, Signal, WritableSignal, inject, signal, untracked } from '@angular/core';
import { Subscription } from 'rxjs';

import { AccountingService } from './accounting.service';

/**
 * Unified failure model (rev 4.5).
 *
 * One slot signal per account; every consumer with a non-null hash reads that slot, whatever its hash and
 * whether or not that hash has failed, so any image resolved for the account is shown by every consumer and a
 * consumer holding a stale or failed hash is woken by every slot change. `failed` (per account, per hash) only
 * suppresses re-fetching, never display. Errors count only when they belong to the current generation; a
 * superseded error is ignored entirely. A current-generation failure keeps whatever the slot shows and
 * force-notifies it; each woken consumer can start a fetch only for a hash that is neither resolved, in flight
 * nor failed, so the GETs between two external changes are bounded by the number of distinct live hashes
 * (a failure is recorded before the wake that follows it). A 30 s per-account timer empties `failed` without
 * fetching; recovery happens on the next consumer re-evaluation. Deletion (every consumer null) clears the
 * account on the next macrotask; an ETag mismatch is cached under the real hash and the requested hash is
 * marked stale for 30 s, re-resolved on expiry.
 *
 * `url()` may be called inside a `computed()`: every synchronous signal write runs inside `untracked()`, and
 * a null hash never writes synchronously at all.
 */

/** Mismatch guard window; on expiry the account re-resolves its last requested hash (see `armStale`). */
const STALE_WINDOW_MS = 30_000;

/** How long a (account, hash) failure suppresses re-fetching before `failed` is emptied (no fetch on expiry). */
const FAILURE_WINDOW_MS = 30_000;

/** Returned for a null hash: that consumer shows nothing without touching the account's shared slot. */
const NO_LOGO: Signal<string | null> = signal<string | null>(null).asReadonly();

interface LogoState {
  /** Hash the slot's current object URL was resolved under (ETag hash when present); null when nothing is shown. */
  hash: string | null;
  /** Hash of the current-generation load in flight; null when none. Superseded loads are not current. */
  pending: string | null;
  /** Hashes whose load failed, per (account, hash). Suppresses fetching only; emptied by `failTimer` or a deletion clear. */
  failed: Set<string>;
  /** One per account, armed/replaced by every current-generation failure; on expiry empties `failed` without fetching. */
  failTimer: ReturnType<typeof setTimeout> | null;
  /** Bumped by every fetch and by the deletion clear; a response from an older generation is never shown. */
  generation: number;
  /** Every in-flight subscription (superseded ones included), unsubscribed on destroy. */
  inflight: Set<Subscription>;
  /**
   * Loop guard, set on an ETag mismatch to the hash that was requested (the model's stale value). Until
   * `staleUntil`, url() neither refetches nor refreshes again for that hash. `staleTimer` ends the window
   * actively: on expiry the account refetches `lastRequested` if it differs from the resolved hash.
   */
  staleRequested: string | null;
  staleUntil: number;
  staleTimer: ReturnType<typeof setTimeout> | null;
  /** The hash of the latest non-null url() call for this account. */
  lastRequested: string | null;
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
      for (const timer of [state.pendingClear, state.staleTimer, state.failTimer]) {
        if (timer !== null) clearTimeout(timer);
      }
      state.pendingClear = state.staleTimer = state.failTimer = null;
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
        hash: null, pending: null, failed: new Set(), failTimer: null, generation: 0, inflight: new Set(),
        staleRequested: null, staleUntil: 0, staleTimer: null, lastRequested: null, pendingClear: null,
      };
      this.states.set(id, state);
    }

    if (state.pendingClear !== null) {
      clearTimeout(state.pendingClear);
      state.pendingClear = null;
    }
    state.lastRequested = hash;

    if (hash === state.staleRequested && Date.now() < state.staleUntil) {
      return slot;
    }
    if (this.wanted(state, hash)) {
      this.fetch(id, state, hash);
    }
    // Always the shared slot, also for a failed hash: display follows the account, only fetching is suppressed.
    return slot;
  }

  /** True when `hash` is neither resolved, in flight (current generation) nor failed. */
  private wanted(state: LogoState, hash: string): boolean {
    return hash !== state.hash && hash !== state.pending && !state.failed.has(hash);
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
    holder.sub = this.http.get(this.accounting.logoImageUrl(id, hash), { responseType: 'blob', observe: 'response' }).subscribe({
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
    if (state.hash === null && state.pending === null && state.failed.size === 0 && this.signals.get(id)?.() == null) {
      return;
    }
    state.pendingClear = setTimeout(() => {
      state.pendingClear = null;
      if (this.destroyed) {
        return;
      }
      state.generation++;
      state.pending = null;
      state.hash = null;
      state.lastRequested = null;
      this.clearFailed(state);
      // Forced notify (the slot signal never compares equal), even null -> null: a clear that drops an
      // in-flight load must still wake the non-null consumers so they refetch.
      this.show(id, null);
    }, 0);
  }

  /** Arms the mismatch guard for `requested` and the one expiry timer of the account (replacing any earlier one). */
  private armStale(id: number, state: LogoState, requested: string): void {
    state.staleRequested = requested;
    state.staleUntil = Date.now() + STALE_WINDOW_MS;
    if (state.staleTimer !== null) {
      clearTimeout(state.staleTimer);
    }
    state.staleTimer = setTimeout(() => {
      state.staleTimer = null;
      if (this.destroyed) {
        return;
      }
      state.staleRequested = null;
      state.staleUntil = 0;
      const last = state.lastRequested;
      if (last !== null && this.wanted(state, last)) {
        this.fetch(id, state, last);
      }
    }, STALE_WINDOW_MS);
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
    const actual = this.parseEtag(response.headers.get('ETag'));
    // Record the mismatch guard before the generation check: even a superseded response proves the
    // requested hash is stale, and skipping it would let a second slot refetch it forever.
    const mismatch = actual !== null && actual !== requested;
    if (mismatch && !(state.staleRequested === requested && Date.now() < state.staleUntil)) {
      this.armStale(id, state, requested);
      this.accounting.refreshAccounts();
    }
    // Generation check: a superseded response never becomes an object URL.
    if (state.generation !== generation) {
      return;
    }
    if (!response.body) {
      this.onError(id, requested, generation);
      return;
    }
    state.failed.delete(requested);
    if (actual !== null) {
      state.failed.delete(actual);
    }
    // Missing or unusable ETag: resolved under the requested hash; only a hash change refetches.
    state.hash = actual ?? requested;
    this.show(id, URL.createObjectURL(response.body));
  }

  private onError(id: number, requested: string, generation: number): void {
    if (this.destroyed) {
      return;
    }
    const state = this.states.get(id)!;
    if (state.generation !== generation) {
      // Superseded: ignored entirely. Whatever superseded it answers (and notifies) for the account.
      return;
    }
    state.failed.add(requested);
    this.armFailureExpiry(state);
    // Keep whatever the slot shows; wake every consumer (the slot never compares equal) so each re-resolves.
    // Bounded: the failure is recorded before the wake, so no woken consumer fetches this hash again.
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
