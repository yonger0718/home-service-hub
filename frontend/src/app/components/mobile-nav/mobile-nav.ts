import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { NavGroup, NavGroupId, summarizeGroupBadges } from '../shell/navigation';
import { NavigationStateService } from '../shell/navigation-state.service';

@Component({
  selector: 'app-mobile-nav',
  imports: [RouterLink],
  templateUrl: './mobile-nav.html',
  styleUrl: './mobile-nav.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MobileNavComponent {
  protected readonly nav = inject(NavigationStateService);

  /** The tab marked active: none on 設定, a pushed screen with its own ‹ back button. */
  protected readonly highlighted = computed<NavGroupId | null>(() =>
    this.nav.activeItem().id === 'settings' ? null : this.nav.activeGroup().id,
  );

  /** A badge dot on a tab other than the one being viewed. */
  protected hasDot(group: NavGroup): boolean {
    if (group.id === this.nav.activeGroup().id) {
      return false;
    }
    const sum = summarizeGroupBadges(group, this.nav.badges());
    return sum.count > 0 || sum.dot;
  }
}
