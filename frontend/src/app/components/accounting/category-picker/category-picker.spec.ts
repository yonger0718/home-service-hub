import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { LayoutMode, LayoutModeService } from '../../../services/layout-mode.service';
import { CategoryNode } from '../../../models/accounting.model';
import { makeCategory } from '../testing/fixtures';
import { Bubble, CategoryPickerComponent, parentNetLines } from './category-picker';

const LUNCH = makeCategory({ id: 12, parent_id: 1, name: '午餐', icon: null, color: null });
const DINNER = makeCategory({ id: 13, parent_id: 1, name: '晚餐' });
const SECRET = makeCategory({ id: 14, parent_id: 1, name: '宵夜', is_hidden: true });
const FOOD = makeCategory({ id: 1, name: '飲食', icon: null, color: '#f0cd92', children: [LUNCH, DINNER, SECRET] });
const BUS = makeCategory({ id: 2, name: '交通', icon: '🚌', color: '#4a90e2' });
const HIDDEN_MAIN = makeCategory({ id: 3, name: '舊類別', is_hidden: true });
const CLOTHES = makeCategory({ id: 41, parent_id: 4, name: '衣物' });
const SHOPPING = makeCategory({ id: 4, name: '購物', icon: '🛍️', color: '#cc7676', children: [CLOTHES] });
const TREE: CategoryNode[] = [FOOD, BUS, HIDDEN_MAIN, SHOPPING];

function render(inputs: Record<string, unknown> = {}, mode: LayoutMode = 'panes') {
  TestBed.configureTestingModule({ imports: [CategoryPickerComponent] });
  TestBed.inject(LayoutModeService).set(mode);
  const fixture = TestBed.createComponent(CategoryPickerComponent);
  fixture.componentRef.setInput('kind', 'expense');
  fixture.componentRef.setInput('categories', TREE);
  for (const [name, value] of Object.entries(inputs)) {
    fixture.componentRef.setInput(name, value);
  }
  fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  const tap = (selector: string, text: string) => {
    const target = Array.from(el.querySelectorAll<HTMLElement>(selector)).find(b => b.textContent?.includes(text));
    if (!target) {
      throw new Error(`no ${selector} with ${text}`);
    }
    target.click();
    fixture.detectChanges();
  };
  return { fixture, el, tap, component: fixture.componentInstance };
}

function labels(el: HTMLElement, selector: string): string[] {
  return Array.from(el.querySelectorAll(selector)).map(node => node.textContent?.replace(/\s+/g, ' ').trim() ?? '');
}

