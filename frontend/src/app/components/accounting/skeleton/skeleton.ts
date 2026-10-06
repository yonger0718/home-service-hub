import { ChangeDetectionStrategy, Component, computed, input, numberAttribute } from '@angular/core';

/** Placeholder rows shown while a list's first page (or a filter change) is loading (spec §1.4). */
@Component({
  selector: 'app-skeleton',
  standalone: true,
  template: `
    @for (row of rowList(); track row) {
      <div class="skel-row" aria-hidden="true">
        <span class="skel-ico"></span>
        <span class="skel-lines"><span class="skel-line"></span><span class="skel-line short"></span></span>
      </div>
    }
  `,
  styleUrl: './skeleton.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'skeleton', role: 'status', 'aria-busy': 'true', 'aria-label': '載入中' },
})
export class SkeletonComponent {
  readonly rows = input(3, { transform: numberAttribute });
  readonly rowList = computed(() => Array.from({ length: Math.max(0, this.rows()) }, (_, index) => index));
}
