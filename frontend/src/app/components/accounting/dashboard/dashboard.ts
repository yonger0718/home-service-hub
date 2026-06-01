import { Component, DestroyRef, OnInit, ViewChild, inject, signal, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { AccountingService } from '../../../services/accounting.service';
import { AppearanceService } from '../../../services/appearance.service';
import {
  AnnualCategoryTrend,
  AnnualReport,
  CardUsageSummary,
  CategoryDeltaSummary,
  MonthlyCompareReport,
  MonthlyReport
} from '../../../models/accounting.model';
import { ChartModule, UIChart } from 'primeng/chart';
import { DatePickerModule } from 'primeng/datepicker';
import { ProgressBarModule } from 'primeng/progressbar';
import { FormsModule } from '@angular/forms';
import { CardModule } from 'primeng/card';
import { ButtonModule } from 'primeng/button';
import { TableModule } from 'primeng/table';
import { SelectModule } from 'primeng/select';
import { BtnComponent } from '../../ui/btn/btn';

@Component({
  selector: 'app-accounting-dashboard',
  imports: [CommonModule, ChartModule, DatePickerModule, FormsModule, CardModule, ProgressBarModule, ButtonModule, TableModule, SelectModule, BtnComponent],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AccountingDashboardComponent implements OnInit {
  private accountingService = inject(AccountingService);
  private appearance = inject(AppearanceService);
  private destroyRef = inject(DestroyRef);

  @ViewChild('expenseChart') expenseChart?: UIChart;

  selectedMonth = new Date();
  selectedAnnualYear = new Date().getFullYear();
  yearOptions = this.buildYearOptions();
  report = signal<MonthlyReport | null>(null);
  compareReport = signal<MonthlyCompareReport | null>(null);
  cardUsage = signal<CardUsageSummary[]>([]);
  cardSortBy = signal<'usage' | 'name'>('usage');
  annualReport = signal<AnnualReport | null>(null);
  annualLoading = signal(false);
  annualError = signal<string | null>(null);
  
  chartData: any;
  paymentChartData: any;
  annualTrendChartData: any;
  annualCategoryChartData: any;
  chartOptions = {
    animation: false,
    plugins: {
        legend: {
            display: false
        },
        tooltip: {
            enabled: true
        }
    },
    cutout: '60%', 
    layout: {
        padding: 0
    },
    maintainAspectRatio: false, // 關鍵：禁止鎖死比例
    responsive: true
  };
  annualLineChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index',
      intersect: false
    },
    plugins: {
      legend: {
        position: 'top',
        labels: {
          usePointStyle: true,
          boxWidth: 8
        }
      },
      tooltip: {
        enabled: true
      }
    },
    scales: {
      x: {
        grid: {
          display: false
        }
      },
      y: {
        beginAtZero: true
      }
    }
  };
  annualCategoryChartOptions = {
    ...this.annualLineChartOptions,
    plugins: {
      ...this.annualLineChartOptions.plugins,
      legend: {
        position: 'bottom',
        labels: {
          usePointStyle: true,
          boxWidth: 8
        }
      }
    }
  };

  ngOnInit() {
    this.loadReport();
    this.loadCardUsage();
    this.appearance.dark$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        const report = this.report();
        if (report) this.prepareChartData(report);
        this.expenseChart?.chart?.update?.('none');
      });
  }

  loadCardUsage() {
      this.accountingService.getCardUsage().subscribe(data => {
          this.sortAndSetCardUsage(data);
      });
  }

  toggleCardSort() {
      const current = this.cardSortBy();
      this.cardSortBy.set(current === 'usage' ? 'name' : 'usage');
      this.sortAndSetCardUsage(this.cardUsage());
  }

  private sortAndSetCardUsage(data: CardUsageSummary[]) {
    const sortedData = [...data].sort((a, b) => {
        if (this.cardSortBy() === 'usage') {
            if (b.usagePercentage !== a.usagePercentage) {
                return b.usagePercentage - a.usagePercentage;
            }
            return a.cardName.localeCompare(b.cardName);
        } else {
            return a.cardName.localeCompare(b.cardName);
        }
    });
    this.cardUsage.set(sortedData);
  }

  loadReport() {
    const year = this.selectedMonth.getFullYear();
    const month = this.selectedMonth.getMonth() + 1;

    this.accountingService.getMonthlyReport(year, month).subscribe(data => {
      this.report.set(data);
      this.prepareChartData(data);
      this.preparePaymentChartData(data);
    });

    this.accountingService.getMonthlyCompareReport(year, month).subscribe(data => {
      this.compareReport.set(data);
    });
  }

  loadAnnualReport() {
    this.annualLoading.set(true);
    this.annualError.set(null);

    this.accountingService.getAnnualReport(this.selectedAnnualYear).subscribe({
      next: data => {
        this.annualReport.set(data);
        this.prepareAnnualTrendChartData(data);
        this.prepareAnnualCategoryChartData(data);
        this.annualLoading.set(false);
      },
      error: error => {
        this.annualReport.set(null);
        this.annualTrendChartData = null;
        this.annualCategoryChartData = null;
        this.annualError.set(this.getAnnualErrorMessage(error));
        this.annualLoading.set(false);
      }
    });
  }

  getTopCategoryChanges(): CategoryDeltaSummary[] {
    const items = this.compareReport()?.categories ?? [];
    return items.filter(i => i.status !== 'flat').slice(0, 6);
  }

  hasAnnualActivity(): boolean {
    const report = this.annualReport();
    if (!report) {
      return false;
    }

    return report.summary.totalIncome > 0
      || report.summary.totalExpense > 0
      || report.categoryTrend.some(item => item.total > 0);
  }

  getAnnualTopCategories(): AnnualCategoryTrend[] {
    return (this.annualReport()?.categoryTrend ?? []).slice(0, 5);
  }

  formatAnnualMonth(month: string | null): string {
    if (!month) {
      return '無';
    }

    const monthNumber = Number(month.split('-')[1]);
    return Number.isFinite(monthNumber) ? `${monthNumber}月` : month;
  }

  formatCardCycle(card: CardUsageSummary): string {
    const startDate = new Date(card.billingCycleStart);
    const endDate = new Date(card.billingCycleEnd);

    if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime())) {
      return `${card.billingCycleStart} - ${card.billingCycleEnd}`;
    }

    return `${startDate.getMonth() + 1}/${startDate.getDate()} - ${endDate.getMonth() + 1}/${endDate.getDate()}`;
  }

  getCardUsageLabel(card: CardUsageSummary): string {
    return card.currentUsage < 0 ? '本期淨退款' : '本期已用';
  }

  getAbsoluteCardUsage(card: CardUsageSummary): number {
    return Math.abs(card.currentUsage);
  }

  getDeltaSign(delta: number): string {
    if (delta > 0) return '+';
    if (delta < 0) return '-';
    return '';
  }

  prepareChartData(report: MonthlyReport) {
    if (!report || !report.expenseBreakdown || report.expenseBreakdown.length === 0) {
        this.chartData = null;
        return;
    }

    const labels = report.expenseBreakdown.map(item => item.category);
    const data = report.expenseBreakdown.map(item => item.amount);
    
    // 擴充色票，確保分類多時不會沒顏色
    const modernPalette = [
      this.cssVar('--app-primary'),
      '#665efd',
      '#8e84fb',
      this.cssVar('--c-red'),
      '#f96bee',
      this.cssVar('--app-text-muted'),
      this.cssVar('--app-dividend'),
      this.cssVar('--app-success'),
    ];
    
    // 根據分類數量循環生成背景色
    const backgroundColors = labels.map((_, i) => modernPalette[i % modernPalette.length]);

    this.chartData = {
      labels: labels,
      datasets: [
        {
          data: data,
          backgroundColor: backgroundColors,
          borderWidth: 0
        }
      ]
    };
  }

  private cssVar(name: string): string {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  preparePaymentChartData(report: MonthlyReport) {
    if (!report || !report.paymentBreakdown || report.paymentBreakdown.length === 0) {
        this.paymentChartData = null;
        return;
    }

    const labels = report.paymentBreakdown.map(item => item.method);
    const data = report.paymentBreakdown.map(item => item.amount);

    this.paymentChartData = {
      labels: labels,
      datasets: [
        {
          data: data,
          backgroundColor: [
            '#36A2EB', '#FF6384', '#FFCE56', '#4BC0C0', '#9966FF', '#C9CBCF'
          ]
        }
      ]
    };
  }

  private prepareAnnualTrendChartData(report: AnnualReport) {
    if (!report.monthlyTrend?.length) {
      this.annualTrendChartData = null;
      return;
    }

    const labels = report.monthlyTrend.map(item => `${Number(item.month.split('-')[1])}月`);
    this.annualTrendChartData = {
      labels,
      datasets: [
        {
          label: '收入',
          data: report.monthlyTrend.map(item => item.totalIncome),
          borderColor: '#16a34a',
          backgroundColor: 'rgba(22, 163, 74, 0.16)',
          tension: 0.3,
          pointRadius: 3,
          pointHoverRadius: 5
        },
        {
          label: '支出',
          data: report.monthlyTrend.map(item => item.totalExpense),
          borderColor: '#ef4444',
          backgroundColor: 'rgba(239, 68, 68, 0.16)',
          tension: 0.3,
          pointRadius: 3,
          pointHoverRadius: 5
        },
        {
          label: '結餘',
          data: report.monthlyTrend.map(item => item.surplus),
          borderColor: '#0ea5e9',
          backgroundColor: 'rgba(14, 165, 233, 0.16)',
          tension: 0.3,
          pointRadius: 3,
          pointHoverRadius: 5
        }
      ]
    };
  }

  private prepareAnnualCategoryChartData(report: AnnualReport) {
    const topCategories = report.categoryTrend.slice(0, 5);
    if (!topCategories.length) {
      this.annualCategoryChartData = null;
      return;
    }

    const labels = report.monthlyTrend.map(item => `${Number(item.month.split('-')[1])}月`);
    const palette = ['#f97316', '#8b5cf6', '#14b8a6', '#eab308', '#ec4899'];

    this.annualCategoryChartData = {
      labels,
      datasets: topCategories.map((category, index) => ({
        label: category.category,
        data: category.monthlyAmounts,
        borderColor: palette[index % palette.length],
        backgroundColor: `${palette[index % palette.length]}22`,
        tension: 0.28,
        pointRadius: 2,
        pointHoverRadius: 4
      }))
    };
  }

  private getAnnualErrorMessage(error: { status?: number }) {
    if (error.status === 404) {
      return '年度報表 API 尚未提供，前端整合已完成並會在 endpoint 上線後直接啟用。';
    }

    if (error.status === 0) {
      return '目前無法連線到記帳服務，請稍後再試。';
    }

    return '年度趨勢資料載入失敗。';
  }

  private buildYearOptions() {
    const currentYear = new Date().getFullYear();
    return Array.from({ length: 10 }, (_, index) => {
      const year = currentYear - index;
      return {
        label: `${year}年`,
        value: year
      };
    });
  }
}
