import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { LayoutModeService } from '../../services/layout-mode.service';
import { NAV_GROUPS } from '../shell/navigation';

@Component({
  selector: 'app-dock',
  imports: [RouterLink, RouterLinkActive],
  templateUrl: './dock.html',
  styleUrl: './dock.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[class.compact]': 'layout.compactHeight()' },
})
export class DockComponent {
  readonly activeId = input.required<string>();
  protected readonly layout = inject(LayoutModeService);
  protected readonly groups = NAV_GROUPS;
  protected readonly logoAlt = 'Home Hub';

  protected readonly activeGroup = computed(() => {
    const id = this.activeId();
    return this.groups.find(group => group.items.some(item => item.id === id))?.id ?? 'supplies';
  });
}
