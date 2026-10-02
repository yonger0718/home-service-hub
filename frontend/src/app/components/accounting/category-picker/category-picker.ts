import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  input,
  model,
  output,
  signal,
  untracked,
} from '@angular/core';

import { CategoryNode, defaultCategoryIcon } from '../../../models/accounting.model';

export function categoryIcon(node: CategoryNode, parent: CategoryNode | null, kind: string): string {
  return node.icon ?? parent?.icon ?? defaultCategoryIcon(parent?.name ?? node.name, kind);
}

export function categoryColor(node: CategoryNode, parent: CategoryNode | null): string {
  return node.color ?? parent?.color ?? 'var(--app-surface-soft)';
}

/** MOZE-style category chooser: icon grid on phones, chip bar with a sub-category select on wider screens. */
@Component({
  selector: 'app-category-picker',
  standalone: true,
  templateUrl: './category-picker.html',
  styleUrl: './category-picker.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
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

  /** Main category whose sub-categories are on screen (grid) or in the select (bar). */
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

  /** Bar: the main whose select is shown — the one being opened, else the parent of the selected sub. */
  readonly barParent = computed(() => {
    const open = this.openParent();
    if (open) {
      return open;
    }
    const selected = this.selected();
    return selected ? (this.parentById().get(selected.id) ?? null) : null;
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
      this.openParent.set(main);
      return;
    }
    this.pick(main);
  }

  pick(node: CategoryNode): void {
    this.selected.set(node);
    this.openParent.set(null);
    this.reopened.set(false);
    this.picked.emit(node);
  }

  pickChildById(main: CategoryNode, id: string): void {
    const child = this.visibleChildren(main).find(node => String(node.id) === id);
    if (child) {
      this.pick(child);
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