describe('CategoryPickerComponent', () => {
  it('offers visible main categories in the grid and hides hidden ones', () => {
    const { el } = render();

    expect(labels(el, '.grid .cat')).toEqual(['🍜飲食', '🚌交通', '🛍️購物']);
  });

  it('opens the sub-categories of a main category with a back chip, without hidden ones', () => {
    const { el, tap } = render();

    tap('.cat', '飲食');

    expect(labels(el, '.grid .cat')).toEqual(['‹返回', '🍜午餐', '🍜晚餐']);
    tap('.cat', '返回');
    expect(labels(el, '.grid .cat')[0]).toBe('🍜飲食');
  });

  it('collapses to the strip with icon, name and amount after a pick', () => {
    const { el, tap, component, fixture } = render();
    const picked: CategoryNode[] = [];
    component.picked.subscribe(node => picked.push(node));

    tap('.cat', '飲食');
    tap('.cat', '午餐');
    fixture.componentRef.setInput('amount', '$170');
    fixture.detectChanges();

    expect(picked.map(node => node.id)).toEqual([12]);
    expect(component.selected()?.id).toBe(12);
    expect(el.querySelector('.grid')).toBeNull();
    const strip = el.querySelector('.strip')!;
    expect(strip.querySelector('.sel .ico')?.textContent).toBe('🍜');
    expect((strip.querySelector('.sel .ico') as HTMLElement).style.background).toBe('rgb(240, 205, 146)');
    expect(strip.querySelector('.sel b')?.textContent).toBe('午餐');
    expect(strip.querySelector('.sel small')?.textContent).toBe('$170');
  });

  it('picks a main category without sub-categories directly', () => {
    const { tap, component } = render();

    tap('.cat', '交通');

    expect(component.selected()?.id).toBe(2);
  });

  it('emits addChild from the bubble strip plus only when the form offers it', () => {
    const bubble = { key: 'a', label: '交通', icon: '🚌', color: '#fff', amount: '-10', empty: false, protected: false };
    const { el, component, fixture } = render({ bubbles: [bubble], activeBubble: 'a', addVisible: true });
    let added = 0;
    component.addChild.subscribe(() => added++);

    (el.querySelector('.strip .add') as HTMLButtonElement).click();
    expect(added).toBe(1);

    fixture.componentRef.setInput('addVisible', false);
    fixture.detectChanges();
    expect(el.querySelector('.strip .add')).toBeNull();
  });

  it('renders outlined, empty and locked bubbles and caps add', () => {
    const { el, component, fixture } = render({
      activeBubble: 'b',
      parentText: '多類別 TWD -10 (2)',
      bubbles: [
        { key: 'a', label: '午餐', icon: '🍜', color: '#fff', amount: '-10', empty: false, protected: true },
        { key: 'b', label: '未選類別', icon: '○', color: '#fff', amount: '0', empty: true, protected: false },
      ],
      addVisible: true,
      addDisabled: true,
      addHint: '最多 50 項',
    });
    expect(el.querySelector('[data-bubble="b"]')?.classList.contains('empty')).toBe(true);
    expect(el.querySelector('[data-bubble="b"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(el.querySelector('[data-bubble="a"]')?.textContent).toContain('🔒');
    expect((el.querySelector('.add') as HTMLButtonElement).disabled).toBe(true);
    expect(el.querySelector('.bubble-hint')?.textContent).toContain('最多 50 項');
    const selected: string[] = [];
    component.bubbleSelected.subscribe(key => selected.push(key));
    (el.querySelector('[data-bubble="parent"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(selected).toEqual(['parent']);
  });

  const child = (key: string, extra: Partial<Bubble> = {}): Bubble => ({
    key, label: '午餐', icon: '🍜', color: '#f0cd92', amount: '−$10', empty: false, protected: false, ...extra,
  });
  const PALETTE = ['#f0cd92', '#4a90e2', '#cc7676', '#7ac29a', '#b48ee0', '#e8a33d'];
  const children = (count: number) =>
    Array.from({ length: count }, (_, i) => child('k' + i, { icon: String.fromCodePoint(0x1f34f + i), color: PALETTE[i] }));
  const rgb = (hex: string) =>
    'rgb(' + [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)).join(', ') + ')';

  it('pins the parent tile at the strip start and reveals a child clear of it, never scrolling for the parent', () => {
    const { el, fixture } = render({ bubbles: children(5), parentText: '多類別 TWD −$50 (5)', activeBubble: 'k0' }, 'phone');
    const strip = el.querySelector<HTMLElement>('.strip.bubbles')!;
    const parent = strip.querySelector<HTMLElement>('[data-bubble="parent"]')!;
    expect(getComputedStyle(parent).position).toBe('sticky');
    expect(getComputedStyle(parent).left).toBe('0px');
    // jsdom has no layout: a 200 px strip, an 80 px tile, then 56 px bubbles every 64 px.
    let scrollLeft = 0;
    Object.defineProperty(strip, 'clientWidth', { value: 200 });
    Object.defineProperty(strip, 'scrollLeft', { get: () => scrollLeft, set: value => (scrollLeft = value) });
    Object.defineProperty(parent, 'offsetWidth', { value: 80 });
    strip.querySelectorAll<HTMLElement>('[data-bubble^="k"]').forEach((bubble, i) => {
      Object.defineProperty(bubble, 'offsetLeft', { value: 88 + i * 64 });
      Object.defineProperty(bubble, 'offsetWidth', { value: 56 });
    });
    const select = (key: string) => {
      fixture.componentRef.setInput('activeBubble', key);
      fixture.detectChanges();
    };

    select('k4');
    expect(scrollLeft).toBe(88 + 4 * 64 + 56 - 200 + 8);
    select('k1');
    expect(scrollLeft).toBe(88 + 64 - 80 - 8);
    select('parent');
    expect(scrollLeft).toBe(64);
  });

  it.each([1, 2, 3, 4])('draws every child of a %i-child split in the folder tile', count => {
    const bubbles = children(count);
    const { el } = render({ bubbles, parentText: `多類別 TWD −$10 (${count})`, activeBubble: 'k0' });
    const cells = Array.from(el.querySelectorAll<HTMLElement>('[data-bubble="parent"] .folder .cell'));
    expect(cells.map(cell => cell.textContent)).toEqual(bubbles.map(bubble => bubble.icon));
    expect(cells.map(cell => cell.style.background)).toEqual(bubbles.map(bubble => rgb(bubble.color)));
    expect(el.querySelector('.folder .more')).toBeNull();
    expect(el.querySelector('.folder .badge')?.textContent).toBe(String(count));
  });

  it.each([[5, 2], [6, 3]])('draws three children and +N for a %i-child split', (count, more) => {
    const bubbles = children(count);
    const { el } = render({ bubbles, parentText: `多類別 TWD −$10 (${count})`, activeBubble: 'k0' });
    const cells = Array.from(el.querySelectorAll<HTMLElement>('[data-bubble="parent"] .folder .cell'));
    expect(cells.map(cell => cell.textContent)).toEqual([...bubbles.slice(0, 3).map(bubble => bubble.icon), '+' + more]);
    expect(cells.slice(0, 3).map(cell => cell.style.background)).toEqual(bubbles.slice(0, 3).map(b => rgb(b.color)));
    expect(cells[3].classList.contains('more')).toBe(true);
    expect(el.querySelector('.folder .badge')?.textContent).toBe(String(count));
  });

  it('labels the folder tile 多類別 with one signed net line per currency and keeps the pill text as its name', () => {
    const text = '多類別 TWD −$2,479 · USD +$3.50 · JPY ¥0 (3)';
    const { el } = render({ bubbles: children(3), parentText: text, activeBubble: 'k0' });
    const parent = el.querySelector<HTMLButtonElement>('[data-bubble="parent"]')!;
    expect(parent.querySelector('.folder')?.getAttribute('aria-hidden')).toBe('true');
    expect(parent.getAttribute('aria-label')).toBe(text);
    expect(parent.querySelector('.name')?.textContent).toBe('多類別');
    const lines = Array.from(parent.querySelectorAll('.amt'));
    expect(lines.map(line => line.textContent)).toEqual(['TWD −$2,479', 'USD +$3.50', 'JPY ¥0']);
    expect(lines.map(line => [line.classList.contains('neg'), line.classList.contains('pos')])).toEqual([
      [true, false], [false, true], [false, false],
    ]);
    expect(parentNetLines('多類別 (2)')).toEqual([]);
  });

  it('rings only the active bubble, following activeBubble()', () => {
    const { el, fixture } = render({ bubbles: children(2), parentText: '多類別 TWD −$20 (2)', activeBubble: 'parent' });
    const on = () => Array.from(el.querySelectorAll('[data-bubble].on')).map(node => node.getAttribute('data-bubble'));
    expect(on()).toEqual(['parent']);
    expect(el.querySelector('[data-bubble="parent"]')?.getAttribute('aria-pressed')).toBe('true');
    fixture.componentRef.setInput('activeBubble', 'k1');
    fixture.detectChanges();
    expect(on()).toEqual(['k1']);
    expect(el.querySelector('[data-bubble="parent"]')?.getAttribute('aria-pressed')).toBe('false');
  });

  it('stacks a child bubble vertically: circle (lock on its corner), full-name title, signed amount', () => {
    const { el } = render({
      activeBubble: 'a',
      bubbles: [
        child('a', { label: '早餐與下午茶', protected: true }),
        child('b', { label: '退款', icon: '↩', amount: '+$5' }),
        child('c', { label: '未選類別', icon: '○', amount: '$0', empty: true }),
      ],
      addVisible: true,
      addDisabled: true,
      addHint: '最多 50 項',
    });
    const a = el.querySelector('[data-bubble="a"]')!;
    expect(Array.from(a.children).map(node => node.className.split(' ')[0])).toEqual(['ico-wrap', 'name', 'amt']);
    expect(a.querySelector('.ico-wrap .ico')?.textContent).toBe('🍜');
    expect(a.querySelector('.ico-wrap .lock')?.textContent).toBe('🔒');
    // The emoji stays out of the button's name; the lock is a sibling, outside the hidden subtree.
    expect(a.querySelector('.ico-wrap .ico')?.getAttribute('aria-hidden')).toBe('true');
    expect(a.querySelector('.ico-wrap .lock')?.closest('[aria-hidden="true"]')).toBeNull();
    expect(a.querySelector('.name')?.getAttribute('title')).toBe('早餐與下午茶');
    expect(a.querySelector('.amt')?.classList.contains('neg')).toBe(true);
    expect(el.querySelector('[data-bubble="b"] .amt')?.classList.contains('pos')).toBe(true);
    expect(el.querySelector('[data-bubble="b"] .lock')).toBeNull();
    expect(el.querySelector('[data-bubble="c"]')?.classList.contains('empty')).toBe(true);
    expect(el.querySelector('[data-bubble="c"] .amt')?.className).toBe('amt');
    const add = el.querySelector<HTMLButtonElement>('.strip .add')!;
    expect(add.disabled).toBe(true);
    expect(add.getAttribute('aria-label')).toBe('新增子項');
    expect(add.querySelector('.add-ico')?.textContent).toBe('＋');
    expect(add.querySelector('.name')?.textContent).toBe('');
    expect(el.querySelector('.bubble-hint')?.textContent).toBe('最多 50 項');
  });

  it('reopens the main grid from the selected bubble; another bubble is a selection and resets drill-in', () => {
    const bubbles = [
      { key: 'a', label: '晚餐', icon: '🍜', color: '#fff', amount: '-10', empty: false, protected: false },
      { key: 'b', label: '交通', icon: '🚌', color: '#fff', amount: '-5', empty: false, protected: false },
    ];
    const { el, component, fixture, tap } = render({ bubbles, activeBubble: 'a', selected: DINNER });
    const selected: string[] = [];
    component.bubbleSelected.subscribe(key => selected.push(key));
    expect(el.querySelector('.grid')).toBeNull();
    (el.querySelector('[data-bubble="a"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(labels(el, '.grid .cat')).toEqual(['🍜飲食', '🚌交通', '🛍️購物']);
    tap('.cat', '飲食');
    expect(labels(el, '.grid .cat')[0]).toBe('‹返回');
    (el.querySelector('[data-bubble="b"]') as HTMLButtonElement).click();
    expect(selected).toEqual(['b']);
    fixture.componentRef.setInput('activeBubble', 'b');
    fixture.componentRef.setInput('selected', BUS);
    fixture.detectChanges();
    // b's own category is shown collapsed: a's drill-in is not inherited.
    expect(el.querySelector('.grid')).toBeNull();
    expect(el.querySelectorAll('.strip .sel')).toHaveLength(2);
  });

  it('shows only the strip when no category can be chosen (parent / protected child)', () => {
    const bubble = { key: 'a', label: '退款', icon: '↩', color: '#fff', amount: '+5', empty: false, protected: true };
    const { el, component, fixture } = render({ bubbles: [bubble], activeBubble: 'a', gridAllowed: false });
    expect(el.querySelector('.grid')).toBeNull();
    const selected: string[] = [];
    component.bubbleSelected.subscribe(key => selected.push(key));
    (el.querySelector('[data-bubble="a"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(el.querySelector('.grid')).toBeNull();
    expect(selected).toEqual(['a']);
  });

  it('reopens the grid at the main level from the strip and returns to the mains on reset', () => {
    const { el, tap, component, fixture } = render();
    tap('.cat', '飲食');
    tap('.cat', '午餐');

    tap('.strip .sel', '午餐');
    expect(labels(el, '.grid .cat')).toEqual(['🍜飲食', '🚌交通', '🛍️購物']);

    component.selected.set(null);
    fixture.detectChanges();
    expect(labels(el, '.grid .cat')[0]).toBe('🍜飲食');
  });


  for (const mode of ['phone', 'panes', 'sheet'] as const) {
    it('uses only the grid flow in ' + mode, () => {
      const { el, tap } = render({}, mode);
      expect(el.querySelector('.grid')).not.toBeNull();
      expect(el.querySelector('.catbar, .subbar, select')).toBeNull();
      tap('.cat', '飲食');
      expect(el.querySelector('.catbar, .subbar, select')).toBeNull();
      tap('.cat', '午餐');
      expect(el.querySelector('.strip .sel')).not.toBeNull();
      // Splitting is the entry form's bubble strip; a plain picker offers no ＋.
      expect(el.querySelector('.strip .add')).toBeNull();
    });
  }

  it('initializes a child strip and synchronizes selected input and reset', () => {
    const { el, fixture, tap } = render({ selected: DINNER });
    expect(el.querySelector('.strip .sel b')?.textContent).toBe('晚餐');
    fixture.componentRef.setInput('selected', CLOTHES);
    fixture.detectChanges();
    expect(el.querySelector('.strip .sel b')?.textContent).toBe('衣物');
    fixture.componentRef.setInput('selected', BUS);
    fixture.detectChanges();
    expect(el.querySelector('.strip .sel b')?.textContent).toBe('交通');
    tap('.sel', '交通');
    tap('.cat', '飲食');
    fixture.componentRef.setInput('selected', null);
    fixture.detectChanges();
    expect(el.querySelector('.back')).toBeNull();
    expect(el.querySelector('.cat.on')).toBeNull();
  });

  it('emits the original main when every child is hidden', () => {
    const hiddenOnly = makeCategory({ id: 5, name: '隱藏子類', children: [SECRET] });
    const { tap, component } = render({ categories: [...TREE, hiddenOnly] });
    const picked: CategoryNode[] = [];
    component.picked.subscribe(node => picked.push(node));
    tap('.cat', '隱藏子類');
    expect(picked[0]).toBe(hiddenOnly);
  });

  function key(target: Element, key: string, extra: KeyboardEventInit = {}) {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...extra });
    target.dispatchEvent(event);
    return event;
  }

  it('moves one tile horizontally and one measured row vertically, including after resizing', () => {
    const categories = Array.from({ length: 9 }, (_, i) => makeCategory({ id: 100 + i }));
    const { el } = render({ categories });
    const buttons = Array.from(el.querySelectorAll<HTMLButtonElement>('.cat'));
    let columns = 3;
    buttons.forEach((button, i) => {
      button.getBoundingClientRect = () => ({ top: Math.floor(i / columns) * 60 }) as DOMRect;
    });
    buttons[0].focus();
    expect(key(buttons[0], 'ArrowRight').defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(buttons[1]);
    key(buttons[1], 'ArrowLeft');
    expect(document.activeElement).toBe(buttons[0]);
    key(buttons[0], 'ArrowDown');
    expect(document.activeElement).toBe(buttons[3]);
    key(buttons[3], 'ArrowUp');
    expect(document.activeElement).toBe(buttons[0]);
    columns = 4;
    key(buttons[0], 'ArrowDown');
    expect(document.activeElement).toBe(buttons[4]);
    key(buttons[8], 'ArrowDown');
    expect(document.activeElement).toBe(buttons[8]);
  });

  it.each([[347, 5], [500, 7], [560, 8]])(
    'fits %ipx containers into %i columns and navigates one row', (width, expectedColumns) => {
      const categories = Array.from({ length: 18 }, (_, i) => makeCategory({ id: 100 + i }));
      const { el } = render({ categories });
      const grid = el.querySelector<HTMLElement>('.grid')!;
      grid.style.width = width + 'px';
      // jsdom drops this valid nested min() declaration from CSSOM. Inspect
      // Angular's actual injected stylesheet text, scoped to this grid element.
      const rules = Array.from(document.querySelectorAll('style')).flatMap(style =>
        Array.from((style.textContent ?? '').matchAll(/([^{}]+)\{([^{}]*)\}/g)),
      );
      const gridRules = rules.filter(([, selector, body]) =>
        /^\s*\.grid(?:\[[^\]]+\])?\s*$/.test(selector) &&
        grid.matches(selector.trim()) && body.includes('grid-template-columns'),
      );
      expect(gridRules).toHaveLength(1);
      const body = gridRules[0][2];
      const tracks = body.match(/grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(min\(100%,\s*(\d+)px\),\s*1fr\)\)\s*;/);
      const spacing = body.match(/(?:^|;)\s*gap:\s*\d+px\s+(\d+)px\s*;/);
      expect(tracks).not.toBeNull();
      expect(spacing).not.toBeNull();
      const minimum = Number(tracks![1]);
      const gap = Number(spacing![1]);
      const columns = Math.floor((width + gap) / (minimum + gap));
      expect(columns).toBe(expectedColumns);
      const buttons = Array.from(grid.querySelectorAll<HTMLButtonElement>('.cat'));
      buttons.forEach((button, i) => {
        button.getBoundingClientRect = () => ({ top: Math.floor(i / columns) * 60 }) as DOMRect;
      });
      buttons[0].focus();
      expect(key(buttons[0], 'ArrowDown').defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(buttons[expectedColumns]);
      key(buttons[expectedColumns], 'ArrowUp');
      expect(document.activeElement).toBe(buttons[0]);
    },
  );

  it('collapses a reopened main grid on Escape without changing selection or cancelling the form', () => {
    const { el, tap, component, fixture } = render({ selected: DINNER });
    const picked: CategoryNode[] = [];
    component.picked.subscribe(node => picked.push(node));
    const unhandled: string[] = [];
    const listener = (event: KeyboardEvent) => {
      if (!event.defaultPrevented) unhandled.push(event.key);
    };
    el.parentElement!.addEventListener('keydown', listener);
    tap('.sel', '晚餐');
    const main = document.activeElement!;
    expect(key(main, 'Escape', { isComposing: true }).defaultPrevented).toBe(false);
    expect(component.reopened()).toBe(true);
    unhandled.length = 0;
    expect(key(main, 'Escape').defaultPrevented).toBe(true);
    fixture.detectChanges();
    expect(el.querySelector('.grid')).toBeNull();
    expect(document.activeElement).toBe(el.querySelector('.strip .sel'));
    expect(component.selected()).toBe(DINNER);
    expect(picked).toEqual([]);
    expect(unhandled).toEqual([]);
    el.parentElement!.removeEventListener('keydown', listener);
  });

  it('returns Escape focus to the selected bubble, never the parent bubble before it', () => {
    const bubbles = [
      { key: 'a', label: '晚餐', icon: '🍜', color: '#fff', amount: '-10', empty: false, protected: false },
      { key: 'b', label: '交通', icon: '🚌', color: '#fff', amount: '-5', empty: false, protected: false },
    ];
    const { el, fixture } = render({ bubbles, activeBubble: 'b', parentText: '多類別 (2)', selected: BUS });
    (el.querySelector('[data-bubble="b"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    const main = document.activeElement!;
    expect(main.classList.contains('cat')).toBe(true);
    expect(key(main, 'Escape').defaultPrevented).toBe(true);
    fixture.detectChanges();
    expect(el.querySelector('.grid')).toBeNull();
    expect(document.activeElement).toBe(el.querySelector('[data-bubble="b"]'));
  });

  it('leaves main-grid Escape unhandled when there is no selection', () => {
    const { el, component } = render();
    expect(key(el.querySelector('.cat')!, 'Escape').defaultPrevented).toBe(false);
    expect(el.querySelector('.grid')).not.toBeNull();
    expect(component.selected()).toBeNull();
  });

  for (const activation of ['Enter', ' ']) {
    it('picks exactly once with ' + activation + ' and prevents form save', () => {
      const { el, fixture, component } = render();
      const unhandled: string[] = [];
      const listener = (event: KeyboardEvent) => {
        if (!event.defaultPrevented) unhandled.push(event.key);
      };
      el.parentElement!.addEventListener('keydown', listener);
      const picked: CategoryNode[] = [];
      component.picked.subscribe(node => picked.push(node));
      expect(key(el.querySelector('.cat')!, activation).defaultPrevented).toBe(true);
      fixture.detectChanges();
      const child = el.querySelector<HTMLButtonElement>('.cat:not(.back)')!;
      expect(document.activeElement).toBe(child);
      expect(key(child, activation, { repeat: true }).defaultPrevented).toBe(true);
      expect(picked).toEqual([]);
      expect(key(child, activation).defaultPrevented).toBe(true);
      fixture.detectChanges();
      expect(picked).toEqual([LUNCH]);
      expect(picked[0]).toBe(LUNCH);
      expect(unhandled).toEqual([]);
      el.parentElement!.removeEventListener('keydown', listener);
    });
  }

  it('focuses the selected child and restores parent focus with back and child Escape only', () => {
    const { el, tap, fixture } = render({ selected: DINNER });
    tap('.sel', '晚餐');
    tap('.cat', '飲食');
    expect(document.activeElement?.textContent).toContain('晚餐');
    expect(key(document.activeElement!, 'Escape').defaultPrevented).toBe(true);
    fixture.detectChanges();
    const parent = el.querySelector<HTMLButtonElement>('[data-category-id="1"]')!;
    expect(document.activeElement).toBe(parent);
    expect(key(parent, 'Escape').defaultPrevented).toBe(true);
    fixture.detectChanges();
    expect(document.activeElement).toBe(el.querySelector('.strip .sel'));
    tap('.sel', '晚餐');
    tap('.cat', '飲食');
    tap('.back', '返回');
    expect(document.activeElement?.getAttribute('data-category-id')).toBe('1');
  });

  it('ignores already handled and IME keys', () => {
    const { el, tap, fixture } = render();
    const main = el.querySelector('.cat')!;
    key(main, 'Enter', { isComposing: true });
    key(main, 'Enter', { keyCode: 229 });
    expect(el.querySelector('.back')).toBeNull();
    tap('.cat', '飲食');
    const child = el.querySelector('.cat:not(.back)')!;
    expect(key(child, 'Escape', { isComposing: true }).defaultPrevented).toBe(false);
    const handled = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    handled.preventDefault();
    child.dispatchEvent(handled);
    fixture.detectChanges();
    expect(el.querySelector('.back')).not.toBeNull();
  });
});
