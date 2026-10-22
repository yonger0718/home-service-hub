import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { catchError, map, of } from 'rxjs';

import { AppearanceService, GainLossConvention, ThemeMode } from '../../services/appearance.service';
import { LayoutModeService } from '../../services/layout-mode.service';
import { NavigationPreferencesService } from '../../services/navigation-preferences.service';
import { PortfolioService } from '../../services/portfolio.service';
import { NAV_GROUPS, NavGroupId, SidebarDefault, buildTabBar } from '../shell/navigation';
import { PageHeaderComponent } from '../shell/page-header/page-header';
import { SegToggleComponent, SegToggleOption } from '../ui/seg-toggle/seg-toggle';
import { SwitchComponent } from '../ui/switch/switch';

interface GroupRow {
  id: NavGroupId;
  label: string;
  icon: string;
  description: string;
}

interface Shortcut {
  keys: string[];
  label: string;
}

interface Job {
  id: 'quotes' | 'snapshot' | 'closing';
  label: string;
  schedule: string;
}

const GROUP_DESCRIPTIONS: Record<NavGroupId, string> = {
  supplies: '庫存、採買清單',
  portfolio: '概覽、交易、股利、已實現、現金、匯入',
  accounting: '記帳記錄、帳戶、記帳設定',
  system: '設定',
};

/** HH:mm in Asia/Taipei (UTC+8, no DST) for an ISO timestamp; null when absent or unparseable. */
function taipeiTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Taipei',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date);
}

@Component({
  selector: 'app-settings',
  imports: [PageHeaderComponent, RouterLink, SegToggleComponent, SwitchComponent],
  templateUrl: './settings.html',
  styleUrl: './settings.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SettingsComponent {
  private readonly appearance = inject(AppearanceService);
  private readonly layout = inject(LayoutModeService);
  protected readonly prefs = inject(NavigationPreferencesService);
  private readonly portfolio = inject(PortfolioService);

  protected readonly theme = toSignal(this.appearance.theme$, { initialValue: this.appearance.theme$.value });
  protected readonly gainLoss = toSignal(this.appearance.gainLoss$, { initialValue: this.appearance.gainLoss$.value });

  /** Below 760 px: controls stack under their titles, no shortcut reference, tab-bar preview shown. */
  protected readonly phone = computed(() => this.layout.mode() === 'phone');

  protected readonly themeOptions: SegToggleOption[] = [
    { value: 'light', label: '淺色' },
    { value: 'dark', label: '深色' },
    { value: 'system', label: '跟隨系統' },
  ];

  protected readonly gainLossOptions: SegToggleOption[] = [
    { value: 'asian', label: '紅漲綠跌' },
    { value: 'western', label: '綠漲紅跌' },
  ];

  protected readonly sidebarOptions: SegToggleOption[] = [
    { value: 'expanded', label: '展開' },
    { value: 'rail', label: '圖示欄' },
  ];

  protected readonly groupRows: GroupRow[] = NAV_GROUPS.map(group => ({
    id: group.id,
    label: group.label,
    icon: group.icon,
    description: GROUP_DESCRIPTIONS[group.id],
  }));

  protected readonly tabBar = computed(() => buildTabBar(NAV_GROUPS, this.prefs.hiddenGroups()));
  protected readonly previewSlots = computed(() =>
    this.tabBar().slots.map(slot =>
      slot.kind === 'plus'
        ? { kind: 'plus' as const, label: '+', icon: 'pi-plus' }
        : { kind: 'group' as const, label: slot.group.tabLabel ?? slot.group.label, icon: slot.group.icon },
    ),
  );

  protected readonly shortcuts: Shortcut[] = [
    { keys: ['N'], label: '記一筆' },
    { keys: ['E'], label: '編輯目前的記錄' },
    { keys: ['↑', '↓'], label: '上一筆／下一筆' },
    { keys: ['Enter'], label: '儲存' },
    { keys: ['⇧', 'Enter'], label: '儲存並續記' },
    { keys: ['Esc'], label: '關閉明細或表單' },
  ];

  protected readonly jobs: Job[] = [
    { id: 'quotes', label: '盤中報價', schedule: '交易時段每 15 分鐘' },
    { id: 'snapshot', label: '淨值快照', schedule: '每天 15:30' },
    { id: 'closing', label: '收盤價回補（TWSE / TPEx）', schedule: '每個交易日 17:00' },
  ];

  /** Last quote refresh (HH:mm Asia/Taipei); null while loading, when absent, or when the request fails. */
  protected readonly lastQuoteRefresh = toSignal(
    this.portfolio.getRecalcStatus().pipe(
      map(status => taipeiTime(status?.quote_refresh?.finished_at)),
      catchError(() => of(null)),
    ),
    { initialValue: null },
  );

  protected isShown(group: NavGroupId): boolean {
    return !this.prefs.hiddenGroups().includes(group);
  }

  protected setShown(group: NavGroupId, shown: boolean): void {
    this.prefs.setHidden(group, !shown);
  }

  protected setTheme(value: string): void {
    this.appearance.setTheme(value as ThemeMode);
  }

  protected setGainLoss(value: string): void {
    this.appearance.setGainLoss(value as GainLossConvention);
  }

  protected setSidebarDefault(value: string): void {
    this.prefs.setSidebarDefault(value as SidebarDefault);
  }
}
