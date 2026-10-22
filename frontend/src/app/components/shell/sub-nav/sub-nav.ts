import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { NavBadge, NavItem } from '../navigation';
import { NavigationStateService } from '../navigation-state.service';

/** The active group's items as a segmented control (chips for 投資); nothing for single-item groups. */
@Component({
  selector: 'app-sub-nav',
  imports: [RouterLink],
  templateUrl: './sub-nav.html',
  styleUrl: './sub-nav.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SubNavComponent {
  protected readonly nav = inject(NavigationStateService);

  protected readonly group = computed(() => this.nav.activeGroup());
  protected readonly show = computed(() => this.group().items.length > 1);
  protected readonly chips = computed(() => this.group().id === 'portfolio');

  protected badge(item: NavItem): NavBadge | undefined {
    return this.nav.badges()[item.id];
  }

  /** Label plus badge, as the sidebar does (`帳戶，1 筆待確認`). */
  protected itemLabel(item: NavItem): string {
    const badge = this.badge(item);
    return badge ? `${item.label}，${badge.label}` : item.label;
  }
}
