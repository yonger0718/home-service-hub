import { HttpClient, HttpResponse } from '@angular/common/http';
import { Injectable, Signal, WritableSignal, inject, signal, untracked } from '@angular/core';
import { Observable, finalize, shareReplay } from 'rxjs';

import { AccountingService } from './accounting.service';

interface LogoState {
  hash: string | null;
  failedHash: string | null;
  generation: number;
  /**
   * Loop guard, set on an ETag mismatch to the hash that was requested (the model's stale value).
   * Until `staleUntil`, url() neither refetches nor refreshes again for that hash, even if another hash
   * was seen in between (two slots of one account may read different sources). Task 8 rule: every slot
   * should read `logo_hash` from a source that `refreshAccounts()` reloads; the 30 s bound only limits
   * the damage when one does not. Accepted edge: a second upload of the same hash landing before the
   * reload completes shows the newer bytes only after the window or on the next hash change.
   */
  staleRequested: string | null;
  staleUntil: number;
}

/**
 * Fetches uploaded account logos as blobs and exposes one object-URL signal per account.
 * `url()` may be called inside a `computed()`: all synchronous signal writes run untracked.
 */
@Injectable({ providedIn: 'root' })
export class AccountLogoStore {
  private readonly http = inject(HttpClient);
  private readonly accounting = inject(AccountingService);

  private readonly signals = new Map<number, WritableSignal<string | null>>();
  private readonly states = new Map<number, LogoState>();
  private readonly inflight = new Map<string, Observable<HttpResponse<Blob>>>();
  url(accountId: number, hash: string | null): Signal<string | null> {
    return untracked(() => this.resolve(accountId, hash));
  }

  private resolve(id: number, hash: string | null): Signal<string | null> {
    let slot = this.signals.get(id);
    if (!slot) {
      slot = signal<string | null>(null);
      this.signals.set(id, slot);
    }
    let state = this.states.get(id);
    if (!state) {
      state = { hash: null, failedHash: null, generation: 0, staleRequested: null, staleUntil: 0 };
      this.states.set(id, state);
    }

    if (hash !== null && hash === state.staleRequested && Date.now() < state.staleUntil) {
      return slot;
    }

    if (hash === null) {
      if (state.hash !== null || state.failedHash !== null || slot() !== null) {
        state.generation++;
        state.hash = null;
        state.failedHash = null;
            this.show(id, null);
      }
      return slot;
    }

    if (state.failedHash === hash) {
      return slot;
    }
    const key = `${id}:${hash}`;
    if (state.hash === hash) {
      return slot;
    }

    state.generation++;
    state.hash = hash;
    state.failedHash = null;
    const generation = state.generation;
    const request$ = this.startRequest(id, hash, key);

    request$.subscribe({
      next: response => this.onResponse(id, hash, generation, response),
      error: () => this.onError(id, hash, generation),
    });
    return slot;
  }

  private startRequest(id: number, hash: string, key: string): Observable<HttpResponse<Blob>> {
    const request$: Observable<HttpResponse<Blob>> = this.http
      .get(this.accounting.logoImageUrl(id, hash), { responseType: 'blob', observe: 'response' })
      .pipe(
        finalize(() => {
          if (this.inflight.get(key) === request$) {
            this.inflight.delete(key);
          }
        }),
        shareReplay(1),
      );
    this.inflight.set(key, request$);
    return request$;
  }

  private onResponse(id: number, requested: string, generation: number, response: HttpResponse<Blob>): void {
    const state = this.states.get(id)!;
    // Generation check comes before the ETag: a superseded response is dropped whatever its hash says.
    if (state.generation !== generation) {
      return;
    }
    if (!response.body) {
      this.onError(id, requested, generation);
      return;
    }
    const actual = this.parseEtag(response.headers.get('ETag'));
    this.show(id, URL.createObjectURL(response.body));
    // Missing or unusable ETag: resolved under the requested hash; only a hash change refetches.
    if (actual !== null && actual !== requested) {
      state.hash = actual;
      state.staleRequested = requested;
      state.staleUntil = Date.now() + 30_000;
      this.accounting.refreshAccounts();
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
