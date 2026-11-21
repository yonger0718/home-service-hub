import {
  NAV_GROUPS,
  NAV_ITEMS,
  NOTIFICATIONS_GROUP,
  NOTIFICATIONS_ITEM,
  NavBadge,
  NavGroupId,
  SidebarModeInput,
  buildTabBar,
  navItemForUrl,
  resolveSidebarMode,
  summarizeGroupBadges,
} from './navigation';

const portfolio = NAV_GROUPS.find(g => g.id === 'portfolio')!;

function tabLabels(hidden: NavGroupId[]) {
  const layout = buildTabBar(NAV_GROUPS, hidden);
  return {
    labels: layout.slots.map(s => (s.kind === 'plus' ? '+' : s.group.tabLabel ?? s.group.label)),
    settingsInHeader: layout.settingsInHeader,
  };
}

describe('navigation model', () => {
  it('portfolio sub-nav order and labels', () => {
    expect(portfolio.items.map(i => i.label)).toEqual(['概覽', '交易', '股利', '已實現', '現金', '匯入']);
    expect(portfolio.items.map(i => i.sidebarLabel)).toEqual(['投資概覽', '交易紀錄', '股利', '已實現損益', '現金帳戶', '匯入']);
  });

  it('the reward rules overview marks 記帳設定 current (#323 PR B)', () => {
    expect(navItemForUrl('/accounting/reward-rules?state=all').id).toBe('accounting-settings');
    expect(navItemForUrl('/accounting/reward-rules').id).toBe('accounting-settings');
    expect(navItemForUrl('/accounting').id).toBe('accounting-timeline');
    expect(navItemForUrl('/accounting/entries/5').id).toBe('accounting-timeline');
    expect(navItemForUrl('/accounting/settings').id).toBe('accounting-settings');
  });

  it('system group', () => {
    const system = NAV_GROUPS.find(g => g.id === 'system')!;
    expect(system.label).toBe('系統');
    expect(system.tabLabel).toBe('設定');
    expect(navItemForUrl('/settings').group).toBe('system');
  });

  it('tab bar with 物資 hidden', () => {
    expect(tabLabels(['supplies'])).toEqual({ labels: ['投資', '+', '財務'], settingsInHeader: true });
  });

  it('tab bar with all visible', () => {
    expect(tabLabels([])).toEqual({ labels: ['物資', '投資', '+', '財務', '設定'], settingsInHeader: false });
  });

  it('all groups hidden leaves plus alone and moves settings to the gear', () => {
    expect(tabLabels(['supplies', 'portfolio', 'accounting'])).toEqual({ labels: ['+'], settingsInHeader: true });
  });

  it('two hidden, even list', () => {
    expect(tabLabels(['supplies', 'portfolio'])).toEqual({ labels: ['財務', '+', '設定'], settingsInHeader: false });
  });

  it('a folded group shows one dot: red when any item is red, else primary, none without a dot', () => {
    const red: Record<string, NavBadge> = { dividends: { dot: true, tone: 'primary', label: '' }, import: { dot: true, label: '' } };
    expect(summarizeGroupBadges(portfolio, red)).toEqual({ dot: true, tone: 'red' });
    const primary: Record<string, NavBadge> = { dividends: { dot: true, tone: 'primary', label: '' } };
    expect(summarizeGroupBadges(portfolio, primary)).toEqual({ dot: true, tone: 'primary' });
    expect(summarizeGroupBadges(portfolio, {})).toEqual({ dot: false, tone: 'primary' });
  });

  it('/notifications and anything under it resolve to the synthetic 通知 item', () => {
    for (const url of ['/notifications', '/notifications?x=1', '/notifications/recon/5']) {
      expect(navItemForUrl(url), url).toBe(NOTIFICATIONS_ITEM);
    }
    expect(navItemForUrl('/nowhere').id).toBe('inventory');
    expect(navItemForUrl('/notifications/')).toBe(NOTIFICATIONS_ITEM);
    // A longer first segment is not under /notifications.
    expect(navItemForUrl('/notificationsfoo')).not.toBe(NOTIFICATIONS_ITEM);
    expect(navItemForUrl('/notificationsfoo').id).toBe('inventory');
  });

  it('通知 sits outside the nav groups and items', () => {
    expect(NAV_ITEMS).not.toContain(NOTIFICATIONS_ITEM);
    expect(NAV_GROUPS).not.toContain(NOTIFICATIONS_GROUP);
    expect(NOTIFICATIONS_ITEM).toEqual({
      id: 'notifications', path: '/notifications', icon: 'pi-bell', label: '通知', sidebarLabel: '通知', title: '通知', group: 'notifications',
    });
    expect(NOTIFICATIONS_GROUP).toEqual({
      id: 'notifications', label: '通知', icon: 'pi-bell', defaultPath: '/notifications', items: [NOTIFICATIONS_ITEM],
    });
  });

  const base: SidebarModeInput = { wide: true, stored: 'expanded', autoCollapse: true, paneOpen: true, paneOverride: false };
  const MODES: [string, Partial<SidebarModeInput>, 'rail' | 'expanded'][] = [
    ['narrow', { wide: false }, 'rail'],
    ['narrow with stored rail', { wide: false, stored: 'rail' }, 'rail'],
    ['stored rail', { stored: 'rail', paneOpen: false }, 'rail'],
    ['pane open auto-collapses', {}, 'rail'],
    ['pane open with override', { paneOverride: true }, 'expanded'],
    ['pane open, autoCollapse off', { autoCollapse: false }, 'expanded'],
    ['no pane', { paneOpen: false }, 'expanded'],
  ];
  for (const [name, patch, expected] of MODES) {
    it(`sidebar mode: ${name}`, () => {
      expect(resolveSidebarMode({ ...base, ...patch })).toBe(expected);
    });
  }
});
