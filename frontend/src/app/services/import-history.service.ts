import { Injectable, OnDestroy, Signal, computed, signal } from '@angular/core';
import { ImportAttempt, ImportType } from '../components/portfolio/import/import-math';

const HISTORY_KEY = 'hh-import-history';
const MAX_ATTEMPTS = 10;
const TYPES: ReadonlySet<string> = new Set<ImportType>(['transactions', 'dividends', 'broker']);

function isAttempt(value: unknown): value is ImportAttempt {
  const a = value as Partial<ImportAttempt> | null;
  return (
    typeof a === 'object' &&
    a !== null &&
    typeof a.at === 'string' &&
    typeof a.file === 'string' &&
    typeof a.type === 'string' &&
    TYPES.has(a.type) &&
    typeof a.ok === 'boolean'
  );
}

/**
 * Device-local import attempts (`localStorage['hh-import-history']`, newest first, max 10).
 * Injects nothing so the nav badge can depend on it without a cycle.
 */
@Injectable({ providedIn: 'root' })
export class ImportHistoryService implements OnDestroy {
  private readonly list = signal<ImportAttempt[]>(this.load());

  readonly attempts: Signal<ImportAttempt[]> = this.list.asReadonly();
  readonly lastFailed: Signal<boolean> = computed(() => this.list()[0]?.ok === false);

  private readonly onStorage = (event: StorageEvent): void => {
    if (event.key === null || event.key === HISTORY_KEY) this.list.set(this.load());
  };

  constructor() {
    if (typeof window !== 'undefined') window.addEventListener('storage', this.onStorage);
  }

  ngOnDestroy(): void {
    if (typeof window !== 'undefined') window.removeEventListener('storage', this.onStorage);
  }

  record(attempt: ImportAttempt): void {
    const next = [attempt, ...this.list()].slice(0, MAX_ATTEMPTS);
    this.list.set(next);
    try {
      this.storage?.setItem(HISTORY_KEY, JSON.stringify(next));
    } catch {
      // private mode / quota: keep the in-memory list
    }
  }

  clear(): void {
    this.list.set([]);
    try {
      this.storage?.removeItem(HISTORY_KEY);
    } catch {
      // blocked storage: in-memory list is already empty
    }
  }

  private load(): ImportAttempt[] {
    try {
      const raw = this.storage?.getItem(HISTORY_KEY);
      if (!raw) return [];
      const parsed: unknown = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.filter(isAttempt).slice(0, MAX_ATTEMPTS) : [];
    } catch {
      return [];
    }
  }

  private get storage(): Storage | null {
    if (typeof window === 'undefined') return null;
    try {
      return window.localStorage;
    } catch {
      return null;
    }
  }
}
