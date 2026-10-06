export type NavGroupId = 'supplies' | 'portfolio' | 'accounting' | 'settings';

export interface NavItem {
  id: string;
  path: string;
  icon: string;
  label: string;
  title: string;
  group: NavGroupId;
  sub?: boolean;
  exact?: boolean;
}

export interface NavGroup {
  id: NavGroupId;
  label: string;
  icon: string;
  defaultPath: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    id: 'supplies',
    label: '物資',
    icon: 'pi-box',
    defaultPath: '/',
    items: [
      { id: 'inventory', path: '/', icon: 'pi-box', label: '庫存', title: '庫存管理', group: 'supplies', exact: true },
      { id: 'shopping', path: '/shopping-list', icon: 'pi-shopping-cart', label: '採買', title: '採買清單', group: 'supplies', sub: true },
    ],
  },
  {
    id: 'portfolio',
    label: '投資',
    icon: 'pi-chart-line',
    defaultPath: '/portfolio',
    items: [
      { id: 'portfolio', path: '/portfolio', icon: 'pi-chart-line', label: '投資', title: '投資概覽', group: 'portfolio', exact: true },
      { id: 'transactions', path: '/portfolio/transactions', icon: 'pi-list', label: '交易', title: '股票交易紀錄', group: 'portfolio', sub: true },
      { id: 'dividends', path: '/portfolio/dividends', icon: 'pi-percentage', label: '股利', title: '股利領取紀錄', group: 'portfolio', sub: true },
      { id: 'import', path: '/portfolio/import', icon: 'pi-upload', label: '匯入', title: '匯入 CSV', group: 'portfolio', sub: true },
      { id: 'realized-pnl', path: '/portfolio/realized-pnl', icon: 'pi-dollar', label: '已實現', title: '已實現損益', group: 'portfolio', sub: true },
      { id: 'accounts', path: '/portfolio/accounts', icon: 'pi-wallet', label: '現金', title: '現金帳戶', group: 'portfolio', sub: true },
    ],
  },
  {
    id: 'accounting',
    label: '財務',
    icon: 'pi-wallet',
    defaultPath: '/accounting',
    items: [
      { id: 'accounting-timeline', path: '/accounting', icon: 'pi-list', label: '記錄', title: '記帳記錄', group: 'accounting', exact: true },
      { id: 'accounting', path: '/accounting/accounts', icon: 'pi-wallet', label: '帳戶', title: '記帳帳戶', group: 'accounting', sub: true },
      { id: 'accounting-settings', path: '/accounting/settings', icon: 'pi-sliders-h', label: '記帳設定', title: '記帳設定', group: 'accounting', sub: true, exact: true },
    ],
  },
  {
    id: 'settings',
    label: '設定',
    icon: 'pi-cog',
    defaultPath: '/settings',
    items: [
      { id: 'settings', path: '/settings', icon: 'pi-cog', label: '設定', title: '設定', group: 'settings', exact: true },
    ],
  },
];

export const NAV_ITEMS = NAV_GROUPS.flatMap(group => group.items);

export function navItemForUrl(url: string): NavItem {
  const cleanUrl = url.split('?')[0].split('#')[0];
  return NAV_ITEMS.find(item => item.exact && item.path === cleanUrl)
    ?? [...NAV_ITEMS]
      .sort((left, right) => right.path.length - left.path.length)
      .find(item => cleanUrl === item.path || cleanUrl.startsWith(`${item.path}/`))
    ?? NAV_GROUPS[0].items[0];
}
