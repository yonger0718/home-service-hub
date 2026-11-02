import { HttpClient, HttpResponse } from '@angular/common/http';
import { Injectable, OnDestroy, Signal, WritableSignal, inject, signal, untracked } from '@angular/core';

import { AccountingService } from './accounting.service';

/** Mismatch guard window; on expiry the account re-resolves its last requested hash (see `armStale`). */
const STALE_WINDOW_MS = 30_000;

/** Returned for a null hash: that consumer shows nothing without touching the account's shared slot. */
const NO_LOGO: Signal<string | null> = signal<string | null>(null).asReadonly();

interface LogoState {
  hash: string | null;
  failedHash: string | null;
  generation: number;
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

  url(accountId: number, hash: string | null): Signal<string | null> {
    return untracked(() => this.resolve(accountId, hash));
  }

  ngOnDestroy(): void {
    for (const state of this.states.values()) {
      if (state.pendingClear !== null) clearTimeout(state.pendingClear);
      if (state.staleTimer !== null) clearTimeout(state.staleTimer);
      state.pendingClear = state.staleTimer = null;
    }
  }

  private resolve(id: number, hash: string | null): Signal<string | null> {
    if (hash === null) {
      this.scheduleClear(id);
      return NO_LOGO;
    }

    let slot = this.signals.get(id);
    if (!slot) {
      slot = signal<string | null>(null);
      this.signals.set(id, slot);
    }
    let state = this.states.get(id);
    if (!state) {
      state = {
        hash: null, failedHash: null, generation: 0, staleRequested: null, staleUntil: 0, staleTimer: null,
        lastRequested: null, pendingClear: null,
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
    if (state.failedHash === hash || state.hash === hash) {
      return slot;
    }
    this.fetch(id, state, hash);
    return slot;
  }

  /** Starts a generation-protected load of `hash`; any earlier in-flight load for the account is superseded. */
  private fetch(id: number, state: LogoState, hash: string): void {
    state.generation++;
    state.hash = hash;
    state.failedHash = null;
    const generation = state.generation;

    this.http.get(this.accounting.logoImageUrl(id, hash), { responseType: 'blob', observe: 'response' }).subscribe({
      next: response => this.onResponse(id, hash, generation, response),
      error: () => this.onError(id, hash, generation),
    });
  }

  /** Clears the account on the next macrotask unless a non-null url() call for it comes first. */
  private scheduleClear(id: number): void {
    const state = this.states.get(id);
    if (!state || state.pendingClear !== null) {
      return;
    }
    if (state.hash === null && state.failedHash === null && this.signals.get(id)?.() == null) {
      return;
    }
    state.pendingClear = setTimeout(() => {
      state.pendingClear = null;
      state.generation++;
      state.hash = null;
      state.failedHash = null;
      state.lastRequested = null;
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
      state.staleRequested = null;
      state.staleUntil = 0;
      const last = state.lastRequested;
      if (last !== null && last !== state.hash && last !== state.failedHash) {
        this.fetch(id, state, last);
      }
    }, STALE_WINDOW_MS);
  }

  private onResponse(id: number, requested: string, generation: number, response: HttpResponse<Blob>): void {
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
    this.show(id, URL.createObjectURL(response.body));
    // Missing or unusable ETag: resolved under the requested hash; only a hash change refetches.
    if (mismatch) {
      state.hash = actual;
    }
  }

  private onError(id: number, hash: string, generation: number): void {
    const state = this.states.get(id)!;
    if (state.generation !== generation) {
      return;
    }
    state.failedHash = hash;
    this.show(id, null);
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
      setTimeout(() => {
        const inUse = [...this.signals.values()].some(s => s() === old);
        if (!inUse) {
          URL.revokeObjectURL(old);
        }
      }, 0);
    }
  }
}
