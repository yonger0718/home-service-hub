import { HttpClient, HttpResponse } from '@angular/common/http';
import { Injectable, OnDestroy, Signal, WritableSignal, inject, signal, untracked } from '@angular/core';
import { Subscription } from 'rxjs';

import { AccountingService } from './accounting.service';

/** Mismatch guard window; on expiry the account re-resolves its last requested hash (see `armStale`). */
const STALE_WINDOW_MS = 30_000;

/** Returned for a null hash: that consumer shows nothing without touching the account's shared slot. */
const NO_LOGO: Signal<string | null> = signal<string | null>(null).asReadonly();

interface LogoState {
  hash: string | null;
  /** The hash the slot's current object URL was resolved under (null when the slot shows nothing). */
  shownHash: string | null;
  /**
   * Hashes whose load failed, per (account, hash), so consumers holding different hashes cannot clear each
   * other's failure. A failed hash stays failed until a load resolves under it (requested or ETag hash) or
   * the deferred deletion clear empties the set; a consumer holding it shows the fallback with no GET.
   */
  failed: Set<string>;
  generation: number;
  /** Generation set by the last deferred deletion clear; errors of loads started before it are ignored. */
  clearedGeneration: number;
  /** In-flight loads (superseded ones included), unsubscribed on destroy. */
  inflight: Set<Subscription>;
  /**
   * Loop guard, set on an ETag mismatch to the hash that was requested (the model's stale value).
   * Until `staleUntil`, url() neither refetches nor refreshes again for that hash, even if another hash
   * was seen in between (two slots of one account may read different sources). Task 8 rule: every slot
   * should read `logo_hash` from a source that `refreshAccounts()` reloads; the 30 s bound only limits
   * the damage when one does not. `staleTimer` ends the window actively: on expiry the account refetches
   * `lastRequested` if it differs from the resolved hash, so a re-upload of the same hash inside the window
   * reaches the slot without any further url() call.
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

/**
 * Fetches uploaded account logos as blobs and exposes one object-URL signal per account.
 * `url()` may be called inside a `computed()`: all synchronous signal writes run untracked, and a null hash
 * never writes synchronously at all.
 */
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
      if (state.pendingClear !== null) clearTimeout(state.pendingClear);
      if (state.staleTimer !== null) clearTimeout(state.staleTimer);
      state.pendingClear = state.staleTimer = null;
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
      // Every write notifies, even null -> null, so the deferred clear and a failure can wake the consumers.
      slot = signal<string | null>(null, { equal: () => false });
      this.signals.set(id, slot);
    }
    let state = this.states.get(id);
    if (!state) {
      state = {
        hash: null, shownHash: null, failed: new Set(), generation: 0, clearedGeneration: 0, inflight: new Set(),
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
    if (state.failed.has(hash)) {
      // Inert until the hash changes: a failed consumer does not depend on the slot, so another hash's
      // load or failure cannot wake it into a refetch.
      return NO_LOGO;
    }
    if (state.hash === hash) {
      return slot;
    }
    this.fetch(id, state, hash);
    return slot;
  }

  /** Starts a generation-protected load of `hash`; any earlier in-flight load for the account is superseded. */
  private fetch(id: number, state: LogoState, hash: string): void {
    state.generation++;
    state.hash = hash;
    const generation = state.generation;

    // The holder keeps the callbacks safe if an interceptor answers synchronously inside subscribe().
    const holder: { sub?: Subscription } = {};
    const done = () => { if (holder.sub) state.inflight.delete(holder.sub); };
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
    if (state.hash === null && state.failed.size === 0 && this.signals.get(id)?.() == null) {
      return;
    }
    state.pendingClear = setTimeout(() => {
      state.pendingClear = null;
      if (this.destroyed) {
        return;
      }
      state.generation++;
      state.clearedGeneration = state.generation;
      state.hash = null;
      state.shownHash = null;
      state.failed.clear();
      state.lastRequested = null;
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
      if (last !== null && last !== state.hash && !state.failed.has(last)) {
        this.fetch(id, state, last);
      }
    }, STALE_WINDOW_MS);
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
    // Only the hashes this load resolved stop being failed; never clear() (a server answering per hash would
    // otherwise let one consumer's success re-arm another consumer's failed fetch on every round).
    state.failed.delete(requested);
    if (actual !== null) {
      state.failed.delete(actual);
    }
    // Missing or unusable ETag: resolved under the requested hash; only a hash change refetches.
    if (mismatch) {
      state.hash = actual;
    }
    state.shownHash = state.hash;
    this.show(id, URL.createObjectURL(response.body));
  }

  private onError(id: number, hash: string, generation: number): void {
    if (this.destroyed) {
      return;
    }
    const state = this.states.get(id)!;
    if (state.generation !== generation) {
      // A superseded failure is still a fact about (account, hash), unless a newer load for the same hash is
      // in flight or the load predates the last deletion clear (a same-bytes re-upload must load again).
      if (generation > state.clearedGeneration && state.hash !== hash) {
        state.failed.add(hash);
      }
      return;
    }
    state.failed.add(hash);
    // Keep whatever is shown for the consumers of that hash and wake every consumer (the slot never compares
    // equal): the failed hash's consumers turn inert, the others re-resolve. Bounded: each wake-up follows a
    // new failure, and a failed hash never fetches again until the deletion clear.
    state.hash = state.shownHash;
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
