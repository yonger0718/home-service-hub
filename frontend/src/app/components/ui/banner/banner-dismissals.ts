import { Injectable, signal } from '@angular/core';

export const BANNER_DISMISSALS_KEY = 'hh-banner-dismissed';

function readStored(): string[] {
  try {
    const raw = localStorage.getItem(BANNER_DISMISSALS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

/** Remembers which 資訊 banners the owner closed. Memory is authoritative; storage is best effort. */
@Injectable({ providedIn: 'root' })
export class BannerDismissals {
  private readonly ids = signal<ReadonlySet<string>>(new Set(readStored()));

  isDismissed(id: string): boolean {
    return this.ids().has(id);
  }

  dismiss(id: string): void {
    if (this.ids().has(id)) return;
    // Merge with storage so an id dismissed in another tab is not dropped.
    const next = new Set([...readStored(), ...this.ids(), id]);
    this.ids.set(next);
    try {
      localStorage.setItem(BANNER_DISMISSALS_KEY, JSON.stringify([...next]));
    } catch {
      // storage unavailable: stays closed for this session only
    }
  }
}
