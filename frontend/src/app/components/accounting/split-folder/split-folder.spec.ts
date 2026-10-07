import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { FolderIcon, SplitFolderComponent, folderCells } from './split-folder';

const PALETTE = ['#f0cd92', '#4a90e2', '#cc7676', '#7ac29a', '#b48ee0', '#e8a33d'];
const icons = (count: number): FolderIcon[] =>
  Array.from({ length: count }, (_, i) => ({ icon: String.fromCodePoint(0x1f34f + i), color: PALETTE[i] }));
const rgb = (hex: string) => 'rgb(' + [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)).join(', ') + ')';

function render(inputs: { icons: FolderIcon[]; count: number; size?: 'row' | 'bubble' }) {
  const fixture = TestBed.createComponent(SplitFolderComponent);
  fixture.componentRef.setInput('icons', inputs.icons);
  fixture.componentRef.setInput('count', inputs.count);
  if (inputs.size) {
    fixture.componentRef.setInput('size', inputs.size);
  }
  fixture.detectChanges();
  const host = fixture.nativeElement as HTMLElement;
  const cells = Array.from(host.querySelectorAll<HTMLElement>('.cell'));
  return {
    host,
    texts: cells.map(cell => cell.textContent),
    colors: cells.filter(cell => !cell.classList.contains('more')).map(cell => cell.style.background),
    badge: host.querySelector('.badge')?.textContent,
  };
}

describe('folderCells', () => {
  it('draws every icon of a bubble up to four and three plus the rest past four', () => {
    expect(folderCells(icons(4), 4, 'bubble')).toEqual({ cells: icons(4), more: 0 });
    expect(folderCells(icons(5), 5, 'bubble')).toEqual({ cells: icons(3), more: 2 });
    expect(folderCells(icons(6), 6, 'bubble')).toEqual({ cells: icons(3), more: 3 });
  });

  it('draws at most three icons in a row and counts the members not drawn, loaded or not', () => {
    expect(folderCells(icons(2), 2, 'row')).toEqual({ cells: icons(2), more: 0 });
    expect(folderCells(icons(3), 3, 'row')).toEqual({ cells: icons(3), more: 0 });
    expect(folderCells(icons(4), 4, 'row')).toEqual({ cells: icons(3), more: 1 });
    // Only two of seven members are on the loaded page.
    expect(folderCells(icons(2), 7, 'row')).toEqual({ cells: icons(2), more: 5 });
    expect(folderCells(icons(1), 2, 'row')).toEqual({ cells: icons(1), more: 1 });
  });
});

describe('SplitFolderComponent', () => {
  it.each([1, 2, 3, 4])('draws all %i icons of a bubble in their colours with the count badge', count => {
    const view = render({ icons: icons(count), count });
    expect(view.texts).toEqual(icons(count).map(cell => cell.icon));
    expect(view.colors).toEqual(icons(count).map(cell => rgb(cell.color)));
    expect(view.badge).toBe(String(count));
    expect(view.host.classList.contains('folder')).toBe(true);
    expect(view.host.classList.contains('compact')).toBe(false);
    expect(view.host.getAttribute('aria-hidden')).toBe('true');
  });

  it('draws three icons and +N for a bubble of more than four', () => {
    const view = render({ icons: icons(6), count: 6 });
    expect(view.texts).toEqual([...icons(3).map(cell => cell.icon), '+3']);
    expect(view.colors).toEqual(icons(3).map(cell => rgb(cell.color)));
    expect(view.host.querySelectorAll('.cell')[3].classList.contains('more')).toBe(true);
    expect(view.badge).toBe('6');
  });

  it('draws only the loaded icons of a row and counts the rest of the group as +N, the badge the full count', () => {
    const view = render({ icons: icons(2), count: 9, size: 'row' });
    expect(view.host.classList.contains('compact')).toBe(true);
    expect(view.texts).toEqual([...icons(2).map(cell => cell.icon), '+7']);
    expect(view.colors).toEqual(icons(2).map(cell => rgb(cell.color)));
    expect(view.badge).toBe('9');
  });

  it('draws three icons of a fully loaded four-member row and +1', () => {
    const view = render({ icons: icons(4), count: 4, size: 'row' });
    expect(view.texts).toEqual([...icons(3).map(cell => cell.icon), '+1']);
    expect(view.badge).toBe('4');
  });

  it.each(['bubble', 'row'] as const)('caps a remainder of ten or more at 9+ in a 13-member %s, the badge still 13', size => {
    const thirteen = Array.from({ length: 13 }, (_, i) => ({ icon: String.fromCodePoint(0x1f34f + i), color: PALETTE[i % 6] }));
    const view = render({ icons: thirteen, count: 13, size });
    expect(view.texts).toEqual([...thirteen.slice(0, 3).map(cell => cell.icon), '9+']);
    expect(view.badge).toBe('13');
    // Nine left over still reads +9.
    expect(render({ icons: thirteen.slice(0, 12), count: 12, size }).texts.at(-1)).toBe('+9');
  });

  it('draws no +N cell when every member is drawn', () => {
    const view = render({ icons: icons(3), count: 3, size: 'row' });
    expect(view.host.querySelector('.more')).toBeNull();
  });
});
