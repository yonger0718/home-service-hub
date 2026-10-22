import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppearanceService } from './appearance.service';

describe('AppearanceService', () => {
  let osListeners: Array<(e: { matches: boolean }) => void>;
  let mql: { matches: boolean; addEventListener: ReturnType<typeof vi.fn>; removeEventListener: ReturnType<typeof vi.fn> };

  function setMatchMedia(matches: boolean) {
    osListeners = [];
    mql = {
      matches,
      addEventListener: vi.fn((_t: string, fn: (e: { matches: boolean }) => void) => osListeners.push(fn)),
      removeEventListener: vi.fn((_t: string, fn: (e: { matches: boolean }) => void) => {
        osListeners = osListeners.filter(l => l !== fn);
      }),
    };
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: vi.fn().mockReturnValue(mql),
    });
  }

  function fireOs(matches: boolean) {
    mql.matches = matches;
    [...osListeners].forEach(l => l({ matches }));
  }

  const rootDark = () => document.documentElement.classList.contains('app-dark-mode');

  const originalMatchMedia = Object.getOwnPropertyDescriptor(window, 'matchMedia');

  afterEach(() => {
    vi.restoreAllMocks();
    if (originalMatchMedia) Object.defineProperty(window, 'matchMedia', originalMatchMedia);
    else delete (window as { matchMedia?: unknown }).matchMedia;
    TestBed.resetTestingModule();
    localStorage.clear();
    document.documentElement.classList.remove('app-dark-mode');
    document.documentElement.removeAttribute('data-gainloss');
  });

  beforeEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
    document.documentElement.classList.remove('app-dark-mode');
    document.documentElement.removeAttribute('data-gainloss');
    setMatchMedia(false);
  });

  it('persists dark mode and gain/loss selections to localStorage', () => {
    const service = TestBed.inject(AppearanceService);

    service.setDark(true);
    service.setGainLoss('western');

    expect(localStorage.getItem('hh-theme')).toBe('dark');
    expect(localStorage.getItem('hh-gainloss')).toBe('western');
  });

  it('restores persisted selections on initialization', () => {
    localStorage.setItem('hh-theme', 'dark');
    localStorage.setItem('hh-gainloss', 'western');

    const service = TestBed.inject(AppearanceService);

    expect(service.theme$.value).toBe('dark');
    expect(service.dark$.value).toBe(true);
    expect(service.gainLoss$.value).toBe('western');
    expect(rootDark()).toBe(true);
    expect(document.documentElement.getAttribute('data-gainloss')).toBe('western');
  });

  it('falls back to OS dark preference and asian gain/loss convention', () => {
    setMatchMedia(true);

    const service = TestBed.inject(AppearanceService);

    expect(service.dark$.value).toBe(true);
    expect(service.gainLoss$.value).toBe('asian');
    expect(document.documentElement.classList.contains('app-dark-mode')).toBe(true);
    expect(document.documentElement.getAttribute('data-gainloss')).toBe('asian');
  });

  it('applies root attributes and emits observable updates from setters', () => {
    const service = TestBed.inject(AppearanceService);
    const darkValues: boolean[] = [];
    const gainLossValues: string[] = [];
    service.dark$.subscribe(value => darkValues.push(value));
    service.gainLoss$.subscribe(value => gainLossValues.push(value));

    service.setDark(true);
    service.setDark(false);
    service.setGainLoss('western');

    expect(darkValues).toEqual([false, true, false]);
    expect(gainLossValues).toEqual(['asian', 'western']);
    expect(document.documentElement.classList.contains('app-dark-mode')).toBe(false);
    expect(document.documentElement.getAttribute('data-gainloss')).toBe('western');
  });

  it('setTheme(dark) updates state, root class and storage', () => {
    const service = TestBed.inject(AppearanceService);
    service.setTheme('dark');
    expect(service.theme$.value).toBe('dark');
    expect(service.dark$.value).toBe(true);
    expect(rootDark()).toBe(true);
    expect(localStorage.getItem('hh-theme')).toBe('dark');
  });

  it('system follows the OS live', () => {
    const service = TestBed.inject(AppearanceService);
    service.setTheme('system');
    expect(service.dark$.value).toBe(false);
    fireOs(true);
    expect(service.dark$.value).toBe(true);
    expect(rootDark()).toBe(true);
  });

  it('leaving system detaches the listener', () => {
    const service = TestBed.inject(AppearanceService);
    service.setTheme('system');
    service.setTheme('light');
    fireOs(true);
    expect(service.dark$.value).toBe(false);
    expect(rootDark()).toBe(false);
  });

  it('re-entering system follows the OS again', () => {
    const service = TestBed.inject(AppearanceService);
    service.setTheme('system');
    service.setTheme('light');
    service.setTheme('system');
    fireOs(true);
    expect(service.dark$.value).toBe(true);
  });

  it.each([
    ['0', 'light'],
    ['1', 'dark'],
  ])('migrates hh-dark=%s once', (old, expected) => {
    localStorage.setItem('hh-dark', old);
    const service = TestBed.inject(AppearanceService);
    expect(service.theme$.value).toBe(expected);
    expect(localStorage.getItem('hh-theme')).toBe(expected);
    expect(localStorage.getItem('hh-dark')).toBeNull();
  });

  it('hh-theme wins over hh-dark and leaves hh-dark as is', () => {
    localStorage.setItem('hh-theme', 'light');
    localStorage.setItem('hh-dark', '1');
    const service = TestBed.inject(AppearanceService);
    expect(service.theme$.value).toBe('light');
    expect(localStorage.getItem('hh-dark')).toBe('1');
  });

  it('defaults to system', () => {
    setMatchMedia(true);
    const service = TestBed.inject(AppearanceService);
    expect(service.theme$.value).toBe('system');
    expect(service.dark$.value).toBe(true);
  });

  it('invalid hh-theme means system and never falls through to hh-dark', () => {
    localStorage.setItem('hh-theme', 'blue');
    localStorage.setItem('hh-dark', '1');
    const service = TestBed.inject(AppearanceService);
    expect(service.theme$.value).toBe('system');
    expect(service.dark$.value).toBe(false);
    expect(localStorage.getItem('hh-dark')).toBe('1');
    expect(localStorage.getItem('hh-theme')).toBe('blue');
  });

  it('invalid hh-dark with no hh-theme means system and is not removed', () => {
    localStorage.setItem('hh-dark', 'x');
    const service = TestBed.inject(AppearanceService);
    expect(service.theme$.value).toBe('system');
    expect(localStorage.getItem('hh-dark')).toBe('x');
    expect(localStorage.getItem('hh-theme')).toBeNull();
  });

  it('works with blocked storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const service = TestBed.inject(AppearanceService);
    expect(service.theme$.value).toBe('system');
    expect(service.gainLoss$.value).toBe('asian');
    service.setTheme('dark');
    expect(service.dark$.value).toBe(true);
    expect(rootDark()).toBe(true);
  });

  it('updates the root class before dark$ emits (setTheme)', () => {
    const service = TestBed.inject(AppearanceService);
    const seen: Array<[boolean, boolean]> = [];
    service.dark$.subscribe(value => seen.push([value, rootDark()]));
    service.setTheme('dark');
    service.setTheme('light');
    expect(seen).toEqual([
      [false, false],
      [true, true],
      [false, false],
    ]);
  });

  it('updates the root class before dark$ emits (OS change on system)', () => {
    const service = TestBed.inject(AppearanceService);
    service.setTheme('system');
    const seen: Array<[boolean, boolean]> = [];
    service.dark$.subscribe(value => seen.push([value, rootDark()]));
    fireOs(true);
    fireOs(false);
    expect(seen).toEqual([
      [false, false],
      [true, true],
      [false, false],
    ]);
  });

  it('updates data-gainloss before gainLoss$ emits', () => {
    const service = TestBed.inject(AppearanceService);
    const seen: Array<[string, string | null]> = [];
    service.gainLoss$.subscribe(value =>
      seen.push([value, document.documentElement.getAttribute('data-gainloss')]),
    );
    service.setGainLoss('western');
    service.setGainLoss('asian');
    expect(seen).toEqual([
      ['asian', 'asian'],
      ['western', 'western'],
      ['asian', 'asian'],
    ]);
  });

  it('keeps hh-dark when writing hh-theme fails during migration', () => {
    localStorage.setItem('hh-dark', '1');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    const service = TestBed.inject(AppearanceService);
    expect(service.theme$.value).toBe('dark');
    expect(localStorage.getItem('hh-dark')).toBe('1');
    expect(localStorage.getItem('hh-theme')).toBeNull();
  });

  it('setDark maps to setTheme', () => {
    const service = TestBed.inject(AppearanceService);
    service.setDark(true);
    expect(service.theme$.value).toBe('dark');
    expect(localStorage.getItem('hh-theme')).toBe('dark');
    service.setDark(false);
    expect(service.theme$.value).toBe('light');
  });
});
