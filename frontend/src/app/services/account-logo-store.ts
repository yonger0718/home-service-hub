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
   * While the model still says that hash, url() neither refetches nor refreshes again; the model's hash
   * moving off it is the signal that the post-mismatch reload landed. Accepted edge: a second upload of
   * the same hash landing before that reload completes shows the newer bytes only on the next hash change.
   */
  staleRequested: string | null;
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
  /** Accounts whose current image came without an ETag: shown, but a later call refetches. */
  private readonly uncached = new Set<number>();

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
      state = { hash: null, failedHash: null, generation: 0, staleRequested: null };
      this.states.set(id, state);
    }

    if (state.staleRequested !== null) {
      if (hash === state.staleRequested) {
        return slot;
      }
      state.staleRequested = null;
    }

    if (hash === null) {
      if (state.hash !== null || state.failedHash !== null || slot() !== null) {
        state.generation++;
        state.hash = null;
        state.failedHash = null;
        this.uncached.delete(id);
        this.show(id, null);
      }
      return slot;
    }

    if (state.failedHash === hash) {
      return slot;
    }
    const key = `${id}:${hash}`;
    if (state.hash === hash && (this.inflight.has(key) || !this.uncached.has(id))) {
      return slot;
    }

    const shared = state.hash === hash && this.inflight.has(key);
    state.generation++;
    state.hash = hash;
    state.failedHash = null;
    this.uncached.delete(id);
    const generation = state.generation;
    const request$ = shared ? this.inflight.get(key)! : this.startRequest(id, hash, key);

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
    if (state.generation !== generation || !response.body) {
      return;
    }
    const actual = this.parseEtag(response.headers.get('ETag'));
    if (actual === null) {
      this.uncached.add(id);
      this.show(id, URL.createObjectURL(response.body));
      return;
    }
    this.show(id, URL.createObjectURL(response.body));
    if (actual !== requested) {
      state.hash = actual;
      state.staleRequested = requested;
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
