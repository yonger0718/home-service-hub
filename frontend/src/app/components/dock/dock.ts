import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { NAV_GROUPS } from '../shell/navigation';

@Component({
  selector: 'app-dock',
  imports: [RouterLink, RouterLinkActive],
  templateUrl: './dock.html',
  styleUrl: './dock.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DockComponent {
  readonly activeId = input.required<string>();
  protected readonly groups = NAV_GROUPS;
  protected readonly logoAlt = 'Home Hub';

  protected readonly activeGroup = computed(() => {
    const id = this.activeId();
    return this.groups.find(group => group.items.some(item => item.id === id))?.id ?? 'supplies';
  });
}
