/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppearanceService } from './appearance.service';

type Keys = Record<string, string>;

const html = readFileSync(join(process.cwd(), 'src/index.html'), 'utf8');
const scriptBody = /<script>([\s\S]*?)<\/script>/.exec(html)![1];

function runScript(keys: Keys, osDark: boolean, throwing = false): void {
  const store = new Map(Object.entries(keys));
  const stubWindow = {
    localStorage: {
      getItem: (k: string) => {
        if (throwing) throw new Error('blocked');
        return store.has(k) ? store.get(k)! : null;
      },
      setItem: vi.fn(),
      removeItem: vi.fn(),
    },
    matchMedia: () => ({ matches: osDark }),
  };
  new Function('window', 'document', scriptBody)(stubWindow, document);
  expect(stubWindow.localStorage.setItem).not.toHaveBeenCalled();
  expect(stubWindow.localStorage.removeItem).not.toHaveBeenCalled();
}

function setOs(matches: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn().mockReturnValue({
      matches,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });
}

const rootDark = () => document.documentElement.classList.contains('app-dark-mode');
const reset = () => {
  TestBed.resetTestingModule();
  localStorage.clear();
  document.documentElement.classList.remove('app-dark-mode');
  document.documentElement.removeAttribute('data-gainloss');
};

describe('pre-paint script in index.html', () => {
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

  beforeEach(reset);

  const themes: Array<string | null> = [null, 'light', 'dark', 'system', 'blue'];
  const darks: Array<string | null> = [null, '0', '1', 'x'];

  for (const theme of themes) {
    for (const dark of darks) {
      for (const osDark of [false, true]) {
        it(`matches the service: hh-theme=${theme} hh-dark=${dark} os=${osDark ? 'dark' : 'light'}`, async () => {
          const keys: Keys = {};
          if (theme !== null) keys['hh-theme'] = theme;
          if (dark !== null) keys['hh-dark'] = dark;

          runScript({ ...keys }, osDark);
          const scriptDark = rootDark();

          reset();
          setOs(osDark);
          for (const [k, v] of Object.entries(keys)) localStorage.setItem(k, v);
          const service = TestBed.inject(AppearanceService);

          expect(scriptDark).toBe(await firstValueFrom(service.dark$));
        });
      }
    }
  }

  for (const osDark of [false, true]) {
    it(`falls back to the OS when storage throws (os=${osDark ? 'dark' : 'light'})`, () => {
      runScript({ 'hh-theme': 'dark' }, osDark, true);
      expect(rootDark()).toBe(osDark);
    });
  }

  it('sets data-gainloss from hh-gainloss', () => {
    runScript({}, false);
    expect(document.documentElement.getAttribute('data-gainloss')).toBe('asian');
    runScript({ 'hh-gainloss': 'western' }, false);
    expect(document.documentElement.getAttribute('data-gainloss')).toBe('western');
  });
});
