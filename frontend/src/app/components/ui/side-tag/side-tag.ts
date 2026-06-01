import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

export type SideTagVariant = 'buy' | 'sell' | 'cash';

@Component({
  selector: 'app-side-tag',
  template: `<span class="side-tag" [class.buy]="variant() === 'buy'" [class.sell]="variant() === 'sell'" [class.cash]="variant() === 'cash'">{{ text() }}</span>`,
  styleUrl: './side-tag.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SideTagComponent {
  readonly variant = input<SideTagVariant>('buy');
  readonly label = input('');

  protected readonly text = computed(() => {
    if (this.label()) return this.label();
    if (this.variant() === 'sell') return '賣';
    if (this.variant() === 'cash') return '息';
    return '買';
  });
}
