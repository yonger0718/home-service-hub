import { DestroyRef, Injectable, Signal, inject, signal } from '@angular/core';
import { DOCUMENT } from '@angular/common';

import { SNOOZE_KEY, SnoozeEntry, SnoozeMap, parseSnoozes } from '../components/notifications/snooze';
import { NOTIF_CLOCK } from './notif-clock';

/**
 * This device's snoozes (`localStorage['hh-notif-snooze']`, D4): never sent to the server. Every read and write is
 * guarded: blocked or corrupt storage reads as no snoozes and a failed write keeps the in-memory state. Another tab
 * writing the key reloads it. Expiry and waking are the notification centre's job (it owns the clock).
 */
@Injectable({ providedIn: 'root' })
export class NotifSnoozeStore {
  private readonly clock = inject(NOTIF_CLOCK);
  private readonly view = inject(DOCUMENT).defaultView;
  private readonly state = signal<SnoozeMap>(this.load());
  readonly entries: Signal<SnoozeMap> = this.state.asReadonly();

  constructor() {
    const view = this.view;
    const onStorage = (event: StorageEvent) => {
      if (event.key === SNOOZE_KEY || event.key === null) {
        this.state.set(this.load());
      }
    };
    view?.addEventListener('storage', onStorage);
    inject(DestroyRef).onDestroy(() => view?.removeEventListener('storage', onStorage));
  }

  set(id: string, entry: SnoozeEntry): void {
    this.save({ ...this.state(), [id]: entry });
  }

  remove(...ids: string[]): void {
    const current = this.state();
    if (!ids.some((id) => id in current)) return;
    const next = { ...current };
    for (const id of ids) delete next[id];
    this.save(next);
  }

  clear(): void {
    this.save({});
  }

  private save(next: SnoozeMap): void {
    this.state.set(next);
    try {
      if (Object.keys(next).length === 0) {
        this.storage?.removeItem(SNOOZE_KEY);
      } else {
        this.storage?.setItem(SNOOZE_KEY, JSON.stringify(next));
      }
    } catch {
      // Blocked or full: keep the in-memory state.
    }
  }

  private load(): SnoozeMap {
    try {
      return parseSnoozes(this.storage?.getItem(SNOOZE_KEY) ?? null, this.clock());
    } catch {
      return {};
    }
  }

  private get storage(): Storage | null {
    try {
      return this.view?.localStorage ?? null;
    } catch {
      return null;
    }
  }
}
