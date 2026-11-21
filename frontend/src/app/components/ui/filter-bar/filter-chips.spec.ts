import { ComponentFixture, TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { FilterChip } from './filter-bar';
import { FilterChipsComponent } from './filter-chips';

describe('FilterChipsComponent', () => {
  let fixture: ComponentFixture<FilterChipsComponent>;
  let el: HTMLElement;
  const removed: string[] = [];
  let cleared = 0;

  const show = (chips: FilterChip[]) => {
    fixture.componentRef.setInput('chips', chips);
    fixture.detectChanges();
  };

  beforeEach(() => {
    removed.length = 0;
    cleared = 0;
    fixture = TestBed.createComponent(FilterChipsComponent);
    fixture.componentInstance.remove.subscribe(k => removed.push(k));
    fixture.componentInstance.clearAll.subscribe(() => cleared++);
    el = fixture.nativeElement;
  });

  it('renders nothing without chips', () => {
    show([]);
    expect(el.querySelector('.filter-chips')).toBeNull();
  });

  it('renders the chips; × emits remove with the key; a single chip has no 清除全部', () => {
    show([{ key: 'broker', label: '券商：甲' }]);
    expect(el.querySelectorAll('.filter-chip').length).toBe(1);
    expect(el.querySelector('.clear-all')).toBeNull();
    el.querySelector<HTMLButtonElement>('.chip-remove')!.click();
    expect(removed).toEqual(['broker']);
  });

  it('shows 清除全部 from two chips and emits clearAll', () => {
    show([{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }]);
    el.querySelector<HTMLButtonElement>('.clear-all')!.click();
    expect(cleared).toBe(1);
  });
});
