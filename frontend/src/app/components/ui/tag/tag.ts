import { ChangeDetectionStrategy, Component, input } from '@angular/core';

export type TagVariant = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'dividend';

@Component({
  selector: 'app-tag',
  template: `
    <span class="tag" [class]="'tag tag-' + variant()">
      @if (icon()) {
        <i class="pi {{ icon() }}" aria-hidden="true"></i>
      }
      <ng-content></ng-content>
    </span>
  `,
  styleUrl: './tag.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TagComponent {
  readonly variant = input<TagVariant>('neutral');
  readonly icon = input('');
}
