import { ChangeDetectionStrategy, Component, InjectionToken, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { catchError, map, of } from 'rxjs';

import { AppearanceService, GainLossConvention, ThemeMode } from '../../services/appearance.service';
import { LayoutModeService } from '../../services/layout-mode.service';
import { NavigationPreferencesService } from '../../services/navigation-preferences.service';
import { RecalcStatus } from '../../models/portfolio.model';
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

/** Current time for the 資料更新 "上次" label; overridable in tests. */
export const SETTINGS_CLOCK = new InjectionToken<() => Date>('SETTINGS_CLOCK', {
  providedIn: 'root',
  factory: () => () => new Date(),
});

type JobTone = 'success' | 'warning' | 'danger' | 'neutral';

interface LastRun {
  text: string;
  tone: JobTone;
}

const TAIPEI_PARTS = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Taipei',
  year: 'numeric',
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

interface TaipeiParts {
  date: string;
  month: string;
  day: string;
  time: string;
}

/** Calendar date and HH:mm in Asia/Taipei, whatever the machine time zone. */
function taipeiParts(date: Date): TaipeiParts {
  const parts: Record<string, string> = {};
  for (const part of TAIPEI_PARTS.formatToParts(date)) parts[part.type] = part.value;
  return {
    date: `${parts['year']}-${parts['month']}-${parts['day']}`,
    month: parts['month'],
    day: parts['day'],
    time: `${parts['hour']}:${parts['minute']}`,
  };
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * `上次 HH:mm` (today), `上次 昨天 HH:mm` or `上次 M/D HH:mm`, all in Asia/Taipei
 * (UTC+8, no DST, so "yesterday" is exactly now - 24 h); null when unparseable.
 */
function lastRunLabel(iso: string, now: Date): string | null {
  const finished = new Date(iso);
  if (Number.isNaN(finished.getTime())) return null;
  const run = taipeiParts(finished);
  if (run.date === taipeiParts(now).date) return `上次 ${run.time}`;
  if (run.date === taipeiParts(new Date(now.getTime() - DAY_MS)).date) return `上次 昨天 ${run.time}`;
  return `上次 ${run.month}/${run.day} ${run.time}`;
}

const STATE_DISPLAY: Partial<Record<RecalcStatus['state'], { tone: JobTone; suffix: string }>> = {
  completed: { tone: 'success', suffix: '' },
  partial: { tone: 'warning', suffix: ' · 部分失敗' },
  failed: { tone: 'danger', suffix: ' · 失敗' },
};

/** Null when no finished run is known (idle, running, no timestamp). */
function lastQuoteRun(run: RecalcStatus | undefined, now: Date): LastRun | null {
  if (!run?.finished_at) return null;
  const label = lastRunLabel(run.finished_at, now);
  if (!label) return null;
  const display = STATE_DISPLAY[run.state] ?? { tone: 'neutral' as const, suffix: '' };
  return { text: label + display.suffix, tone: display.tone };
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
  private readonly clock = inject(SETTINGS_CLOCK);

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

  /** Last finished quote refresh; null while loading, when none is known, or when the request fails. */
  protected readonly lastQuoteRefresh = toSignal(
    this.portfolio.getRecalcStatus().pipe(
      map(status => lastQuoteRun(status?.quote_refresh, this.clock())),
      catchError(() => of(null)),
    ),
    { initialValue: null },
  );

  /** Only the quote row has a known outcome; the other jobs keep a neutral dot. */
  protected jobTone(job: Job): JobTone {
    return job.id === 'quotes' ? (this.lastQuoteRefresh()?.tone ?? 'neutral') : 'neutral';
  }

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
