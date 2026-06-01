import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export type GainLossConvention = 'asian' | 'western';

const DARK_KEY = 'hh-dark';
const GAIN_LOSS_KEY = 'hh-gainloss';

@Injectable({ providedIn: 'root' })
export class AppearanceService {
  readonly dark$ = new BehaviorSubject<boolean>(this.readInitialDark());
  readonly gainLoss$ = new BehaviorSubject<GainLossConvention>(this.readInitialGainLoss());

  constructor() {
    this.apply();
  }

  initialize(): void {
    this.apply();
  }

  setDark(value: boolean): void {
    this.dark$.next(value);
    this.persist(DARK_KEY, value ? '1' : '0');
    this.apply();
  }

  setGainLoss(value: GainLossConvention): void {
    this.gainLoss$.next(value);
    this.persist(GAIN_LOSS_KEY, value);
    this.apply();
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

  private readInitialDark(): boolean {
    const stored = this.storage?.getItem(DARK_KEY);
    if (stored === '1') return true;
    if (stored === '0') return false;

    return this.prefersDark();
  }

  private readInitialGainLoss(): GainLossConvention {
    return this.storage?.getItem(GAIN_LOSS_KEY) === 'western' ? 'western' : 'asian';
  }

  private prefersDark(): boolean {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
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
