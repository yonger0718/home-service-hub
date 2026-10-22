import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NavigationPreferencesService } from './navigation-preferences.service';

function create(): NavigationPreferencesService {
  return TestBed.inject(NavigationPreferencesService);
}

describe('NavigationPreferencesService', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  afterEach(() => vi.restoreAllMocks());

  it('defaults on first load', () => {
    const svc = create();
    expect(svc.hiddenGroups()).toEqual(['supplies']);
    expect(svc.sidebarDefault()).toBe('expanded');
    expect(svc.autoCollapse()).toBe(true);
    expect(svc.showBadges()).toBe(true);
    expect(svc.foldedGroups()).toEqual([]);
  });

  it('writes each preference to its own key', () => {
    const svc = create();
    svc.setHidden('portfolio', true);
    svc.setSidebarDefault('rail');
    svc.setAutoCollapse(false);
    svc.setShowBadges(false);
    svc.setFolded('accounting', true);
    expect(JSON.parse(localStorage.getItem('hh-nav-hidden')!)).toEqual(['supplies', 'portfolio']);
    expect(localStorage.getItem('hh-nav-sidebar')).toBe('rail');
    expect(localStorage.getItem('hh-nav-autocollapse')).toBe('0');
    expect(localStorage.getItem('hh-nav-badges')).toBe('0');
    expect(JSON.parse(localStorage.getItem('hh-nav-folded')!)).toEqual(['accounting']);
  });

  it('restores stored values on a fresh instance', () => {
    const svc = create();
    svc.setHidden('portfolio', true);
    svc.setSidebarDefault('rail');
    svc.setAutoCollapse(false);
    svc.setShowBadges(false);
    svc.setFolded('accounting', true);
    TestBed.resetTestingModule();
    const fresh = create();
    expect(fresh.hiddenGroups()).toEqual(['supplies', 'portfolio']);
    expect(fresh.sidebarDefault()).toBe('rail');
    expect(fresh.autoCollapse()).toBe(false);
    expect(fresh.showBadges()).toBe(false);
    expect(fresh.foldedGroups()).toEqual(['accounting']);
  });

  it('never hides system', () => {
    const svc = create();
    svc.setHidden('system', true);
    expect(svc.hiddenGroups()).toEqual(['supplies']);
    expect(localStorage.getItem('hh-nav-hidden')).toBeNull();
  });

  it('ignores corrupt or unknown stored values', () => {
    for (const raw of ['nope', '{"a":1}', '"portfolio"']) {
      TestBed.resetTestingModule();
      localStorage.setItem('hh-nav-hidden', raw);
      expect(create().hiddenGroups()).toEqual(['supplies']);
    }
    TestBed.resetTestingModule();
    localStorage.setItem('hh-nav-hidden', '["system","portfolio","bogus"]');
    expect(create().hiddenGroups()).toEqual(['portfolio']);
    TestBed.resetTestingModule();
    localStorage.removeItem('hh-nav-hidden');
    localStorage.setItem('hh-nav-sidebar', 'wide');
    expect(create().sidebarDefault()).toBe('expanded');
  });

  it('blocked storage keeps in-memory state', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const svc = create();
    expect(svc.hiddenGroups()).toEqual(['supplies']);
    expect(svc.sidebarDefault()).toBe('expanded');
    expect(() => svc.setSidebarDefault('rail')).not.toThrow();
    expect(svc.sidebarDefault()).toBe('rail');
  });

  it('unhiding removes the group', () => {
    const svc = create();
    svc.setHidden('supplies', false);
    expect(svc.hiddenGroups()).toEqual([]);
  });

  it('hiding an already hidden group does not duplicate it', () => {
    const svc = create();
    svc.setHidden('supplies', true);
    expect(svc.hiddenGroups()).toEqual(['supplies']);
  });
});
