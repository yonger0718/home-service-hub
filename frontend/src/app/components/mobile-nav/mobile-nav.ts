import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { NAV_GROUPS, NavGroupId } from '../shell/navigation';

@Component({
  selector: 'app-mobile-nav',
  imports: [RouterLink],
  templateUrl: './mobile-nav.html',
  styleUrl: './mobile-nav.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MobileNavComponent {
  readonly activeId = input.required<string>();
  readonly activeGroupId = input.required<NavGroupId>();

  protected readonly groups = NAV_GROUPS;

  protected readonly currentGroup = computed(() => (
    this.groups.find(group => group.id === this.activeGroupId()) ?? this.groups[0]
  ));
}
