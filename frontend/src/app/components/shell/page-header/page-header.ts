import { Location } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { LayoutModeService } from '../../../services/layout-mode.service';
import { NavigationStateService } from '../navigation-state.service';
import { SubNavComponent } from '../sub-nav/sub-nav';

/**
 * Page title row (`<h1>`, projected page actions, phone gear) with the phone sub-nav below. With `back`, phones get a
 * ‹ button before the title (a pushed screen such as 設定).
 */
@Component({
  selector: 'app-page-header',
  imports: [RouterLink, SubNavComponent],
  templateUrl: './page-header.html',
  styleUrl: './page-header.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PageHeaderComponent {
  readonly title = input<string>();
  /** Phones only: a ‹ 返回 button before the title. */
  readonly back = input(false);

  protected readonly nav = inject(NavigationStateService);
  private readonly layout = inject(LayoutModeService);
  private readonly router = inject(Router);
  private readonly location = inject(Location);

  protected readonly heading = computed(() => this.title() ?? this.nav.activeItem().title);
  protected readonly phone = computed(() => this.layout.mode() === 'phone');
  /** No gear on 設定 itself: it would link to the page being viewed. */
  protected readonly gear = computed(
    () => this.phone() && this.nav.tabBar().settingsInHeader && this.nav.activeItem().id !== 'settings',
  );

  /**
   * True when this page was not reached by the router's first successful navigation, so the history entry behind it
   * belongs to the app (as `leavePage` in accounting-ui). Never `history.length`: a bookmark opened in a tab with
   * history would leave the app.
   */
  private readonly hasAppHistory = computed(() => !!this.router.lastSuccessfulNavigation()?.previousNavigation);
  /** Hidden when its fallback would be this very page (every hideable group hidden, so the landing path is 設定). */
  protected readonly showBack = computed(
    () =>
      this.back() &&
      this.phone() &&
      (this.hasAppHistory() || this.nav.landingPath() !== this.nav.activeItem().path),
  );

  protected goBack(): void {
    if (this.hasAppHistory()) {
      this.location.back();
    } else {
      void this.router.navigateByUrl(this.nav.landingPath());
    }
  }
}
