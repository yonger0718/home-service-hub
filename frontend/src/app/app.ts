import { Component, signal, inject, OnDestroy, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule, Router, NavigationEnd } from '@angular/router';
import { filter } from 'rxjs/operators';
import { TooltipModule } from 'primeng/tooltip';

@Component({
  selector: 'app-root',
  imports: [
    CommonModule, RouterModule, TooltipModule
  ],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit, OnDestroy {
  private themeMediaQuery?: MediaQueryList;
  private themeListener?: (event: MediaQueryListEvent) => void;
  private router = inject(Router);
  
  protected readonly title = signal('庫存管理');
  protected readonly isSidebarOpen = signal(false);
  
  protected readonly currentUrl = signal('/');

  protected readonly mainTab = computed(() => {
    const url = this.currentUrl();
    if (url === '/' || url === '/shopping-list') return 'supplies';
    if (url.startsWith('/portfolio')) return 'portfolio';
    if (url.startsWith('/accounting')) return 'accounting';
    return 'supplies';
  });

  constructor() {
    this.initTheme();
  }

  ngOnInit() {
    this.router.events.pipe(
      filter(event => event instanceof NavigationEnd)
    ).subscribe((event: any) => {
      this.currentUrl.set(event.urlAfterRedirects.split('?')[0]);
      this.updateTitleByUrl(this.currentUrl());
      this.closeSidebar();
    });
  }

  private updateTitleByUrl(url: string) {
    let newTitle = '家庭服務中心';
    if (url === '/') newTitle = '庫存管理';
    else if (url === '/shopping-list') newTitle = '採買清單';
    else if (url === '/portfolio') newTitle = '投資概覽';
    else if (url === '/portfolio/transactions') newTitle = '股票交易紀錄';
    else if (url === '/portfolio/dividends') newTitle = '股利領取紀錄';
    else if (url === '/portfolio/realized-pnl') newTitle = '已實現損益';
    else if (url === '/portfolio/import') newTitle = '匯入 CSV';
    else if (url === '/accounting/dashboard') newTitle = '記帳分析';
    else if (url === '/accounting/transactions') newTitle = '交易紀錄';
    else if (url === '/accounting/settings') newTitle = '會計設定';
    
    this.title.set(newTitle);
  }

  ngOnDestroy() {
    if (!this.themeMediaQuery || !this.themeListener) return;
    if (this.themeMediaQuery.removeEventListener) {
      this.themeMediaQuery.removeEventListener('change', this.themeListener);
    } else {
      this.themeMediaQuery.removeListener(this.themeListener);
    }
  }

  protected toggleSidebar() {
    this.isSidebarOpen.update(v => !v);
  }

  protected closeSidebar() {
    this.isSidebarOpen.set(false);
  }

  private applyTheme(isDark: boolean) {
    if (typeof document !== 'undefined') {
      document.documentElement.classList.toggle('app-dark-mode', isDark);
      document.documentElement.classList.toggle('app-light-mode', !isDark);
    }
  }

  private initTheme() {
    if (typeof window === 'undefined') return;
    // 預設強制使用淺色主題
    this.applyTheme(false);
  }
}
