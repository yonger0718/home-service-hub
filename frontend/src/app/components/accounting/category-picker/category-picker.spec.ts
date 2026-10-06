import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { CategoryNode } from '../../../models/accounting.model';
import { makeCategory } from '../testing/fixtures';
import { CategoryPickerComponent } from './category-picker';

const LUNCH = makeCategory({ id: 12, parent_id: 1, name: '午餐', icon: null, color: null });
const DINNER = makeCategory({ id: 13, parent_id: 1, name: '晚餐' });
const SECRET = makeCategory({ id: 14, parent_id: 1, name: '宵夜', is_hidden: true });
const FOOD = makeCategory({ id: 1, name: '飲食', icon: null, color: '#f0cd92', children: [LUNCH, DINNER, SECRET] });
const BUS = makeCategory({ id: 2, name: '交通', icon: '🚌', color: '#4a90e2' });
const HIDDEN_MAIN = makeCategory({ id: 3, name: '舊類別', is_hidden: true });
const CLOTHES = makeCategory({ id: 41, parent_id: 4, name: '衣物' });
const SHOPPING = makeCategory({ id: 4, name: '購物', icon: '🛍️', color: '#cc7676', children: [CLOTHES] });
const TREE: CategoryNode[] = [FOOD, BUS, HIDDEN_MAIN, SHOPPING];

