export type NavGroupId = 'supplies' | 'portfolio' | 'accounting' | 'system';
export type SidebarDefault = 'expanded' | 'rail';

export interface NavItem {
  id: string;
  path: string;
  icon: string;
  label: string;
  sidebarLabel: string;
  title: string;
  group: NavGroupId;
  sub?: boolean;
  exact?: boolean;
}

export interface NavGroup {
  id: NavGroupId;
  label: string;
  tabLabel?: string;
  icon: string;
  defaultPath: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    id: 'supplies',
    label: '物資',
    icon: 'pi-box',
    defaultPath: '/inventory',
    items: [
      { id: 'inventory', path: '/inventory', icon: 'pi-box', label: '庫存', sidebarLabel: '庫存', title: '庫存管理', group: 'supplies', exact: true },
      { id: 'shopping', path: '/shopping-list', icon: 'pi-shopping-cart', label: '採買', sidebarLabel: '採買清單', title: '採買清單', group: 'supplies', sub: true },
    ],
  },
  {
    id: 'portfolio',
    label: '投資',
    icon: 'pi-chart-line',
    defaultPath: '/portfolio',
    items: [
      { id: 'portfolio', path: '/portfolio', icon: 'pi-chart-line', label: '概覽', sidebarLabel: '投資概覽', title: '投資概覽', group: 'portfolio', exact: true },
      { id: 'transactions', path: '/portfolio/transactions', icon: 'pi-list', label: '交易', sidebarLabel: '交易紀錄', title: '股票交易紀錄', group: 'portfolio', sub: true },
      { id: 'dividends', path: '/portfolio/dividends', icon: 'pi-percentage', label: '股利', sidebarLabel: '股利', title: '股利領取紀錄', group: 'portfolio', sub: true },
      { id: 'realized-pnl', path: '/portfolio/realized-pnl', icon: 'pi-dollar', label: '已實現', sidebarLabel: '已實現損益', title: '已實現損益', group: 'portfolio', sub: true },
      { id: 'accounts', path: '/portfolio/accounts', icon: 'pi-wallet', label: '現金', sidebarLabel: '現金帳戶', title: '現金帳戶', group: 'portfolio', sub: true },
      { id: 'import', path: '/portfolio/import', icon: 'pi-upload', label: '匯入', sidebarLabel: '匯入', title: '匯入 CSV', group: 'portfolio', sub: true },
    ],
  },
  {
    id: 'accounting',
    label: '財務',
    icon: 'pi-wallet',
    defaultPath: '/accounting',
    items: [
      { id: 'accounting-timeline', path: '/accounting', icon: 'pi-list', label: '記錄', sidebarLabel: '記帳記錄', title: '記帳記錄', group: 'accounting', exact: true },
      { id: 'accounting', path: '/accounting/accounts', icon: 'pi-wallet', label: '帳戶', sidebarLabel: '帳戶', title: '記帳帳戶', group: 'accounting', sub: true },
      { id: 'accounting-settings', path: '/accounting/settings', icon: 'pi-sliders-h', label: '記帳設定', sidebarLabel: '記帳設定', title: '記帳設定', group: 'accounting', sub: true, exact: true },
    ],
  },
  {
    id: 'system',
    label: '系統',
    tabLabel: '設定',
    icon: 'pi-cog',
    defaultPath: '/settings',
    items: [
      { id: 'settings', path: '/settings', icon: 'pi-cog', label: '設定', sidebarLabel: '設定', title: '設定', group: 'system', exact: true },
    ],
  },
];

export const NAV_ITEMS = NAV_GROUPS.flatMap(group => group.items);

/** Where the logo and the bare URL lead: the first visible group's page; `/settings` when only 系統 is left. */
export function landingPathFor(hidden: readonly NavGroupId[]): string {
  return NAV_GROUPS.find(group => group.id !== 'system' && !hidden.includes(group.id))?.defaultPath ?? '/settings';
}

export function navItemForUrl(url: string): NavItem {
  const cleanUrl = url.split('?')[0].split('#')[0];
  return NAV_ITEMS.find(item => item.exact && item.path === cleanUrl)
    ?? [...NAV_ITEMS]
      .sort((left, right) => right.path.length - left.path.length)
      .find(item => cleanUrl === item.path || cleanUrl.startsWith(`${item.path}/`))
    ?? NAV_GROUPS[0].items[0];
}

export interface NavBadge {
  count?: number;
  dot?: boolean;
  label: string;
}

export type TabSlot = { kind: 'group'; group: NavGroup } | { kind: 'plus' };

export interface TabBarLayout {
  slots: TabSlot[];
  settingsInHeader: boolean;
}

/** Visible groups plus the system tab, with "+" in the middle; an odd list drops the system tab to the header gear. */
export function buildTabBar(groups: readonly NavGroup[], hidden: readonly NavGroupId[]): TabBarLayout {
  const list = groups.filter(g => g.id !== 'system' && !hidden.includes(g.id));
  const system = groups.find(g => g.id === 'system');
  if (system) list.push(system);
  const settingsInHeader = list.length % 2 === 1;
  if (settingsInHeader) list.pop();
  const slots: TabSlot[] = list.map(group => ({ kind: 'group', group }));
  slots.splice(Math.floor(list.length / 2), 0, { kind: 'plus' });
  return { slots, settingsInHeader };
}

export interface GroupBadgeSummary {
  count: number;
  dot: boolean;
}

export function summarizeGroupBadges(group: NavGroup, badges: Readonly<Record<string, NavBadge>>): GroupBadgeSummary {
  let count = 0;
  let dot = false;
  for (const item of group.items) {
    const badge = badges[item.id];
    if (!badge) continue;
    count += badge.count ?? 0;
    dot = dot || !!badge.dot;
  }
  return { count, dot };
}

export interface SidebarModeInput {
  wide: boolean;
  stored: SidebarDefault;
  autoCollapse: boolean;
  paneOpen: boolean;
  paneOverride: boolean;
}

export function resolveSidebarMode(input: SidebarModeInput): SidebarDefault {
  if (!input.wide || input.stored === 'rail') return 'rail';
  if (input.paneOpen && input.autoCollapse && !input.paneOverride) return 'rail';
  return 'expanded';
}
