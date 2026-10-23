import {
  NAV_GROUPS,
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

  it('folded group sums counts and shows a dot', () => {
    const badges: Record<string, NavBadge> = { dividends: { count: 2, label: '' }, import: { dot: true, label: '' } };
    expect(summarizeGroupBadges(portfolio, badges)).toEqual({ count: 2, dot: true });
    expect(summarizeGroupBadges(portfolio, {})).toEqual({ count: 0, dot: false });
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
