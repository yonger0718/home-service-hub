import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { BehaviorSubject, Observable, of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsComponent } from './settings';
import { LayoutMode, LayoutModeService, MATCH_MEDIA } from '../../services/layout-mode.service';
import { AppearanceService, GainLossConvention, ThemeMode } from '../../services/appearance.service';
import { NavigationPreferencesService } from '../../services/navigation-preferences.service';
import { PortfolioService } from '../../services/portfolio.service';
import { RecalcStatus } from '../../models/portfolio.model';
import { NAV_GROUPS, buildTabBar } from '../shell/navigation';

const SECTIONS = ['外觀', '投資顯示', '導覽', '記帳', '鍵盤快捷鍵', '資料更新'];

describe('SettingsComponent', () => {
  let appearance: {
    theme$: BehaviorSubject<ThemeMode>;
    dark$: BehaviorSubject<boolean>;
    gainLoss$: BehaviorSubject<GainLossConvention>;
    setTheme: ReturnType<typeof vi.fn>;
    setDark: ReturnType<typeof vi.fn>;
    setGainLoss: ReturnType<typeof vi.fn>;
  };
  let recalcStatus: () => Observable<RecalcStatus>;

  beforeEach(async () => {
    localStorage.clear();
    appearance = {
      theme$: new BehaviorSubject<ThemeMode>('light'),
      dark$: new BehaviorSubject(false),
      gainLoss$: new BehaviorSubject<GainLossConvention>('asian'),
      setTheme: vi.fn(),
      setDark: vi.fn(),
      setGainLoss: vi.fn(),
    };
    recalcStatus = () => of({ state: 'idle' });

    await TestBed.configureTestingModule({
      imports: [SettingsComponent],
      providers: [
        { provide: AppearanceService, useValue: appearance },
        { provide: PortfolioService, useValue: { getRecalcStatus: () => recalcStatus() } },
        provideRouter([]),
        { provide: MATCH_MEDIA, useValue: () =>
            ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined }) as unknown as MediaQueryList },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    localStorage.clear();
  });

  function render(mode: LayoutMode = 'panes') {
    TestBed.inject(LayoutModeService).set(mode);
    const fixture = TestBed.createComponent(SettingsComponent);
    fixture.detectChanges();
    return fixture;
  }

  function el(fixture: { nativeElement: HTMLElement }): HTMLElement {
    return fixture.nativeElement;
  }

  function headings(root: HTMLElement): string[] {
    return Array.from(root.querySelectorAll('h2.set-section-title')).map(h => h.textContent!.trim());
  }

  function buttonByText(root: HTMLElement, text: string): HTMLButtonElement {
    const button = Array.from(root.querySelectorAll('button')).find(b => b.textContent?.trim() === text);
    expect(button, `button ${text}`).toBeTruthy();
    return button as HTMLButtonElement;
  }

  function switchFor(root: HTMLElement, label: string): HTMLButtonElement {
    const sw = root.querySelector(`button[role="switch"][aria-label="${label}"]`);
    expect(sw, `switch ${label}`).toBeTruthy();
    return sw as HTMLButtonElement;
  }

  function rowByTitle(root: HTMLElement, title: string): HTMLElement {
    const row = Array.from(root.querySelectorAll<HTMLElement>('.set-row')).find(
      r => r.querySelector('.t')?.textContent?.trim() === title,
    );
    expect(row, `row ${title}`).toBeTruthy();
    return row!;
  }

  function previewSlots(root: HTMLElement): string[] {
    return Array.from(root.querySelectorAll('.tab-preview .tab-slot')).map(slot => slot.textContent!.trim());
  }

  describe('sections', () => {
    it('renders the six sections in order in sheet mode', () => {
      expect(headings(el(render('sheet')))).toEqual(SECTIONS);
    });

    it('renders the six sections in order in panes mode', () => {
      expect(headings(el(render('panes')))).toEqual(SECTIONS);
    });

    it('drops 鍵盤快捷鍵 and stacks the rows on phones', () => {
      const root = el(render('phone'));
      expect(headings(root)).toEqual(SECTIONS.filter(s => s !== '鍵盤快捷鍵'));
      expect(root.querySelector('.shortcut-grid')).toBeNull();
      expect(rowByTitle(root, '主題').classList).toContain('stacked');
      expect(rowByTitle(root, '漲跌顏色').classList).toContain('stacked');
      expect(rowByTitle(root, '桌面側欄預設').classList).toContain('stacked');
    });

    it('stacked rows give the text block no flex basis or growth (no gap above the control)', () => {
      const root = el(render('phone'));
      for (const title of ['主題', '漲跌顏色', '桌面側欄預設']) {
        const row = rowByTitle(root, title);
        expect(getComputedStyle(row).flexDirection, title).toBe('column');
        const text = getComputedStyle(row.querySelector('.set-text')!);
        expect(text.flexGrow, title).toBe('0');
        expect(text.flexBasis, title).toBe('auto');
        const control = getComputedStyle(row.querySelector('.set-control')!);
        expect(control.flexGrow || '0', title).toBe('0');
        expect(control.height || 'auto', title).toBe('auto');
      }
    });

    it('does not stack rows from 760 px', () => {
      const root = el(render('sheet'));
      expect(root.querySelectorAll('.set-row.stacked').length).toBe(0);
    });
  });

  describe('外觀', () => {
    it('shows 淺色 / 深色 / 跟隨系統 with the theme$ value selected', () => {
      appearance.theme$.next('system');
      const root = el(render());
      const row = rowByTitle(root, '主題');
      expect(row.querySelector('.d')!.textContent!.trim()).toBe('跟隨系統時，會依裝置的深淺色自動切換');
      const options = Array.from(row.querySelectorAll('button[role="radio"]'));
      expect(options.map(o => o.textContent!.trim())).toEqual(['淺色', '深色', '跟隨系統']);
      expect(options.map(o => o.getAttribute('aria-checked'))).toEqual(['false', 'false', 'true']);
    });

    it('calls setTheme for 跟隨系統 and 深色', () => {
      const root = el(render());
      buttonByText(root, '跟隨系統').click();
      expect(appearance.setTheme).toHaveBeenCalledWith('system');
      buttonByText(root, '深色').click();
      expect(appearance.setTheme).toHaveBeenCalledWith('dark');
    });
  });

  describe('投資顯示', () => {
    it('calls setGainLoss and keeps the 即時預覽 chips', () => {
      const root = el(render());
      buttonByText(root, '綠漲紅跌').click();
      expect(appearance.setGainLoss).toHaveBeenCalledWith('western');
      const chips = Array.from(root.querySelectorAll('.pill-preview')).map(c => c.textContent!.trim());
      expect(chips).toEqual(['上漲 +2.35%', '下跌 -1.20%']);
      expect(root.textContent).toContain('即時預覽');
    });
  });

  describe('導覽', () => {
    it('shows group switches with 物資 off by default and 系統 fixed', () => {
      const root = el(render());
      expect(switchFor(root, '顯示「物資」分組').getAttribute('aria-checked')).toBe('false');
      expect(switchFor(root, '顯示「投資」分組').getAttribute('aria-checked')).toBe('true');
      expect(switchFor(root, '顯示「財務」分組').getAttribute('aria-checked')).toBe('true');
      const system = rowByTitle(root, '系統');
      expect(system.textContent).toContain('固定顯示');
      expect(system.querySelector('[role="switch"]')).toBeNull();
      expect(root.textContent).toContain('顯示的分組');
    });

    it('toggling 物資 on clears hiddenGroups', () => {
      const fixture = render();
      switchFor(el(fixture), '顯示「物資」分組').click();
      fixture.detectChanges();
      expect(TestBed.inject(NavigationPreferencesService).hiddenGroups()).toEqual([]);
      expect(switchFor(el(fixture), '顯示「物資」分組').getAttribute('aria-checked')).toBe('true');
    });

    it('sets the sidebar default, auto-collapse and badges', () => {
      const root = el(render());
      const prefs = TestBed.inject(NavigationPreferencesService);
      expect(rowByTitle(root, '桌面側欄預設').textContent).toContain('較窄的螢幕一律使用圖示欄');
      buttonByText(root, '圖示欄').click();
      expect(prefs.sidebarDefault()).toBe('rail');
      switchFor(root, '開啟明細時自動收合側欄').click();
      expect(prefs.autoCollapse()).toBe(false);
      switchFor(root, '顯示待處理數字與紅點').click();
      expect(prefs.showBadges()).toBe(false);
    });

    it('hiding every hideable group keeps Settings reachable', () => {
      const fixture = render('phone');
      switchFor(el(fixture), '顯示「投資」分組').click();
      switchFor(el(fixture), '顯示「財務」分組').click();
      fixture.detectChanges();
      const hidden = TestBed.inject(NavigationPreferencesService).hiddenGroups();
      expect([...hidden].sort()).toEqual(['accounting', 'portfolio', 'supplies']);
      expect(buildTabBar(NAV_GROUPS, hidden).settingsInHeader).toBe(true);
      expect(previewSlots(el(fixture))).toEqual(['+']);
      expect(el(fixture).querySelector('.tab-note')!.textContent!.trim()).toBe(
        '設定移到每頁右上角的齒輪，讓「＋」維持在中間。',
      );
    });

    it('phone preview with 物資 off shows 投資, +, 財務 and the gear sentence', () => {
      const root = el(render('phone'));
      expect(previewSlots(root)).toEqual(['投資', '+', '財務']);
      expect(root.querySelector('.tab-note')!.textContent!.trim()).toBe('設定移到每頁右上角的齒輪，讓「＋」維持在中間。');
    });

    it('phone preview with every group visible keeps 設定 and no sentence', () => {
      const fixture = render('phone');
      switchFor(el(fixture), '顯示「物資」分組').click();
      fixture.detectChanges();
      expect(previewSlots(el(fixture))).toEqual(['物資', '投資', '+', '財務', '設定']);
      expect(el(fixture).querySelector('.tab-note')).toBeNull();
    });

    it('shows no tab-bar preview from 760 px', () => {
      expect(el(render('sheet')).querySelector('.tab-preview')).toBeNull();
    });
  });

  describe('記帳', () => {
    it('links 記帳設定 to /accounting/settings', () => {
      const root = el(render());
      const link = root.querySelector<HTMLAnchorElement>('a.set-row')!;
      expect(link.getAttribute('href')).toBe('/accounting/settings');
      expect(link.querySelector('.t')!.textContent!.trim()).toBe('記帳設定');
      expect(link.querySelector('.d')!.textContent!.trim()).toBe('類別、帳戶分組、專案、對象、支出收入顏色、數字鍵盤、匯入 MOZE 備份');
    });
  });

  describe('鍵盤快捷鍵', () => {
    it('lists six shortcuts with <kbd> keys in a two-column grid', () => {
      const root = el(render());
      const grid = root.querySelector('.shortcut-grid')!;
      expect(grid).toBeTruthy();
      const rows = Array.from(grid.querySelectorAll('.shortcut'));
      expect(rows.map(r => ({
        keys: Array.from(r.querySelectorAll('kbd')).map(k => k.textContent!.trim()),
        label: r.querySelector('.shortcut-label')!.textContent!.trim(),
      }))).toEqual([
        { keys: ['N'], label: '記一筆' },
        { keys: ['E'], label: '編輯目前的記錄' },
        { keys: ['↑', '↓'], label: '上一筆／下一筆' },
        { keys: ['Enter'], label: '儲存' },
        { keys: ['⇧', 'Enter'], label: '儲存並續記' },
        { keys: ['Esc'], label: '關閉明細或表單' },
      ]);
    });
  });

  describe('資料更新', () => {
    function jobs(root: HTMLElement) {
      return Array.from(root.querySelectorAll('.job')).map(job => ({
        label: job.querySelector('.t')!.textContent!.trim(),
        when: job.querySelector('.d')!.textContent!.trim(),
        last: job.querySelector('.job-last')?.textContent?.trim() ?? null,
      }));
    }

    it('lists the three jobs and the last quote refresh in Asia/Taipei', () => {
      recalcStatus = () => of({
        state: 'idle',
        quote_refresh: { state: 'completed', finished_at: '2026-10-22T05:30:00Z' },
      });
      expect(jobs(el(render()))).toEqual([
        { label: '盤中報價', when: '交易時段每 15 分鐘', last: '13:30' },
        { label: '淨值快照', when: '每天 15:30', last: null },
        { label: '收盤價回補（TWSE / TPEx）', when: '每個交易日 17:00', last: null },
      ]);
    });

    it('shows only the schedule when the status request fails', () => {
      recalcStatus = () => throwError(() => new Error('boom'));
      const root = el(render());
      expect(jobs(root).map(j => j.last)).toEqual([null, null, null]);
      expect(root.querySelector('.error, [role="alert"]')).toBeNull();
      expect(root.textContent).not.toContain('boom');
    });
  });
});
