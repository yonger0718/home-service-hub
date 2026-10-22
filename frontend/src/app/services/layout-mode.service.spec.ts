import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { LayoutMode, LayoutModeService, MATCH_MEDIA } from './layout-mode.service';

/** Evaluates `(min|max)-(width|height): Npx` conditions, so a changed query constant changes the result. */
function evaluate(query: string, width: number, height: number): boolean {
  const conditions = [...query.matchAll(/\((min|max)-(width|height):\s*([\d.]+)px\)/g)];
  if (conditions.length === 0) {
    throw new Error(`Unsupported media query: ${query}`);
  }
  return conditions.every(([, bound, axis, px]) => {
    const size = axis === 'width' ? width : height;
    return bound === 'min' ? size >= Number(px) : size <= Number(px);
  });
}

function fakeMatchMedia(initialWidth: number, initialHeight = 900) {
  let width = initialWidth;
  let height = initialHeight;
  const listeners = new Set<() => void>();
  const matchMedia = (query: string) =>
    ({
      media: query,
      get matches() {
        return evaluate(query, width, height);
      },
      addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
    }) as unknown as MediaQueryList;
  return {
    matchMedia,
    resize(next: number, nextHeight = height) {
      width = next;
      height = nextHeight;
      listeners.forEach(listener => listener());
    },
  };
}

function create(width: number, height?: number) {
  const media = fakeMatchMedia(width, height);
  TestBed.configureTestingModule({ providers: [{ provide: MATCH_MEDIA, useValue: media.matchMedia }] });
  return { service: TestBed.inject(LayoutModeService), media };
}

describe('LayoutModeService', () => {
  it.each<[number, LayoutMode]>([
    [390, 'phone'],
    [759, 'phone'],
    [760, 'sheet'],
    [820, 'sheet'],
    [1023, 'sheet'],
    [1024, 'panes'],
    [1280, 'panes'],
    [759.5, 'phone'],
    [759.99, 'sheet'],
  ])('maps %ipx to %s', (width, mode) => {
    expect(create(width).service.mode()).toBe(mode);
  });

  it('follows the viewport when it crosses a breakpoint', () => {
    const { service, media } = create(390);

    media.resize(1280);
    expect(service.mode()).toBe('panes');
    media.resize(820);
    expect(service.mode()).toBe('sheet');
  });

  it('lets set() override the viewport until it is cleared', () => {
    const { service, media } = create(390);

    service.set('panes');
    media.resize(820);
    expect(service.mode()).toBe('panes');
    service.set(null);
    expect(service.mode()).toBe('sheet');
  });

  it('falls back to the window width without matchMedia', () => {
    TestBed.configureTestingModule({ providers: [{ provide: MATCH_MEDIA, useValue: null }] });
    const width = window.innerWidth;
    const expected: LayoutMode = width < 760 ? 'phone' : width >= 1024 ? 'panes' : 'sheet';

    expect(TestBed.inject(LayoutModeService).mode()).toBe(expected);
  });

  describe('compactHeight', () => {
    it.each<[number, boolean]>([
      [800, true],
      [820, true],
      [900, false],
      [1080, false],
    ])('is %s px tall → %s', (height, compact) => {
      expect(create(1280, height).service.compactHeight()).toBe(compact);
    });

    it('follows the viewport height', () => {
      const { service, media } = create(1280, 1080);

      media.resize(1280, 800);
      expect(service.compactHeight()).toBe(true);
      media.resize(1440, 900);
      expect(service.compactHeight()).toBe(false);
    });

    it('falls back to the window height without matchMedia', () => {
      TestBed.configureTestingModule({ providers: [{ provide: MATCH_MEDIA, useValue: null }] });

      expect(TestBed.inject(LayoutModeService).compactHeight()).toBe(window.innerHeight <= 820);
    });
  });

  describe('wideSidebar', () => {
    it.each<[number, boolean]>([
      [1023, false],
      [1100, false],
      [1279, false],
      [1280, true],
      [1440, true],
    ])('is %i px wide → %s', (width, wide) => {
      expect(create(width).service.wideSidebar()).toBe(wide);
    });

    it('follows the viewport width', () => {
      const { service, media } = create(1100);

      media.resize(1440);
      expect(service.wideSidebar()).toBe(true);
      media.resize(1279);
      expect(service.wideSidebar()).toBe(false);
    });

    it('falls back to the window width without matchMedia', () => {
      TestBed.configureTestingModule({ providers: [{ provide: MATCH_MEDIA, useValue: null }] });

      expect(TestBed.inject(LayoutModeService).wideSidebar()).toBe(window.innerWidth >= 1280);
    });
  });
});
