import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { LayoutModeService } from '../../../services/layout-mode.service';
import { NavigationStateService } from '../navigation-state.service';
import { SubNavComponent } from '../sub-nav/sub-nav';

/** Page title row (`<h1>`, projected page actions, phone gear) with the phone sub-nav below. */
@Component({
  selector: 'app-page-header',
  imports: [RouterLink, SubNavComponent],
  templateUrl: './page-header.html',
  styleUrl: './page-header.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PageHeaderComponent {
  readonly title = input<string>();

  protected readonly nav = inject(NavigationStateService);
  private readonly layout = inject(LayoutModeService);

  protected readonly heading = computed(() => this.title() ?? this.nav.activeItem().title);
  protected readonly phone = computed(() => this.layout.mode() === 'phone');
  protected readonly gear = computed(() => this.phone() && this.nav.tabBar().settingsInHeader);
}
