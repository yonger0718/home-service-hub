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

  /** The ＋ link's query: the calendar month / day of the current URL, `{}` off the calendar. */
  protected readonly entryQuery = this.nav.calendarQuery;

  /** The tab marked active: none on 設定, a pushed screen with its own ‹ back button. */
  protected readonly highlighted = computed<NavGroupId | null>(() =>
    this.nav.activeItem().id === 'settings' ? null : this.nav.activeGroup().id,
  );

  /** The dot tone of a tab other than the one being viewed; null when it has no dot. */
  protected dotTone(group: NavGroup): 'red' | 'primary' | null {
    if (group.id === this.nav.activeGroup().id) {
      return null;
    }
    const sum = summarizeGroupBadges(group, this.nav.badges());
    return sum.dot ? sum.tone : null;
  }
}
