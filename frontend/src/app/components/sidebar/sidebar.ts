import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { LayoutModeService } from '../../services/layout-mode.service';
import { LogoLockupComponent } from './logo-lockup';
import { NavigationStateService } from '../shell/navigation-state.service';
import { GroupBadgeSummary, NavBadge, NavGroup, NavItem, summarizeGroupBadges } from '../shell/navigation';

const EXPANDED_WIDTH = '248px';
const RAIL_WIDTH = '72px';

@Component({
  selector: 'app-sidebar',
  imports: [RouterLink, LogoLockupComponent],
  templateUrl: './sidebar.html',
  styleUrl: './sidebar.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '[class.expanded]': 'expanded()',
    '[class.rail]': '!expanded()',
    '[class.compact]': 'layout.compactHeight()',
    '[style.--sidebar-w]': 'expanded() ? expandedWidth : railWidth',
  },
})
export class SidebarComponent {
  protected readonly nav = inject(NavigationStateService);
  protected readonly layout = inject(LayoutModeService);
  protected readonly expandedWidth = EXPANDED_WIDTH;
  protected readonly railWidth = RAIL_WIDTH;

  protected readonly expanded = computed(() => this.nav.sidebarMode() === 'expanded');

  /** The first visible group's landing page; only 系統 left means `/settings`. */
  protected readonly logoPath = this.nav.landingPath;

  protected badge(item: NavItem): NavBadge | undefined {
    return this.nav.badges()[item.id];
  }

  /** The item's accessible name: its label, plus the badge when there is one (`記帳記錄，提醒中心 2 項`). */
  protected itemLabel(item: NavItem): string {
    const badge = this.badge(item);
    return badge ? `${item.sidebarLabel}，${badge.label}` : item.sidebarLabel;
  }

  protected summary(group: NavGroup): GroupBadgeSummary {
    return summarizeGroupBadges(group, this.nav.badges());
  }

  /**
   * The sidebar is its own scroll container, which would clip an absolutely positioned rail tooltip, so the tip is
   * `position: fixed` beside the rail at the item's vertical centre, measured when the pointer or focus reaches it.
   */
  protected placeTip(event: Event): void {
    if (this.expanded() || !(event.target instanceof Element)) {
      return;
    }
    const item = event.target.closest<HTMLElement>('a.side-item');
    if (item) {
      const rect = item.getBoundingClientRect();
      item.style.setProperty('--tip-y', `${rect.top + rect.height / 2}px`);
    }
  }
}
