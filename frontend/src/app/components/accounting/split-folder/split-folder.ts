import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** One child's category icon in a split folder tile. */
export interface FolderIcon {
  icon: string;
  color: string;
}

/** The tile's 2×2 mosaic: every icon of a split of up to four, else three and a `+N` cell. */
const FOLDER_CELLS = 4;

/**
 * The icons a tile draws and its `+N` remainder. A bubble (picker strip, entry detail) draws all of a split of up to
 * four; a timeline row and any larger split draw at most three. `icons` may be fewer than `count` (a timeline page holds
 * only some members): N counts every member not drawn, so the cells plus N always add up to `count`.
 */
export function folderCells(icons: readonly FolderIcon[], count: number, size: 'row' | 'bubble'): { cells: FolderIcon[]; more: number } {
  const cap = size === 'row' || count > FOLDER_CELLS ? FOLDER_CELLS - 1 : FOLDER_CELLS;
  const cells = icons.slice(0, cap);
  return { cells, more: Math.max(0, count - cells.length) };
}

/** A 多類別 group drawn as a folder: its children's icons in their colours plus a count badge. Presentation only. */
@Component({
  selector: 'app-split-folder',
  standalone: true,
  template: `@for (cell of view().cells; track $index) {<span class="cell" [style.background]="cell.color">{{ cell.icon }}</span>}@if (view().more) {<span class="cell more">+{{ view().more }}</span>}<span class="badge">{{ count() }}</span>`,
  styleUrl: './split-folder.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'folder', 'aria-hidden': 'true', '[class.compact]': "size() === 'row'" },
})
export class SplitFolderComponent {
  readonly icons = input<readonly FolderIcon[]>([]);
  readonly count = input.required<number>();
  readonly size = input<'row' | 'bubble'>('bubble');

  readonly view = computed(() => folderCells(this.icons(), this.count(), this.size()));
}
