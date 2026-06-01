import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppearanceService } from './appearance.service';

describe('AppearanceService', () => {
  function setMatchMedia(matches: boolean) {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: vi.fn().mockReturnValue({
        matches,
        media: '(prefers-color-scheme: dark)',
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    });
  }

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

    expect(localStorage.getItem('hh-dark')).toBe('1');
    expect(localStorage.getItem('hh-gainloss')).toBe('western');
  });

  it('restores persisted selections on initialization', () => {
    localStorage.setItem('hh-dark', '1');
    localStorage.setItem('hh-gainloss', 'western');

    const service = TestBed.inject(AppearanceService);

    expect(service.dark$.value).toBe(true);
    expect(service.gainLoss$.value).toBe('western');
    expect(document.documentElement.classList.contains('app-dark-mode')).toBe(true);
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
});