function render(inputs: Record<string, unknown> = {}) {
  TestBed.configureTestingModule({ imports: [CategoryPickerComponent] });
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

  it('emits addLine from the strip plus and hides it when not splittable', () => {
    const { el, tap, component, fixture } = render();
    let lines = 0;
    component.addLine.subscribe(() => lines++);
    tap('.cat', '交通');

    (el.querySelector('.strip .add') as HTMLButtonElement).click();
    expect(lines).toBe(1);

    fixture.componentRef.setInput('splittable', false);
    fixture.detectChanges();
    expect(el.querySelector('.strip .add')).toBeNull();
  });

  it('reopens the grid at the sub level from the strip and returns to the mains on reset', () => {
    const { el, tap, component, fixture } = render();
    tap('.cat', '飲食');
    tap('.cat', '午餐');

    tap('.strip .sel', '午餐');
    expect(labels(el, '.grid .cat')).toEqual(['‹返回', '🍜午餐', '🍜晚餐']);

    component.selected.set(null);
    fixture.detectChanges();
    expect(labels(el, '.grid .cat')[0]).toBe('🍜飲食');
  });

  describe('bar', () => {
    function key(target: Element, key: string, extra: KeyboardEventInit = {}) {
      const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...extra });
      target.dispatchEvent(event);
      return event;
    }

    it('toggles and rapidly switches visible child chips with parent-colour dots', () => {
      const { el, tap } = render({ variant: 'bar' });
      tap('.catbar button', '飲食');
      expect(el.querySelector('select.subselect')).toBeNull();
      expect(el.querySelector('.subbar')?.getAttribute('role')).toBe('listbox');
      expect(el.querySelector('.subbar')?.getAttribute('aria-label')).toBe('飲食 子類別');
      expect(labels(el, '.subbar button')).toEqual(['🍜午餐', '🍜晚餐']);
      expect((el.querySelector('.subbar .dot') as HTMLElement).style.background).toBe('rgb(240, 205, 146)');
      tap('.catbar button', '飲食');
      expect(el.querySelector('.subbar')).toBeNull();
      tap('.catbar button', '飲食');
      tap('.catbar button', '購物');
      expect(labels(el, '.subbar button')).toEqual(['🛍️衣物']);
      tap('.catbar button', '飲食');
      expect(labels(el, '.subbar button')).toEqual(['🍜午餐', '🍜晚餐']);
    });

    it('emits the original child, marks it and stays expanded until toggled', () => {
      const { el, tap, component } = render({ variant: 'bar' });
      const picked: CategoryNode[] = [];
      component.picked.subscribe(node => picked.push(node));
      tap('.catbar button', '購物');
      tap('.subbar button', '衣物');
      expect(picked).toEqual([CLOTHES]);
      expect(picked[0]).toBe(CLOTHES);
      expect(component.selected()).toBe(CLOTHES);
      expect(el.querySelector('.subbar button.on')?.getAttribute('aria-selected')).toBe('true');
      expect(labels(el, '.catbar button.on')).toEqual(['🛍️購物 › 衣物']);
      tap('.catbar button', '購物');
      expect(el.querySelector('.subbar')).toBeNull();
    });

    it('initializes and synchronizes selected input including reset and direct main', () => {
      const { el, tap, fixture } = render({ variant: 'bar', selected: DINNER });
      expect(labels(el, '.subbar button.on')).toEqual(['🍜晚餐']);
      tap('.catbar button', '飲食');
      fixture.componentRef.setInput('selected', CLOTHES);
      fixture.detectChanges();
      expect(labels(el, '.subbar button.on')).toEqual(['🛍️衣物']);
      fixture.componentRef.setInput('selected', BUS);
      fixture.detectChanges();
      expect(el.querySelector('.subbar')).toBeNull();
      fixture.componentRef.setInput('selected', null);
      fixture.detectChanges();
      expect(el.querySelector('.catbar button.on')).toBeNull();
    });

    it('picks childless and all-hidden-child mains directly', () => {
      const hiddenOnly = makeCategory({ id: 5, name: '隱藏子類', children: [SECRET] });
      const { el, tap, component } = render({ variant: 'bar', selected: BUS, categories: [...TREE, hiddenOnly] });
      const picked: CategoryNode[] = [];
      component.picked.subscribe(node => picked.push(node));
      tap('.catbar button', '飲食');
      tap('.catbar button', '交通');
      expect(el.querySelector('.subbar')).toBeNull();
      tap('.catbar button', '隱藏子類');
      expect(picked).toEqual([BUS, hiddenOnly]);
    });

    it('moves horizontally, down to first/selected child and up to parent', () => {
      const { el, fixture } = render({ variant: 'bar' });
      const mains = el.querySelectorAll<HTMLButtonElement>('.catbar button');
      mains[0].focus();
      key(mains[0], 'ArrowRight');
      expect(document.activeElement).toBe(mains[1]);
      key(mains[1], 'ArrowLeft');
      expect(document.activeElement).toBe(mains[0]);
      expect(key(mains[0], 'ArrowDown').defaultPrevented).toBe(true);
      const children = el.querySelectorAll<HTMLButtonElement>('.subbar button');
      expect(document.activeElement).toBe(children[0]);
      key(children[0], 'ArrowRight');
      expect(document.activeElement).toBe(children[1]);
      key(children[1], 'ArrowLeft');
      expect(document.activeElement).toBe(children[0]);
      key(children[0], 'ArrowUp');
      expect(document.activeElement).toBe(mains[0]);
      fixture.componentRef.setInput('selected', DINNER);
      fixture.detectChanges();
      key(mains[0], 'ArrowDown');
      expect(document.activeElement).toBe(children[1]);
    });

    it('handles Enter/Space before bubbling and Esc restores focus without clearing selection', () => {
      const { el, fixture, component } = render({ variant: 'bar' });
      const unhandled: string[] = [];
      el.parentElement!.addEventListener('keydown', event => {
        if (!event.defaultPrevented) unhandled.push(event.key);
      });
      const picked: CategoryNode[] = [];
      component.picked.subscribe(node => picked.push(node));
      const main = el.querySelector<HTMLButtonElement>('.catbar button')!;
      expect(key(main, 'Enter').defaultPrevented).toBe(true);
      fixture.detectChanges();
      const children = el.querySelectorAll<HTMLButtonElement>('.subbar button');
      children[0].focus();
      expect(key(children[0], 'Enter').defaultPrevented).toBe(true);
      fixture.detectChanges();
      expect(picked).toEqual([LUNCH]);
      expect(key(children[1], ' ').defaultPrevented).toBe(true);
      fixture.detectChanges();
      expect(picked).toEqual([LUNCH, DINNER]);
      expect(key(children[1], 'Escape').defaultPrevented).toBe(true);
      fixture.detectChanges();
      expect(el.querySelector('.subbar')).toBeNull();
      expect(document.activeElement).toBe(main);
      expect(component.selected()).toBe(DINNER);
      expect(unhandled).toEqual([]);
      expect(key(main, 'Escape').defaultPrevented).toBe(false);
    });

    it('ignores handled keys and IME and preserves main focus when collapsing', () => {
      const { el, fixture } = render({ variant: 'bar', selected: LUNCH });
      const main = el.querySelector<HTMLButtonElement>('.catbar button')!;
      expect(key(main, 'Escape', { isComposing: true }).defaultPrevented).toBe(false);
      expect(key(main, 'Enter', { keyCode: 229 }).defaultPrevented).toBe(false);
      const handled = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      handled.preventDefault();
      main.dispatchEvent(handled);
      fixture.detectChanges();
      expect(el.querySelector('.subbar')).not.toBeNull();
      main.focus();
      key(main, ' ');
      fixture.detectChanges();
      expect(el.querySelector('.subbar')).toBeNull();
      expect(document.activeElement).toBe(main);
    });
  });
});
