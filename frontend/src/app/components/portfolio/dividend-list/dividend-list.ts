import { Component, OnInit, inject, signal, ViewChild, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PortfolioService } from '../../../services/portfolio.service';
import { Dividend } from '../../../models/portfolio.model';
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { InputNumberModule } from 'primeng/inputnumber';
import { DatePickerModule } from 'primeng/datepicker';
import { ConfirmationService, MessageService, MenuItem } from 'primeng/api';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { ToastModule } from 'primeng/toast';
import { MenuModule } from 'primeng/menu';
import { Menu } from 'primeng/menu';
import { ListItemComponent } from '../../shared/list-item/list-item';

@Component({
  selector: 'app-portfolio-dividends',
  imports: [CommonModule, TableModule, ButtonModule, DialogModule, FormsModule, InputTextModule, InputNumberModule, DatePickerModule, ConfirmDialogModule, ToastModule, MenuModule, ListItemComponent],
  providers: [ConfirmationService, MessageService],
  templateUrl: './dividend-list.html',
  styleUrl: './dividend-list.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class PortfolioDividendListComponent implements OnInit {
  private portfolioService = inject(PortfolioService);
  private confirmationService = inject(ConfirmationService);
  private messageService = inject(MessageService);

  @ViewChild('menu') menu!: Menu;
  menuItems: MenuItem[] = [];

  dividends = signal<Dividend[]>([]);
  symbolNames = signal<Record<string, string>>({});
  showDialog = signal<boolean>(false);
  isEdit = signal<boolean>(false);

  nameFor(symbol: string): string | null {
    return this.symbolNames()[symbol] ?? null;
  }
  
  newDividend: Partial<Dividend> = {
    amount: 0,
    fee: 0,
    tax: 0,
  };

  grossHint(): string | null {
    const qty = Number(this.newDividend.quantity_at_record_date ?? 0);
    const perShare = Number(this.newDividend.cash_dividend_per_share ?? 0);
    if (qty <= 0 || perShare <= 0) {
      return null;
    }
    const gross = qty * perShare;
    const fee = Number(this.newDividend.fee ?? 0);
    const tax = Number(this.newDividend.tax ?? 0);
    const fmt = (v: number) => `NT$${v.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
    if (tax > 0 || fee > 0) {
      const parts: string[] = [];
      if (fee > 0) parts.push(`手續費 −${fmt(fee)}`);
      if (tax > 0) parts.push(`補充保費 −${fmt(tax)}`);
      return `配息 ${fmt(gross)} (${parts.join(', ')})`;
    }
    return `配息 ${fmt(gross)}`;
  }

  ngOnInit() {
    this.loadDividends();
    this.portfolioService.getSymbolNames().subscribe(map => this.symbolNames.set(map));
  }

  showMenu(event: MouseEvent, dividend: Dividend) {
    this.menuItems = [
      { label: '編輯', icon: 'pi pi-pencil', command: () => this.editDividend(dividend) },
      { separator: true },
      { label: '刪除', icon: 'pi pi-trash', styleClass: 'text-danger', command: () => this.deleteDividend(dividend) }
    ];
    this.menu.toggle(event);
  }

  loadDividends() {
    this.portfolioService.getDividends().subscribe(data => {
      this.dividends.set(data);
    });
  }

  openNew() {
    this.isEdit.set(false);
    this.newDividend = { amount: 0, fee: 0, tax: 0 };
    this.showDialog.set(true);
  }

  editDividend(dividend: Dividend) {
    this.isEdit.set(true);
    this.newDividend = { ...dividend, ex_dividend_date: dividend.ex_dividend_date ? new Date(dividend.ex_dividend_date) : undefined };
    this.showDialog.set(true);
  }

  deleteDividend(dividend: Dividend) {
    this.confirmationService.confirm({
      message: `確定要刪除 ${dividend.symbol} 的這筆股利紀錄嗎？`,
      header: '確認刪除',
      icon: 'pi pi-exclamation-triangle',
      accept: () => {
        this.portfolioService.deleteDividend(dividend.id).subscribe(() => {
          this.messageService.add({ severity: 'success', summary: '成功', detail: '紀錄已刪除' });
          this.loadDividends();
        });
      }
    });
  }

  saveDividend() {
    if (this.isEdit() && this.newDividend.id) {
      this.portfolioService.updateDividend(this.newDividend.id, this.newDividend).subscribe(() => {
        this.showDialog.set(false);
        this.loadDividends();
        this.messageService.add({ severity: 'success', summary: '成功', detail: '紀錄已更新' });
      });
    } else {
      this.portfolioService.createDividend(this.newDividend).subscribe(() => {
        this.showDialog.set(false);
        this.loadDividends();
        this.messageService.add({ severity: 'success', summary: '成功', detail: '紀錄已新增' });
      });
    }
  }
}
