import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Params, Router, RouterLink } from '@angular/router';
import { filter } from 'rxjs';

import { calendarParams } from '../accounting/timeline/calendar-url';
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
  private readonly router = inject(Router);
  /** The current URL, refreshed on every NavigationEnd (calendar month / day writes included). */
  private readonly url = signal(this.router.url);

  /** The ＋ link's query: the calendar month / day of the current URL, `{}` off the calendar. */
  protected readonly entryQuery = computed<Params>(() => calendarParams(this.router.parseUrl(this.url()).queryParamMap));

  constructor() {
    this.router.events
      .pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(event => this.url.set(event.urlAfterRedirects));
  }

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
