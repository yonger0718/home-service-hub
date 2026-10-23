import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ImportAttempt } from '../components/portfolio/import/import-math';
import { ImportHistoryService } from './import-history.service';

const KEY = 'hh-import-history';

function attempt(n: number, ok = true): ImportAttempt {
  return {
    at: new Date(2026, 9, 1, 12, n).toISOString(),
    file: `file-${n}.csv`,
    type: 'transactions',
    ok,
    created: ok ? n : 0,
    errors: ok ? 0 : 1,
    reason: null,
  };
}

function create(): ImportHistoryService {
  return TestBed.inject(ImportHistoryService);
}

describe('ImportHistoryService', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  afterEach(() => {
    TestBed.resetTestingModule();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('starts empty and not failed', () => {
    const svc = create();
    expect(svc.attempts()).toEqual([]);
    expect(svc.lastFailed()).toBe(false);
  });

  it('records newest first and persists under hh-import-history', () => {
    const svc = create();
    svc.record(attempt(1));
    svc.record(attempt(2));
    expect(svc.attempts().map(a => a.file)).toEqual(['file-2.csv', 'file-1.csv']);
    expect(JSON.parse(localStorage.getItem(KEY)!).map((a: ImportAttempt) => a.file)).toEqual(['file-2.csv', 'file-1.csv']);
  });

  it('caps the list at 10', () => {
    const svc = create();
    for (let i = 1; i <= 12; i++) svc.record(attempt(i));
    expect(svc.attempts()).toHaveLength(10);
    expect(svc.attempts()[0].file).toBe('file-12.csv');
    expect(svc.attempts()[9].file).toBe('file-3.csv');
    expect(JSON.parse(localStorage.getItem(KEY)!)).toHaveLength(10);
  });

  it('restores from storage on a fresh instance and drops malformed entries', () => {
    localStorage.setItem(KEY, JSON.stringify([attempt(2), { nope: true }, attempt(1, false), 'x']));
    const svc = create();
    expect(svc.attempts().map(a => a.file)).toEqual(['file-2.csv', 'file-1.csv']);
  });

  it('treats non-JSON storage as empty', () => {
    localStorage.setItem(KEY, '{not json');
    expect(create().attempts()).toEqual([]);
  });

  it('lastFailed follows the head of the list', () => {
    const svc = create();
    svc.record(attempt(1, false));
    expect(svc.lastFailed()).toBe(true);
    svc.record(attempt(2, true));
    expect(svc.lastFailed()).toBe(false);
    svc.record(attempt(3, false));
    expect(svc.lastFailed()).toBe(true);
  });

  it('clear empties the list and the key', () => {
    const svc = create();
    svc.record(attempt(1, false));
    svc.clear();
    expect(svc.attempts()).toEqual([]);
    expect(svc.lastFailed()).toBe(false);
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('survives a throwing localStorage', () => {
    const boom = () => {
      throw new Error('SecurityError');
    };
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(boom);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(boom);
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(boom);
    const svc = create();
    expect(svc.attempts()).toEqual([]);
    expect(() => svc.record(attempt(1, false))).not.toThrow();
    expect(svc.attempts()).toHaveLength(1);
    expect(svc.lastFailed()).toBe(true);
    expect(() => svc.clear()).not.toThrow();
    expect(svc.attempts()).toEqual([]);
  });

  it('reloads when another tab writes the key', () => {
    const svc = create();
    svc.record(attempt(1));
    localStorage.setItem(KEY, JSON.stringify([attempt(5, false), attempt(1)]));
    window.dispatchEvent(new StorageEvent('storage', { key: KEY }));
    expect(svc.attempts().map(a => a.file)).toEqual(['file-5.csv', 'file-1.csv']);
    expect(svc.lastFailed()).toBe(true);
  });

  it('reloads on a storage clear (key null) and ignores other keys', () => {
    const svc = create();
    svc.record(attempt(1));
    localStorage.setItem(KEY, JSON.stringify([attempt(7)]));
    window.dispatchEvent(new StorageEvent('storage', { key: 'hh-nav-hidden' }));
    expect(svc.attempts().map(a => a.file)).toEqual(['file-1.csv']);
    localStorage.clear();
    window.dispatchEvent(new StorageEvent('storage', { key: null }));
    expect(svc.attempts()).toEqual([]);
  });

  it('stops listening once its injector is destroyed', () => {
    const svc = create();
    TestBed.resetTestingModule();
    localStorage.setItem(KEY, JSON.stringify([attempt(9)]));
    window.dispatchEvent(new StorageEvent('storage', { key: KEY }));
    expect(svc.attempts()).toEqual([]);
  });
});
