import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  ElementRef,
  inject,
  Component,
  computed,
  effect,
  input,
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

/** One child of the entry form's split strip. */
export interface Bubble {
  key: string;
  label: string;
  icon: string;
  color: string;
  amount: string;
  empty: boolean;
  protected: boolean;
}

/**
 * Category chooser with one drill-in grid on every layout. The entry form passes `bubbles` (its children, a parent
 * bubble and ＋); other callers keep the single selected strip.
 */
@Component({
  selector: 'app-category-picker',
  standalone: true,
  templateUrl: './category-picker.html',
  styleUrl: './category-picker.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onGridKeydown($event)' },
})
export class CategoryPickerComponent {
  readonly kind = input.required<string>();
  readonly categories = input<CategoryNode[]>([]);
  readonly amount = input<string | null>(null);
  readonly selected = model<CategoryNode | null>(null);
  readonly picked = output<CategoryNode>();
  readonly bubbles = input<Bubble[]>([]);
  readonly activeBubble = input<string | null>(null);
  readonly parentText = input<string | null>(null);
  readonly addVisible = input(false);
  readonly addDisabled = input(false);
  readonly addHint = input<string | null>(null);
  /** False for the parent and for a protected child: no category can be chosen. */
  readonly gridAllowed = input(true);
  readonly bubbleSelected = output<string>();
  readonly addChild = output<void>();

  /** main category whose sub-categories are on screen. */
  readonly openParent = signal<CategoryNode | null>(null);
  /** true while the owner is choosing again after a pick. */
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

  constructor() {
    effect(() => {
      if (this.selected() === null) {
        untracked(() => {
          this.openParent.set(null);
          this.reopened.set(false);
        });
      }
    });
    // Another bubble never inherits the previous child's drill-in or reopened grid.
    effect(() => {
      this.activeBubble();
      untracked(() => {
        this.openParent.set(null);
        this.reopened.set(false);
      });
    });
  }

  /** The selected bubble again reopens its main grid; any other bubble (or the parent) is a selection change. */
  chooseBubble(key: string): void {
    if (key === this.activeBubble() && key !== 'parent' && this.gridAllowed()) {
      this.reopen();
    } else {
      this.bubbleSelected.emit(key);
    }
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

  tapMain(main: CategoryNode): void {
    if (this.visibleChildren(main).length) {
      this.openParent.set(main);
      this.cdr.detectChanges();
      const grid = this.host.nativeElement;
      (grid.querySelector<HTMLButtonElement>('.cat.on:not(.back)') ??
        grid.querySelector<HTMLButtonElement>('.cat:not(.back)'))?.focus();
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

  onGridKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event)) return;
    const host = this.host.nativeElement;
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('.grid button');
    if (!button || !host.contains(button)) return;
    if (event.key === 'Escape' && this.openParent()) {
      event.preventDefault();
      this.back();
    } else if (event.key === 'Escape' && this.reopened() && this.selected()) {
      event.preventDefault();
      this.reopened.set(false);
      this.cdr.detectChanges();
      (host.querySelector<HTMLButtonElement>('[data-bubble][aria-pressed="true"]') ??
        host.querySelector<HTMLButtonElement>('.strip .sel'))?.focus();
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (!event.repeat) button.click();
    } else {
      const buttons = Array.from(host.querySelectorAll<HTMLButtonElement>('.grid button'));
      const columns = this.renderedColumns(buttons);
      const delta: Record<string, number> = {
        ArrowLeft: -1, ArrowRight: 1, ArrowUp: -columns, ArrowDown: columns,
      };
      if (!(event.key in delta)) return;
      event.preventDefault();
      const index = buttons.indexOf(button);
      buttons[Math.max(0, Math.min(buttons.length - 1, index + delta[event.key]))]?.focus();
    }
  }

  /** Measure the first rendered row, including the back tile; adapts to container width. */
  private renderedColumns(buttons: HTMLButtonElement[]): number {
    const top = buttons[0]?.getBoundingClientRect().top;
    const nextRow = buttons.findIndex(button => Math.abs(button.getBoundingClientRect().top - top!) > 1);
    return nextRow > 0 ? nextRow : Math.max(1, buttons.length);
  }

  reopen(): void {
    this.openParent.set(null);
    this.reopened.set(true);
    this.cdr.detectChanges();
    (this.host.nativeElement.querySelector<HTMLButtonElement>('.cat.on') ??
      this.host.nativeElement.querySelector<HTMLButtonElement>('.cat'))?.focus();
  }

  back(): void {
    const parent = this.openParent();
    this.openParent.set(null);
    this.cdr.detectChanges();
    this.host.nativeElement.querySelector<HTMLButtonElement>(
      '[data-category-id="' + parent?.id + '"]',
    )?.focus();
  }
}
