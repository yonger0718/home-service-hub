import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  ElementRef,
  inject,
  Component,
  computed,
  effect,
  input,
  linkedSignal,
  model,
  output,
  signal,
  untracked,
} from '@angular/core';

import { isHandledKey } from '../accounting-ui';

import { CategoryNode, defaultCategoryIcon } from '../../../models/accounting.model';

export function categoryIcon(node: CategoryNode, parent: CategoryNode | null, kind: string): string {
  return node.icon ?? parent?.icon ?? defaultCategoryIcon(parent?.name ?? node.name, kind);
}

export function categoryColor(node: CategoryNode, parent: CategoryNode | null): string {
  return node.color ?? parent?.color ?? 'var(--app-surface-soft)';
}

/** MOZE-style category chooser: icon grid on phones, two-level chip bar on wider screens. */
@Component({
  selector: 'app-category-picker',
  standalone: true,
  templateUrl: './category-picker.html',
  styleUrl: './category-picker.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onBarKeydown($event)' },
})
export class CategoryPickerComponent {
  readonly kind = input.required<string>();
  readonly categories = input<CategoryNode[]>([]);
  readonly variant = input<'grid' | 'bar'>('grid');
  readonly amount = input<string | null>(null);
  readonly splittable = input(true);
  readonly selected = model<CategoryNode | null>(null);
  readonly picked = output<CategoryNode>();
  readonly addLine = output<void>();

  /** Grid only: main category whose sub-categories are on screen. */
  readonly openParent = signal<CategoryNode | null>(null);
  /** Grid only: true while the owner is choosing again after a pick. */
  readonly reopened = signal(false);

  readonly mains = computed(() => this.categories().filter(node => !node.is_hidden));
  private readonly parentById = computed(() => {
    const parents = new Map<number, CategoryNode>();
    for (const main of this.categories()) {
      for (const child of main.children) {
        parents.set(child.id, main);
      }
    }
    return parents;
  });

  /** The selected node while the grid is collapsed into the strip. */
  readonly strip = computed(() => (this.reopened() ? null : this.selected()));

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly cdr = inject(ChangeDetectorRef);

  /** Explicitly writable so collapsing does not fall back to the selected child's parent. */
  readonly barParent = linkedSignal(() => {
    const selected = this.selected();
    const parent = selected ? this.parentById().get(selected.id) : null;
    return parent && !parent.is_hidden && this.visibleChildren(parent).length ? parent : null;
  });

  constructor() {
    effect(() => {
      if (this.selected() === null) {
        untracked(() => {
          this.openParent.set(null);
          this.reopened.set(false);
        });
      }
    });
  }

  parentOf(node: CategoryNode): CategoryNode | null {
    return this.parentById().get(node.id) ?? null;
  }

  visibleChildren(main: CategoryNode): CategoryNode[] {
    return main.children.filter(child => !child.is_hidden);
  }

  iconOf(node: CategoryNode): string {
    return categoryIcon(node, this.parentOf(node), this.kind());
  }

  colorOf(node: CategoryNode): string {
    return categoryColor(node, this.parentOf(node));
  }

  isSelectedMain(main: CategoryNode): boolean {
    const selected = this.selected();
    return !!selected && (selected.id === main.id || this.parentOf(selected)?.id === main.id);
  }

  chipLabel(main: CategoryNode): string {
    const selected = this.selected();
    if (selected && this.parentOf(selected)?.id === main.id) {
      return `${main.name} › ${selected.name}`;
    }
    return main.name;
  }

  tapMain(main: CategoryNode): void {
    if (this.visibleChildren(main).length) {
      if (this.variant() === 'bar') {
        this.barParent.set(this.barParent()?.id === main.id ? null : main);
      } else {
        this.openParent.set(main);
      }
      return;
    }
    this.pick(main);
  }

  pick(node: CategoryNode): void {
    this.selected.set(node);
    if (this.variant() === 'bar') this.barParent.set(this.parentOf(node));
    this.openParent.set(null);
    this.reopened.set(false);
    this.picked.emit(node);
  }

  onBarKeydown(event: KeyboardEvent): void {
    if (this.variant() !== 'bar' || isHandledKey(event)) return;
    const host = this.host.nativeElement;
    const target = event.target as HTMLElement;
    const button = target.closest<HTMLButtonElement>('button');
    if (!button || !host.contains(button)) return;
    const inChildren = !!button.closest('.subbar');
    const mainButtons = Array.from(host.querySelectorAll<HTMLButtonElement>('.catbar button'));
    const focusParent = () => mainButtons.find(b => Number(b.dataset['categoryId']) === this.barParent()?.id)?.focus();
    if (event.key === 'Escape' && this.barParent()) {
      event.preventDefault();
      if (inChildren) focusParent();
      this.barParent.set(null);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      button.click();
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      const buttons = inChildren
        ? Array.from(host.querySelectorAll<HTMLButtonElement>('.subbar button')) : mainButtons;
      const index = buttons.indexOf(button);
      buttons[(index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length]?.focus();
    } else if (event.key === 'ArrowUp' && inChildren) {
      event.preventDefault();
      focusParent();
    } else if (event.key === 'ArrowDown' && !inChildren) {
      const main = this.mains().find(node => node.id === Number(button.dataset['categoryId']));
      if (!main || !this.visibleChildren(main).length) return;
      event.preventDefault();
      this.barParent.set(main);
      this.cdr.detectChanges();
      (host.querySelector<HTMLButtonElement>('.subbar button.on') ?? host.querySelector<HTMLButtonElement>('.subbar button'))?.focus();
    }
  }

  reopen(): void {
    const selected = this.selected();
    this.openParent.set(selected ? this.parentOf(selected) : null);
    this.reopened.set(true);
  }

  back(): void {
    this.openParent.set(null);
  }
}
