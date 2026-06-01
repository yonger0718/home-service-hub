import { ChangeDetectionStrategy, Component, input } from '@angular/core';

@Component({
  selector: 'app-bento',
  template: `
    <section class="bento" [class.b-full]="full()">
      @if (title()) {
        <h2 class="card-title">{{ title() }}</h2>
      }
      <ng-content></ng-content>
    </section>
  `,
  styleUrl: './bento.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BentoComponent {
  readonly title = input('');
  readonly full = input(false);
}
