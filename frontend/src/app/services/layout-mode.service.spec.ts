import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { COMPACT_QUERY, LayoutMode, LayoutModeService, MATCH_MEDIA, PHONE_QUERY } from './layout-mode.service';

function fakeMatchMedia(initialWidth: number, initialHeight = 900) {
  let width = initialWidth;
  let height = initialHeight;
  const listeners = new Set<() => void>();
  const matchMedia = (query: string) =>
    ({
      media: query,
      get matches() {
        if (query === COMPACT_QUERY) {
          return height <= 820;
        }
        return query === PHONE_QUERY ? width < 760 : width >= 1024;
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
});
