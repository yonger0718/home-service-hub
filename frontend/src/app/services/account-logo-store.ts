import { HttpClient, HttpContext, HttpResponse } from '@angular/common/http';
import { Injectable, OnDestroy, Signal, WritableSignal, inject, signal, untracked } from '@angular/core';
import { Subscription } from 'rxjs';

import { SILENT_HTTP_ERRORS } from '../interceptors/error-logging.interceptor';
import { AccountingService } from './accounting.service';

/**
 * Lease model (rev 5.0).
 *
 * A consumer never asks the store to fetch by reading a signal. It registers a lease for its current
 * (account, hash) with `acquire()` and releases it when that pair changes or the consumer goes away; the per-account
 * image signal from `slot()` is read-only and reading it never starts anything. Fetching is driven only by external
 * events — a lease acquired or released, or a failure window expiring — so the store can tell "the model moved to
 * hash X" (a new lease) from "the same consumer re-read X" (nothing), which the pull model could not.
 *
 * Per account, the **target** is the hash of the newest non-null lease (null when every lease is null or none exists).
 * `evaluate()` fetches the target's effective hash (`aliases` maps a requested hash to the ETag hash the server
 * answered with) unless it is already shown, in flight or inside its 30 s failure window. Only the current-generation
 * response is honoured; a superseded response, success or error, is ignored entirely. A success sets the shown hash to
 * the ETag hash (else the requested one), records the alias and refreshes the accounts once per (account, requested
 * hash) when they differ, and replaces the object URL (the old one is revoked on the next macrotask). A failure (any
 * HTTP error, a null or 0-byte body) keeps the shown image, records the hash in `failed` and arms one 30 s timer per
 * account that empties `failed` and evaluates once. When the target becomes null a deferred clear (`setTimeout(0)`)
 * runs unless a non-null lease arrives first: it bumps the generation, forgets aliases and failures and blanks the slot.
 *
 * Bounds: GETs per account ≤ acquires + releases that change the target, plus at most one per 30 s while the live
 * target keeps failing. A re-upload of an earlier logo (A → B → A) loads A again because the newest lease is the target.
 * Accepted edge: a one-shot stale detail acquired after the list, when the browser cache serves its old bytes with
 * the old ETag, shows the old image until the next acquire.
 *
 * `acquire()` may run inside an effect and `slot()` inside a computed: every synchronous signal write is untracked.
 */

/** How long a failed (account, hash) suppresses fetching before `failed` is emptied and the target re-evaluated. */
const FAILURE_WINDOW_MS = 30_000;

/** Constant null signal handed out after destroy. */
const NO_LOGO: Signal<string | null> = signal<string | null>(null).asReadonly();

/** A live consumer binding; `release()` is idempotent. */
export interface LogoLease {
  release(): void;
}

const NOOP_LEASE: LogoLease = { release: () => undefined };

interface LogoState {
  /** The account's image (object URL or null); the only signal consumers read. */
  slot: WritableSignal<string | null>;
  readonly readonlySlot: Signal<string | null>;
  /** Hash the slot's current URL was resolved under (ETag hash when present); null when nothing is shown. */
  shownHash: string | null;
  /** Requested hash of the current-generation load in flight; null when none. */
  pending: string | null;
  /** Bumped by every fetch and by the deferred clear; a response from an older generation is ignored entirely. */
  generation: number;
  /** Every in-flight subscription, unsubscribed on destroy. */
  inflight: Set<Subscription>;
  /** Live leases by global sequence number (insertion-ordered, so the last non-null entry is the newest). */
  leases: Map<number, string | null>;
  /** Requested hash → ETag hash the server answered with. */
  aliases: Map<string, string>;
  /** Requested hashes the account has refreshed the accounts for (one refresh per pair). */
  refreshed: Set<string>;
  /** Hashes whose current-generation load failed inside the open window. */
  failed: Set<string>;
  /** One per account; on expiry empties `failed` and evaluates once. */
  failTimer: ReturnType<typeof setTimeout> | null;
  /** Deferred clear, scheduled when the target became null; any non-null acquire cancels it. */
  clearTimer: ReturnType<typeof setTimeout> | null;
}

