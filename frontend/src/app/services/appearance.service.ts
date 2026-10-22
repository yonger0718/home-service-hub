import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export type GainLossConvention = 'asian' | 'western';
export type ThemeMode = 'light' | 'dark' | 'system';

const THEME_KEY = 'hh-theme';
const LEGACY_DARK_KEY = 'hh-dark';
const GAIN_LOSS_KEY = 'hh-gainloss';
const DARK_QUERY = '(prefers-color-scheme: dark)';

@Injectable({ providedIn: 'root' })
export class AppearanceService {
  private readonly osQuery: MediaQueryList | null = this.createOsQuery();
  private osListening = false;

  readonly theme$ = new BehaviorSubject<ThemeMode>(this.resolveInitialTheme());
  /** Effective dark state, derived from `theme$` and (for `system`) the OS. */
  readonly dark$ = new BehaviorSubject<boolean>(this.effectiveDark(this.theme$.value));
  readonly gainLoss$ = new BehaviorSubject<GainLossConvention>(this.readInitialGainLoss());

  private readonly onOsChange = (event: { matches: boolean }): void => {
    if (this.theme$.value !== 'system') return;
    this.dark$.next(event.matches);
    this.apply();
  };

  constructor() {
    this.syncOsListener();
    this.apply();
  }

  initialize(): void {
    this.apply();
  }

  setTheme(value: ThemeMode): void {
    this.theme$.next(value);
    this.dark$.next(this.effectiveDark(value));
    this.persist(THEME_KEY, value);
    this.syncOsListener();
    this.apply();
  }

  setDark(value: boolean): void {
    this.setTheme(value ? 'dark' : 'light');
  }

  setGainLoss(value: GainLossConvention): void {
    this.gainLoss$.next(value);
    this.persist(GAIN_LOSS_KEY, value);
    this.apply();
  }

  private effectiveDark(theme: ThemeMode): boolean {
    if (theme === 'system') return this.osQuery?.matches ?? false;
    return theme === 'dark';
  }

  private syncOsListener(): void {
    if (!this.osQuery) return;
    const wanted = this.theme$.value === 'system';
    if (wanted && !this.osListening) {
      this.osQuery.addEventListener('change', this.onOsChange);
      this.osListening = true;
    } else if (!wanted && this.osListening) {
      this.osQuery.removeEventListener('change', this.onOsChange);
      this.osListening = false;
    }
  }

  private persist(key: string, value: string): void {
    try {
      this.storage?.setItem(key, value);
    } catch {
      // localStorage write failed (quota / blocked); keep in-memory state
    }
  }

  private apply(): void {
    if (typeof document === 'undefined') return;

    const root = document.documentElement;
    root.classList.toggle('app-dark-mode', this.dark$.value);
    root.setAttribute('data-gainloss', this.gainLoss$.value);
  }

  /**
   * A valid `hh-theme` wins; a present but invalid one means `system`.
   * `hh-dark` is read only when `hh-theme` is absent, and migrated once
   * (only for '1' / '0').
   */
  private resolveInitialTheme(): ThemeMode {
    const stored = this.read(THEME_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
    if (stored !== null) return 'system';

    const legacy = this.read(LEGACY_DARK_KEY);
    if (legacy === '1' || legacy === '0') {
      const migrated: ThemeMode = legacy === '1' ? 'dark' : 'light';
      this.persist(THEME_KEY, migrated);
      try {
        this.storage?.removeItem(LEGACY_DARK_KEY);
      } catch {
        // ignore
      }
      return migrated;
    }
    return 'system';
  }

  private readInitialGainLoss(): GainLossConvention {
    return this.read(GAIN_LOSS_KEY) === 'western' ? 'western' : 'asian';
  }

  private read(key: string): string | null {
    try {
      return this.storage?.getItem(key) ?? null;
    } catch {
      return null;
    }
  }

  private createOsQuery(): MediaQueryList | null {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null;
    return window.matchMedia(DARK_QUERY);
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
