import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SNOOZE_KEY, SnoozeEntry } from '../components/notifications/snooze';
import { NOTIF_CLOCK } from './notif-clock';
import { NotifSnoozeStore } from './notif-snooze.store';

const NOW = new Date(2026, 9, 24, 10, 0);
const entry = (h: number): SnoozeEntry => ({
  until: new Date(2026, 9, 24, h, 0).toISOString(),
  tier: 'today',
  from: 'schedules',
  wake: { count: 3 },
});

function make(): NotifSnoozeStore {
  TestBed.configureTestingModule({ providers: [{ provide: NOTIF_CLOCK, useValue: () => NOW }] });
  return TestBed.inject(NotifSnoozeStore);
}

describe('NotifSnoozeStore', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('reads the stored map (live entries only) and writes every change back', () => {
    localStorage.setItem(SNOOZE_KEY, JSON.stringify({ a: entry(13), old: entry(9) }));
    const store = make();
    expect(store.entries()).toEqual({ a: entry(13) });
    store.set('b', entry(14));
    expect(JSON.parse(localStorage.getItem(SNOOZE_KEY)!)).toEqual({ a: entry(13), b: entry(14) });
    store.remove('a');
    expect(Object.keys(store.entries())).toEqual(['b']);
    expect(Object.keys(JSON.parse(localStorage.getItem(SNOOZE_KEY)!))).toEqual(['b']);
    store.clear();
    expect(store.entries()).toEqual({});
    expect(localStorage.getItem(SNOOZE_KEY)).toBeNull();
  });

  it('restore puts saved entries back beside the current ones (復原 after 現在顯示)', () => {
    const store = make();
    store.set('a', entry(13));
    store.restore({ b: entry(14), c: entry(15) });
    expect(store.entries()).toEqual({ a: entry(13), b: entry(14), c: entry(15) });
    expect(Object.keys(JSON.parse(localStorage.getItem(SNOOZE_KEY)!))).toEqual(['a', 'b', 'c']);
  });

  it('remove of several ids, and of an unknown id, is harmless', () => {
    const store = make();
    store.set('a', entry(13));
    store.set('b', entry(13));
    store.remove('a', 'b', 'zzz');
    expect(store.entries()).toEqual({});
  });

  it('a corrupt stored value reads as nothing', () => {
    localStorage.setItem(SNOOZE_KEY, '{not json');
    expect(make().entries()).toEqual({});
  });

  it('blocked storage: nothing stored, nothing thrown, the session still snoozes in memory', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const store = make();
    expect(store.entries()).toEqual({});
    store.set('a', entry(13));
    expect(store.entries()).toEqual({ a: entry(13) });
  });

  it('another tab writing the key reloads the entries; other keys are ignored', () => {
    const store = make();
    localStorage.setItem(SNOOZE_KEY, JSON.stringify({ x: entry(15) }));
    window.dispatchEvent(new StorageEvent('storage', { key: 'other' }));
    expect(store.entries()).toEqual({});
    window.dispatchEvent(new StorageEvent('storage', { key: SNOOZE_KEY }));
    expect(store.entries()).toEqual({ x: entry(15) });
    window.dispatchEvent(new StorageEvent('storage', { key: null }));
    expect(store.entries()).toEqual({ x: entry(15) });
  });
});