/** Fetches uploaded account logos as blobs and exposes one object-URL signal per account (model: module comment). */
@Injectable({ providedIn: 'root' })
export class AccountLogoStore implements OnDestroy {
  private readonly http = inject(HttpClient);
  private readonly accounting = inject(AccountingService);

  private readonly states = new Map<number, LogoState>();
  /** Pending revoke timers and the URL each one would revoke. */
  private readonly revokeTimers = new Map<ReturnType<typeof setTimeout>, string>();
  private seq = 0;
  private destroyed = false;

  /** Read-only image signal of an account (object URL or null). Created lazily; reading it never fetches. */
  slot(accountId: number): Signal<string | null> {
    if (this.destroyed) {
      return NO_LOGO;
    }
    return untracked(() => this.state(accountId).readonlySlot);
  }

  /** Registers a live consumer binding for (account, hash); may start a fetch. Call outside reactive contexts. */
  acquire(accountId: number, hash: string | null): LogoLease {
    if (this.destroyed) {
      return NOOP_LEASE;
    }
    return untracked(() => {
      const state = this.state(accountId);
      const seq = ++this.seq;
      state.leases.set(seq, hash);
      if (hash !== null) {
        this.cancelClear(state);
        this.evaluate(accountId);
      } else if (this.target(state) === null) {
        this.scheduleClear(accountId);
      }
      return { release: () => this.release(accountId, seq) };
    });
  }

  /** Cancels every timer and in-flight load, then revokes every object URL the store still owns. */
  ngOnDestroy(): void {
    this.destroyed = true;
    const owned = new Set<string>();
    for (const state of this.states.values()) {
      for (const timer of [state.clearTimer, state.failTimer]) {
        if (timer !== null) clearTimeout(timer);
      }
      state.clearTimer = state.failTimer = null;
      for (const sub of state.inflight) sub.unsubscribe();
      state.inflight.clear();
      const current = untracked(state.slot);
      if (current) owned.add(current);
    }
    for (const [timer, old] of this.revokeTimers) {
      clearTimeout(timer);
      owned.add(old);
    }
    this.revokeTimers.clear();
    for (const u of owned) {
      // Never throw from teardown (jsdom has no revokeObjectURL; specs restore the original after each test).
      try { URL.revokeObjectURL(u); } catch { /* ignore */ }
    }
  }

  private state(id: number): LogoState {
    let state = this.states.get(id);
    if (!state) {
      const slot = signal<string | null>(null);
      state = {
        slot, readonlySlot: slot.asReadonly(), shownHash: null, pending: null, generation: 0, inflight: new Set(),
        leases: new Map(), aliases: new Map(), refreshed: new Set(), failed: new Set(), failTimer: null, clearTimer: null,
      };
      this.states.set(id, state);
    }
    return state;
  }

  /** Hash of the newest non-null lease, or null. */
  private target(state: LogoState): string | null {
    let target: string | null = null;
    for (const hash of state.leases.values()) {
      if (hash !== null) target = hash;
    }
    return target;
  }

  private release(id: number, seq: number): void {
    const state = this.states.get(id);
    if (this.destroyed || !state || !state.leases.has(seq)) {
      return;
    }
    untracked(() => {
      const before = this.target(state);
      state.leases.delete(seq);
      const after = this.target(state);
      if (after === null) {
        this.scheduleClear(id);
      } else if (after !== before) {
        this.evaluate(id);
      }
    });
  }

  /** Fetches the target's effective hash unless it is shown, in flight or inside its failure window. */
  private evaluate(id: number): void {
    const state = this.states.get(id);
    if (this.destroyed || !state) {
      return;
    }
    const target = this.target(state);
    if (target === null) {
      return; // the deferred clear handles it
    }
    const effective = state.aliases.get(target) ?? target;
    if (effective === state.shownHash || effective === state.pending || state.failed.has(effective)) {
      return;
    }
    this.fetch(id, state, effective);
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

  private onResponse(id: number, requested: string, generation: number, response: HttpResponse<Blob>): void {
    if (this.destroyed) {
      return;
    }
    const state = this.states.get(id)!;
    if (state.generation !== generation) {
      return; // superseded: ignored entirely
    }
    if (!response.body || response.body.size === 0) {
      this.onError(id, requested, generation);
      return;
    }
    const actual = this.parseEtag(response.headers.get('ETag')) ?? requested;
    untracked(() => {
      if (actual !== requested) {
        // Flatten chains so every alias points at the hash now shown.
        for (const [key, value] of state.aliases) {
          if (value === requested) state.aliases.set(key, actual);
        }
        state.aliases.set(requested, actual);
        if (!state.refreshed.has(requested)) {
          state.refreshed.add(requested);
          this.accounting.refreshAccounts();
        }
      }
      state.shownHash = actual;
      state.failed.delete(requested);
      state.failed.delete(actual);
      state.pending = null;
      this.show(state, URL.createObjectURL(response.body!));
    });
  }

  private onError(id: number, requested: string, generation: number): void {
    if (this.destroyed) {
      return;
    }
    const state = this.states.get(id)!;
    if (state.generation !== generation) {
      return; // superseded: ignored entirely
    }
    state.pending = null;
    state.failed.add(requested);
    if (state.failTimer !== null) {
      clearTimeout(state.failTimer);
    }
    state.failTimer = setTimeout(() => {
      state.failTimer = null;
      if (this.destroyed) {
        return;
      }
      state.failed.clear();
      this.evaluate(id);
    }, FAILURE_WINDOW_MS);
  }

  /** Clears the account on the next macrotask unless a non-null lease arrives first (or the target is non-null then). */
  private scheduleClear(id: number): void {
    const state = this.states.get(id)!;
    if (state.clearTimer !== null) {
      return;
    }
    const idle = state.shownHash === null && state.pending === null && state.failed.size === 0
      && state.aliases.size === 0 && untracked(state.slot) === null;
    if (idle) {
      return;
    }
    state.clearTimer = setTimeout(() => {
      state.clearTimer = null;
      if (this.destroyed || this.target(state) !== null) {
        return;
      }
      state.generation++; // drops every in-flight response
      state.pending = null;
      state.shownHash = null;
      state.aliases.clear();
      state.refreshed.clear();
      state.failed.clear();
      if (state.failTimer !== null) {
        clearTimeout(state.failTimer);
        state.failTimer = null;
      }
      untracked(() => this.show(state, null));
    }, 0);
  }

  private cancelClear(state: LogoState): void {
    if (state.clearTimer !== null) {
      clearTimeout(state.clearTimer);
      state.clearTimer = null;
    }
  }

  private parseEtag(raw: string | null): string | null {
    if (!raw || raw.startsWith('W/')) {
      return null;
    }
    const hex = raw.replace(/"/g, '').slice(0, 16);
    return /^[0-9a-f]{16}$/i.test(hex) ? hex.toLowerCase() : null;
  }

  /** Sets the account's slot first, then revokes the old URL on the next macrotask unless still in use. */
  private show(state: LogoState, next: string | null): void {
    const old = state.slot();
    state.slot.set(next);
    if (old && old !== next) {
      const timer = setTimeout(() => {
        this.revokeTimers.delete(timer);
        if (this.destroyed) {
          return;
        }
        const inUse = [...this.states.values()].some(s => untracked(s.slot) === old);
        if (!inUse) {
          URL.revokeObjectURL(old);
        }
      }, 0);
      this.revokeTimers.set(timer, old);
    }
  }
}
